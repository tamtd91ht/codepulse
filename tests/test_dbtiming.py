"""Every transaction timed where it runs (app/dbtiming.py); a slow one is reported once, and one
the minute watch already rang for gets a follow-up instead (app/dbwatch.py `report_slow`)."""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app import dbtiming
from app.config import Settings
from app.dbwatch import KIND, Seen, report_slow
from app.models import User, UserNotification

pytestmark = pytest.mark.integration


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    got: list[dict[str, Any]] = []
    monkeypatch.setattr(dbtiming, "reporter", got.append)
    monkeypatch.setattr(dbtiming.threshold, "value", 0.5)
    yield got


def test_only_a_slow_transaction_is_reported_with_its_slowest_statement(
    postgres_url: str, reports: list[dict[str, Any]]
) -> None:
    engine = create_engine(postgres_url)
    dbtiming.install(engine)
    try:
        with engine.begin() as conn:  # fast: nothing
            conn.execute(text("SELECT 1"))
        assert reports == []
        with engine.begin() as conn:
            conn.execute(text("SELECT 1"))
            conn.execute(text("SELECT pg_sleep(0.7)"))
        ((slow,),) = [reports]
        assert slow["seconds"] >= 0.5 and slow["how"] == "commit"
        assert slow["statements"] == 2 and "pg_sleep" in slow["slowest_sql"]
        assert slow["slowest_s"] >= 0.5 and isinstance(slow["pid"], int)
        assert slow["flow"].startswith("cp-")
        # A rollback is timed too.
        conn = engine.connect()
        conn.begin()
        conn.execute(text("SELECT pg_sleep(0.6)"))
        conn.rollback()
        conn.close()
        assert reports[-1]["how"] == "rollback"
        # An autocommit connection (the sync's repo lock) holds no transaction: not timed.
        count = len(reports)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as auto:
            auto.execute(text("SELECT pg_sleep(0.6)"))
            auto.execute(text("SELECT 1"))
        assert len(reports) == count
    finally:
        engine.dispose()


def settings() -> Settings:
    return Settings(
        session_secret=SecretStr("s" * 48), admin_emails=["admin@x.vn"], long_tx_alert_s=60
    )


def payload(**kw: Any) -> dict[str, Any]:
    return {
        "seconds": 84.2,
        "started": 1_000.0,
        "ended": 1_084.2,
        "how": "commit",
        "flow": "cp-worker sync.sync_project_job #12",
        "where": "app/collector/sync.py:312 _sync_staged",
        "pid": 4242,
        "statements": 14230,
        "slowest_s": 61.0,
        "slowest_sql": "INSERT INTO raw_gitlab ...",
        **kw,
    }


def test_an_ended_slow_transaction_rings_once_per_place_then_follows_up(
    db_session: Session,
) -> None:
    admin = User(full_name="Admin", primary_email="admin@x.vn")
    db_session.add(admin)
    db_session.flush()
    seen = Seen(None)

    assert report_slow(db_session, settings(), seen, payload()) is True
    # The same flow at the same place: cooldown.
    assert report_slow(db_session, settings(), seen, payload(pid=4343)) is False
    # The minute watch rang for pid 99 that started at 2000: its end is a follow-up.
    seen.mark("live:99", 3600, time.time(), "2001.5")
    assert report_slow(db_session, settings(), seen, payload(pid=99, started=2000.0)) is True
    rows = db_session.scalars(
        select(UserNotification).where(UserNotification.kind == KIND).order_by(UserNotification.id)
    ).all()
    assert [r.user_id for r in rows] == [admin.id, admin.id]
    assert "sync.sync_project_job #12" in rows[0].title and "sync.py:312" in (rows[0].body or "")
    assert rows[1].title.startswith("Transaction DB đã xong sau")
    assert not seen.has("live:99", time.time())
    history = seen.history(time.time())
    assert [h["source"] for h in history] == ["ended", "ended"]
    assert history[0]["where"] == "app/collector/sync.py:312 _sync_staged"


def test_the_hooks_never_fail_an_invalidated_or_killed_connection(
    postgres_url: str, reports: list[dict[str, Any]]
) -> None:
    """Review finding: the sync's repo lock invalidates then closes its connection, and a
    backend can be killed mid-transaction: neither may raise from the hooks."""
    engine = create_engine(postgres_url)
    dbtiming.install(engine)
    try:
        lock = engine.connect()
        lock.execute(text("SELECT 1"))
        lock.invalidate()
        lock.close()  # raised PendingRollbackError before

        victim = engine.connect()
        victim.begin()
        pid = victim.scalar(text("SELECT pg_backend_pid()"))
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as killer:
            killer.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        with pytest.raises(Exception) as caught:
            victim.execute(text("SELECT 1"))
        assert "PendingRollback" not in type(caught.value).__name__
        victim.rollback()
        victim.close()
    finally:
        engine.dispose()


def test_a_report_after_the_admin_turned_the_watch_off_does_not_ring(
    db_session: Session,
) -> None:
    db_session.add(User(full_name="Admin", primary_email="admin@x.vn"))
    db_session.flush()
    off = Settings(
        session_secret=SecretStr("s" * 48), admin_emails=["admin@x.vn"], long_tx_alert_s=0
    )
    assert report_slow(db_session, off, Seen(None), payload()) is False
