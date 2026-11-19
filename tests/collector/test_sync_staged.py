"""The staged sync (app/collector/sync.py, 2026-11-19): the GitLab calls run with no transaction
open and the repo lock outlives the short transactions, while what is stored stays exactly what
the in-transaction sync stores."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.collector.sync import ADVISORY_LOCK_NAMESPACE, sync_project
from app.models import Commit, Connection, MergeRequest, Pipeline, RawGitlab
from tests.collector.fake_gitlab import BASE, mount
from tests.collector.test_collect import make_project

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
IGNORE = ["*.lock", "vendor/**"]


class Watching(GitLabClient):
    """Records, at each GitLab call, whether the session had a transaction open."""

    def __init__(self, session: Session, seen: list[bool], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._session, self._seen = session, seen

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self._seen.append(self._session.in_transaction())
        return super().request(method, url, **kwargs)


def counts(session: Session) -> tuple[int, int, int, int]:
    return (
        session.scalar(select(func.count()).select_from(RawGitlab)) or 0,
        session.scalar(select(func.count()).select_from(Commit)) or 0,
        session.scalar(select(func.count()).select_from(MergeRequest)) or 0,
        session.scalar(select(func.count()).select_from(Pipeline)) or 0,
    )


def test_staged_stores_what_the_in_transaction_sync_stores_with_no_transaction_on_gitlab(
    db_session: Session,
) -> None:
    project = make_project(db_session)
    seen: list[bool] = []

    def watched(connection: Connection) -> GitLabClient:
        return Watching(db_session, seen, connection.base_url, "t", max_per_second=1000)

    with respx.mock(assert_all_called=False) as router:
        mount(router)
        staged = sync_project(
            db_session,
            project,
            watched,
            backfill_days=90,
            ignore_paths=IGNORE,
            now=NOW,
            staged=True,
        )
    assert seen and not any(seen), "a GitLab call ran inside a transaction"
    assert not db_session.in_transaction()
    assert project.last_synced_at == NOW
    after_staged = counts(db_session)
    assert staged.counts is not None and staged.counts["domain.commits"] == 4
    assert after_staged == (18, 4, 2, after_staged[3])

    # The same run in one transaction stores the same entities (raw rows twice: two runs).
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        legacy = sync_project(
            db_session,
            project,
            lambda c: GitLabClient(c.base_url, "t", max_per_second=1000),
            backfill_days=90,
            ignore_paths=IGNORE,
            now=NOW + timedelta(minutes=30),
        )
    assert legacy.counts is not None
    assert {k: v for k, v in legacy.counts.items() if k.startswith("domain.")} == {
        k: v for k, v in staged.counts.items() if k.startswith("domain.")
    }
    assert counts(db_session)[1:] == after_staged[1:]


def test_staged_failure_stores_nothing_and_keeps_last_synced_at(db_session: Session) -> None:
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        router.get(f"{BASE}/api/v4/projects/1001/pipelines").respond(403)
        with pytest.raises(Exception, match="403"):
            sync_project(
                db_session,
                project,
                lambda c: GitLabClient(c.base_url, "t", max_per_second=1000),
                backfill_days=90,
                ignore_paths=IGNORE,
                now=NOW,
                staged=True,
            )
    db_session.refresh(project)
    assert project.last_synced_at is None
    assert counts(db_session)[:2] == (0, 0)


def test_staged_is_skipped_while_another_sync_holds_the_repo(
    db_session: Session, migrated_engine: Engine
) -> None:
    project = make_project(db_session)
    with migrated_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as other:
        other.execute(
            text("SELECT pg_advisory_lock(:ns, :pid)"),
            {"ns": ADVISORY_LOCK_NAMESPACE, "pid": project.id},
        )

        def no_gitlab(connection: Connection) -> GitLabClient:
            raise AssertionError("a busy repo must not reach GitLab")

        result = sync_project(
            db_session, project, no_gitlab, backfill_days=90, ignore_paths=IGNORE, staged=True
        )
        assert result.skipped is True
        other.execute(
            text("SELECT pg_advisory_unlock(:ns, :pid)"),
            {"ns": ADVISORY_LOCK_NAMESPACE, "pid": project.id},
        )
    # Released: the lock of a staged sync does not outlive it either.
    with migrated_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as probe:
        got = probe.scalar(
            text("SELECT pg_try_advisory_lock(:ns, :pid)"),
            {"ns": ADVISORY_LOCK_NAMESPACE, "pid": project.id},
        )
        assert got is True
        probe.execute(
            text("SELECT pg_advisory_unlock(:ns, :pid)"),
            {"ns": ADVISORY_LOCK_NAMESPACE, "pid": project.id},
        )


def test_staged_sync_with_the_timing_hooks_installed(
    db_session: Session, migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: on the production engine the timing hooks (app/dbtiming.py) see the repo
    lock's invalidate-then-close; the sync must still finish."""
    from sqlalchemy import event

    from app import dbtiming

    monkeypatch.setattr(dbtiming, "reporter", lambda payload: None)
    dbtiming.install(migrated_engine)
    try:
        project = make_project(db_session)
        with respx.mock(assert_all_called=False) as router:
            mount(router)
            result = sync_project(
                db_session,
                project,
                lambda c: GitLabClient(c.base_url, "t", max_per_second=1000),
                backfill_days=90,
                ignore_paths=IGNORE,
                now=NOW,
                staged=True,
            )
        assert result.skipped is False and project.last_synced_at == NOW
    finally:
        for name, fn in (
            ("begin", dbtiming._on_begin),
            ("commit", dbtiming._on_commit),
            ("rollback", dbtiming._on_rollback),
            ("before_cursor_execute", dbtiming._before),
            ("after_cursor_execute", dbtiming._after),
        ):
            event.remove(migrated_engine, name, fn)
