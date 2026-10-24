import json
from datetime import UTC, datetime, timedelta

import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient, GitLabError
from app.collector.gitlab.collect import GitLabCollector, window_start
from app.collector.gitlab.diffstat import count_lines, file_stat, is_ignored
from app.collector.sync import fetch_commit
from app.models import Commit, Connection, Project, RawGitlab
from tests.collector.fake_gitlab import API, BASE, FIXTURES, mount

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def test_count_lines_ignores_file_headers() -> None:
    assert count_lines("--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n-a\n+b\n+c\n context\n") == (2, 1)


def test_file_stat_drops_diff_text() -> None:
    stat = file_stat({"new_path": "a.py", "old_path": "a.py", "diff": "@@\n+secret = 1\n"})
    assert "diff" not in stat
    assert "secret" not in json.dumps(stat)
    assert (stat["additions"], stat["deletions"]) == (1, 0)


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("yarn.lock", True),
        ("web/package.lock", True),
        ("dist/app.js", True),
        ("vendor/chart.min.js", True),
        ("generated/api.py", True),
        ("app/generated/api.py", True),
        ("app/review/parser.py", False),
    ],
)
def test_ignore_paths(path: str, ignored: bool) -> None:
    patterns = ["*.lock", "dist/**", "vendor/**", "*.min.js", "**/generated/**"]
    assert is_ignored(path, patterns) is ignored


def test_window_backfills_then_overlaps_one_hour() -> None:
    project = Project(external_id=1, path_with_namespace="g/p")
    assert window_start(project, NOW, backfill_days=90) == NOW - timedelta(days=90)
    project.last_synced_at = NOW - timedelta(minutes=30)
    assert window_start(project, NOW, backfill_days=90) == NOW - timedelta(minutes=90)


def make_project(session: Session) -> Project:
    connection = Connection(name="test", base_url=BASE, token_encrypted="x")
    session.add(connection)
    session.flush()
    project = Project(connection_id=connection.id, external_id=1001, path_with_namespace="demo/p")
    session.add(project)
    session.flush()
    return project


@pytest.mark.integration
def test_collect_stores_raw_without_diff_text(db_session: Session) -> None:
    project = make_project(db_session)
    with (
        respx.mock(assert_all_called=False) as router,
        GitLabClient(BASE, "t", max_per_second=1000) as client,
    ):
        mount(router)
        result = GitLabCollector(client, clock=lambda: NOW).collect(db_session, project, None)

    assert result.counts == {
        "project": 1,
        "commit": 4,
        "commit_diff": 3,  # the merge commit is skipped
        "merge_request": 2,
        "mr_commits": 2,
        "mr_notes": 2,
        "mr_diffs": 2,
        "pipeline": 2,
    }
    rows = db_session.scalars(select(RawGitlab).where(RawGitlab.project_id == project.id)).all()
    assert all(r.fetched_at == NOW for r in rows)
    assert all('"diff"' not in json.dumps(r.payload) for r in rows)
    pipeline = next(r for r in rows if r.endpoint == "pipeline" and r.params == {"id": 7001})
    assert pipeline.payload["duration"] == 300
    assert pipeline.payload["status"] == "success"


@pytest.mark.integration
def test_one_item_gitlab_cannot_serve_does_not_fail_the_sync(db_session: Session) -> None:
    project = make_project(db_session)
    with (
        respx.mock(assert_all_called=False) as router,
        GitLabClient(BASE, "t", max_per_second=1000, max_retries=0) as client,
    ):
        # Registered first, so it wins over the generic routes: GitLab 500s on one MR's diffs.
        router.get(f"{API}/projects/1001/merge_requests/1/diffs").respond(500)
        router.get(f"{API}/projects/1001/pipelines/7002").respond(404)
        mount(router)
        result = GitLabCollector(client, clock=lambda: NOW).collect(db_session, project, None)
    assert result.counts["merge_request"] == 2 and result.counts["mr_diffs"] == 1
    assert result.counts["skipped.mr_diffs"] == 1 and result.counts["skipped.pipeline"] == 1
    assert result.counts["pipeline"] == 2  # the list row is kept without its detail


@pytest.mark.integration
def test_auth_errors_still_fail_the_sync(db_session: Session) -> None:
    project = make_project(db_session)
    with (
        respx.mock(assert_all_called=False) as router,
        GitLabClient(BASE, "t", max_per_second=1000, max_retries=0) as client,
    ):
        router.get(f"{API}/projects/1001/merge_requests/1/diffs").respond(401)
        mount(router)
        with pytest.raises(GitLabError):
            GitLabCollector(client, clock=lambda: NOW).collect(db_session, project, None)


@pytest.mark.integration
def test_fetch_commit_brings_one_commit_in_without_a_sync(db_session: Session) -> None:
    project = make_project(db_session)
    commits = json.loads((FIXTURES / "commits.json").read_text(encoding="utf-8"))
    sha = commits[0]["id"]
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{API}/projects/1001/repository/commits/{sha}").respond(json=commits[0])
        router.get(url__regex=rf"{API}/projects/1001/repository/commits/f+(\?.*)?$").respond(404)
        mount(router)

        def make_client(connection: Connection) -> GitLabClient:
            return GitLabClient(BASE, "t", max_per_second=1000, max_retries=0)

        assert fetch_commit(db_session, project, make_client, sha, ignore_paths=[]) is True
        assert fetch_commit(db_session, project, make_client, "f" * 40, ignore_paths=[]) is False
        listed = [str(c.request.url.path) for c in router.calls]
    commit = db_session.get(Commit, sha)
    assert commit is not None and commit.project_id == project.id
    assert project.last_synced_at is None  # not a sync
    assert not any(path.endswith("/merge_requests") for path in listed)
