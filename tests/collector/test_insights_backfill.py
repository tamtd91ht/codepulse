"""docs/specs/20 (9.12): repos synced before Phase 9 get Insights data from stored raw rows."""

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.insights.backfill import reconvert
from app.models import CommitFile, MergeRequest, RawGitlab
from tests.collector.test_commit_files import collected
from tests.collector.test_extract_convert import IGNORE
from tests.test_cli import cli

pytestmark = pytest.mark.integration


def files(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CommitFile)) or 0


def test_reconvert_fills_what_older_syncs_left_empty(db_session: Session) -> None:
    project = collected(db_session)
    # The stored raw rows of the sync are what the backfill reads again.
    first = db_session.scalars(select(RawGitlab).where(RawGitlab.project_id == project.id)).all()
    assert first
    reconvert(db_session, project, IGNORE)
    db_session.flush()
    expected = files(db_session)
    assert expected > 0
    # As if converted before Phase 9: no commit_files, no merge/squash shas.
    db_session.execute(delete(CommitFile))
    db_session.execute(update(MergeRequest).values(merge_commit_sha=None))
    for row in db_session.scalars(select(RawGitlab).where(RawGitlab.endpoint == "merge_request")):
        row.payload = {**row.payload, "merge_commit_sha": "9" * 40}
    db_session.flush()

    result = reconvert(db_session, project, IGNORE)
    db_session.flush()
    assert not result.skipped and result.runs >= 1
    assert files(db_session) == expected
    shas = set(db_session.scalars(select(MergeRequest.merge_commit_sha)))
    assert shas == {"9" * 40}
    again = reconvert(db_session, project, IGNORE)  # idempotent
    db_session.flush()
    assert again.runs == result.runs and files(db_session) == expected


def test_cli_insights_backfill(db_session: Session) -> None:
    project = collected(db_session)
    ok, out = cli(db_session, "insights", "backfill", "--project", str(project.id))
    assert ok and out.startswith("demo/p: ") and "resync" in out
    assert files(db_session) > 0
