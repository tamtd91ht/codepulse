from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.collector.sync import enqueue_syncs, sync_project
from app.models import Commit, Connection, MergeRequest, RawGitlab
from tests.collector.fake_gitlab import BASE, mount
from tests.collector.test_collect import make_project

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
IGNORE = ["*.lock", "vendor/**"]


def make_client(connection: Connection) -> GitLabClient:
    return GitLabClient(connection.base_url, "t", max_per_second=1000)


def test_first_sync_backfills_then_increments_with_overlap(db_session: Session) -> None:
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        first = sync_project(
            db_session, project, make_client, backfill_days=90, ignore_paths=IGNORE, now=NOW
        )
        later = NOW + timedelta(minutes=30)
        second = sync_project(
            db_session, project, make_client, backfill_days=90, ignore_paths=IGNORE, now=later
        )
        commit_calls = [
            c for c in router.calls if c.request.url.path.endswith("/repository/commits")
        ]

    assert first.window_start == NOW - timedelta(days=90)
    assert second.window_start == NOW - timedelta(hours=1)
    assert project.last_synced_at == later
    assert commit_calls[1].request.url.params["since"] == (NOW - timedelta(hours=1)).isoformat()
    assert first.counts is not None and first.counts["domain.commits"] == 4
    # Both runs stored raw rows, but the domain layer holds each entity once.
    assert db_session.scalar(select(func.count()).select_from(RawGitlab)) == 2 * 18
    assert db_session.scalar(select(func.count()).select_from(Commit)) == 4
    assert db_session.scalar(select(func.count()).select_from(MergeRequest)) == 2


def test_failed_sync_does_not_advance_last_synced_at(db_session: Session) -> None:
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        router.get(f"{BASE}/api/v4/projects/1001/pipelines").respond(403)
        with pytest.raises(Exception, match="403"), db_session.begin_nested():
            sync_project(
                db_session, project, make_client, backfill_days=90, ignore_paths=IGNORE, now=NOW
            )
    db_session.refresh(project)
    assert project.last_synced_at is None


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((f, args, kwargs))


def test_enqueue_syncs_one_job_per_project(db_session: Session) -> None:
    project = make_project(db_session)
    queue = FakeQueue()
    assert enqueue_syncs(db_session, queue) == [project.id]
    ((func_name, args, kwargs),) = queue.jobs
    assert func_name == "app.collector.sync.sync_project_job"
    assert args == (project.id,)
    assert kwargs["job_id"] == f"sync-project-{project.id}"
