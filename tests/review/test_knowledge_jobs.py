"""The knowledge build's storage and its first-review gate (docs/specs/19 §3.4, §6) on an
in-memory SQLite: version assignment under a pre-existing row, one active row per scope, the
failed row of a build that paid, the month's spend, and when a review may build."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import BigInteger, create_engine, event, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.models import ReviewKnowledge
from app.models.base import Base
from app.review.knowledge import Knowledge
from app.review.providers.base import ReviewResult
from app.workers import knowledge_jobs
from app.workers.knowledge_jobs import (
    FIRST_BUILD_KEY,
    FIRST_BUILD_TTL_S,
    CostLedger,
    first_build_allowed,
    job_id_for,
    month_spend_usd,
    record_failure,
    store_knowledge,
)


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_: Any, compiler: Any, **kw: Any) -> str:
    """SQLite only auto-numbers an INTEGER primary key."""
    return "INTEGER"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    # pysqlite's own transaction handling breaks SAVEPOINT; drive BEGIN by hand instead.
    @event.listens_for(engine, "connect")
    def _no_autobegin(dbapi_connection: Any, record: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    # No reload after commit: a read of a stored row's attribute must not open a transaction
    # (the job keeps none open between its steps).
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def knowledge(statement: str = "Mọi truy vấn phải lọc theo tenant_id") -> Knowledge:
    return Knowledge.model_validate(
        {
            "invariants": [{"id": "inv-1", "statement": statement, "severity": "high"}],
            "meta": {"cost_usd": 0.3},
        }
    )


def rows(session: Session, project_id: int = 7) -> list[tuple[int, str]]:
    with session.begin():  # the job keeps no transaction open between its steps
        return [
            (r.version, r.status)
            for r in session.scalars(
                select(ReviewKnowledge)
                .where(ReviewKnowledge.scope == "project", ReviewKnowledge.project_id == project_id)
                .order_by(ReviewKnowledge.version)
            )
        ]


def allowed(session: Session, **kw: Any) -> bool:
    with session.begin():
        return first_build_allowed(session, 7, **kw)


def store(session: Session, status: str = "active", built_by: str = "manual") -> ReviewKnowledge:
    with session.begin():
        return store_knowledge(
            session,
            scope="project",
            project_id=7,
            business_project_id=None,
            knowledge=knowledge(),
            sources={"claude_tree_sha": "1" * 40},
            built_by=built_by,
            status=status,
        )


# -- versions and the one active row ---------------------------------------------------------------


def test_store_versions_after_the_newest_row_and_supersedes_the_active_one(
    session: Session,
) -> None:
    first = store(session)
    assert (first.version, first.status) == (1, "active")
    second = store(session, built_by="auto")
    assert second.version == 2 and second.id != first.id
    assert rows(session) == [(1, "superseded"), (2, "active")]

    # A failed build takes the next version and leaves the active row alone.
    with session.begin():
        failed = record_failure(
            session,
            scope="project",
            project_id=7,
            business_project_id=None,
            cost_usd=0.1234,
            error="LLMError: boom",
            built_by="first_review",
        )
    assert failed.version == 3 and failed.data["meta"] == {
        "cost_usd": 0.1234,
        "error": "LLMError: boom",
        "built_at": failed.data["meta"]["built_at"],
        "notes": ["build failed: LLMError: boom"],
    }
    assert rows(session) == [(1, "superseded"), (2, "active"), (3, "failed")]
    # Another scope's numbering is its own.
    with session.begin():
        shared = store_knowledge(
            session,
            scope="business_project",
            project_id=None,
            business_project_id=7,
            knowledge=knowledge(),
            sources={},
            built_by="manual",
        )
    assert shared.version == 1
    assert store(session).version == 4
    assert rows(session) == [(1, "superseded"), (2, "superseded"), (3, "failed"), (4, "active")]


def test_store_keeps_the_winner_when_a_concurrent_build_took_the_version(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner = store(session)
    # The other worker committed version 1 between our read and our insert.
    monkeypatch.setattr(knowledge_jobs, "_next_version", lambda *a, **k: 1)
    with session.begin():
        got = store_knowledge(
            session,
            scope="project",
            project_id=7,
            business_project_id=None,
            knowledge=knowledge("other"),
            sources={},
            built_by="auto",
        )
    assert got.id == winner.id and got.status == "active"
    assert rows(session) == [(1, "active")]
    with session.begin():
        kept = session.get(ReviewKnowledge, winner.id)
        assert kept is not None and kept.data["invariants"][0]["statement"].startswith("Mọi")


# -- money -----------------------------------------------------------------------------------------


def test_month_spend_counts_every_knowledge_row_including_failed(session: Session) -> None:
    store(session)  # 0.3, later superseded
    store(session)  # 0.3, active
    with session.begin():
        record_failure(
            session,
            scope="project",
            project_id=7,
            business_project_id=None,
            cost_usd=0.5,
            error="x",
            built_by="auto",
        )
    with session.begin():
        assert month_spend_usd(session) == pytest.approx(1.1)
        old = session.get(ReviewKnowledge, 1)
        assert old is not None
        old.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    with session.begin():
        assert month_spend_usd(session) == pytest.approx(0.8)


def test_cost_ledger_adds_each_answered_call() -> None:
    ledger = CostLedger()
    ledger.add(ReviewResult(output=None, model="m", cost_usd=0.25))
    ledger.add(ReviewResult(output="junk", model="m", cost_usd=0.125))
    assert ledger.total == 0.375 and ledger.calls == [0.25, 0.125]


# -- the first-review gate ---------------------------------------------------------------------


class FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, int | None] = {}

    def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.keys:
            return False
        self.keys[key] = ex
        return True


class FakeJob:
    def __init__(self, status: str) -> None:
        self.status = status

    def get_status(self, refresh: bool = False) -> str:
        return self.status


class FakeQueue:
    def __init__(self, status: str | None) -> None:
        self.status = status

    def fetch_job(self, job_id: str) -> FakeJob | None:
        assert job_id == job_id_for(7)
        return FakeJob(self.status) if self.status else None


def test_first_build_waits_an_hour_after_any_row_failed_included(session: Session) -> None:
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert allowed(session, redis=None, queue=None, now=now) is True
    with session.begin():
        failed = record_failure(
            session,
            scope="project",
            project_id=7,
            business_project_id=None,
            cost_usd=0.2,
            error="LLMError",
            built_by="first_review",
        )
        failed.created_at = now - timedelta(minutes=10)
    assert allowed(session, redis=None, queue=None, now=now) is False
    with session.begin():
        failed.created_at = now - timedelta(hours=1, minutes=1)
    assert allowed(session, redis=None, queue=None, now=now) is True


def test_first_build_yields_to_a_queued_job_and_to_the_hour_key(session: Session) -> None:
    assert allowed(session, redis=None, queue=FakeQueue("queued")) is False
    assert allowed(session, redis=None, queue=FakeQueue("started")) is False
    assert allowed(session, redis=None, queue=FakeQueue("finished")) is True
    redis = FakeRedis()
    assert allowed(session, redis=redis, queue=FakeQueue(None)) is True
    assert redis.keys == {FIRST_BUILD_KEY.format(project_id=7): FIRST_BUILD_TTL_S}
    # The key is the claim: a second review within the hour does not build.
    assert allowed(session, redis=redis, queue=FakeQueue(None)) is False
