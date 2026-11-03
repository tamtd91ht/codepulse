"""docs/specs/20 §5.1 and §15: commit_files from the converter and from the backfill."""

import pytest
import respx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.collector.gitlab import collect as ep
from app.collector.gitlab.client import GitLabClient
from app.collector.gitlab.collect import GitLabCollector
from app.collector.gitlab.convert import Converter, backfill_commit_files, raw_rows_query
from app.collector.gitlab.extract import GlFileStat, extract
from app.models import CommitFile, Project, RawGitlab
from tests.collector.fake_gitlab import BASE, mount
from tests.collector.test_collect import make_project
from tests.collector.test_extract_convert import IGNORE, B
from tests.test_cli import cli

pytestmark = pytest.mark.integration


def files_of(session: Session, sha: str) -> dict[str, tuple[int, int]]:
    return {
        f.path: (f.additions, f.deletions)
        for f in session.scalars(select(CommitFile).where(CommitFile.sha == sha))
    }


def total(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CommitFile)) or 0


def collected(session: Session) -> Project:
    project = make_project(session)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        GitLabCollector(client).collect(session, project, None)
    return project


def test_converter_stores_paths_after_ignore_and_is_idempotent(db_session: Session) -> None:
    project = collected(db_session)
    counts = []
    for _ in range(2):
        rows = db_session.scalars(raw_rows_query(project)).all()
        Converter(db_session, project, IGNORE).run(extract(rows))
        db_session.flush()
        counts.append(total(db_session))
    assert counts[0] == counts[1] > 0
    vendored = files_of(db_session, B)
    assert len(vendored) == 1  # vendor/chart.min.js dropped, like commits.files_changed
    assert not any(p.startswith("vendor/") for p in vendored)


def test_a_later_diff_replaces_the_paths(db_session: Session) -> None:
    project = collected(db_session)
    rows = db_session.scalars(raw_rows_query(project)).all()
    converter = Converter(db_session, project, IGNORE)
    converter.run(extract(rows))
    converter.commit_files(
        B,
        [
            GlFileStat("src/a.py", 3, 1),
            GlFileStat("src/a.py", 2, 0),  # listed twice: summed
            GlFileStat("yarn.lock", 100, 0),  # ignored
        ],
    )
    db_session.flush()
    assert files_of(db_session, B) == {"src/a.py": (5, 1)}
    converter.commit_files(B, [GlFileStat("vendor/x.js", 1, 1)])
    db_session.flush()
    assert files_of(db_session, B) == {}


def test_backfill_rebuilds_from_raw_and_skips_unknown_commits(db_session: Session) -> None:
    project = collected(db_session)
    rows = db_session.scalars(raw_rows_query(project)).all()
    Converter(db_session, project, IGNORE).run(extract(rows))
    db_session.flush()
    expected = total(db_session)
    db_session.execute(delete(CommitFile))
    db_session.add(
        RawGitlab(
            project_id=project.id,
            endpoint=ep.EP_COMMIT_DIFF,
            params={"sha": "f" * 40},
            payload=[{"new_path": "x.py", "additions": 1, "deletions": 0}],
        )
    )
    db_session.flush()
    first = backfill_commit_files(db_session, project, IGNORE)
    assert total(db_session) == expected
    assert first.skipped == 1 and first.commits > 0
    again = backfill_commit_files(db_session, project, IGNORE)
    assert (again.commits, total(db_session)) == (first.commits, expected)


def test_cli_backfill_files(db_session: Session) -> None:
    project = collected(db_session)
    rows = db_session.scalars(raw_rows_query(project)).all()
    Converter(db_session, project, IGNORE).run(extract(rows))
    db_session.execute(delete(CommitFile))
    db_session.flush()
    ok, out = cli(db_session, "insights", "backfill-files", "--project", str(project.id))
    assert ok and out.startswith("demo/p: ") and "commits" in out
    assert total(db_session) > 0


def test_removing_a_repo_removes_its_commit_files(db_session: Session) -> None:
    from zoneinfo import ZoneInfo

    from app.collector.removal import remove_projects

    project = collected(db_session)
    rows = db_session.scalars(raw_rows_query(project)).all()
    Converter(db_session, project, IGNORE).run(extract(rows))
    db_session.flush()
    stored = total(db_session)
    result = remove_projects(db_session, [project.id], ZoneInfo("Asia/Ho_Chi_Minh"))
    assert result.rows["commit_files"] == stored and total(db_session) == 0
