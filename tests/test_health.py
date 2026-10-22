import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from rq import Worker
from sqlalchemy import create_engine

from app import health
from app.api.main import app
from app.db import get_engine, get_redis


class StubRedis:
    def __init__(self, up: bool) -> None:
        self.up = up

    def ping(self) -> bool:
        if not self.up:
            raise ConnectionError("redis down")
        return True


@dataclass
class StubWorker:
    hostname: str
    last_heartbeat: datetime | None


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def workers(monkeypatch: pytest.MonkeyPatch) -> list[StubWorker]:
    registered: list[StubWorker] = []
    monkeypatch.setattr(Worker, "all", staticmethod(lambda connection: registered))
    return registered


def test_live_workers_ignores_stale_heartbeats(workers: list[StubWorker]) -> None:
    workers += [
        StubWorker("w1", NOW - timedelta(seconds=10)),
        StubWorker("w2", NOW - timedelta(minutes=10)),
        StubWorker("w3", None),
    ]
    live = health.live_workers(StubRedis(True), now=NOW)  # type: ignore[arg-type]
    assert [w.hostname for w in live] == ["w1"]


def test_local_worker_alive_matches_hostname(workers: list[StubWorker]) -> None:
    workers.append(StubWorker("worker-1", datetime.now(UTC)))
    assert health.local_worker_alive(StubRedis(True), hostname="worker-1")  # type: ignore[arg-type]
    assert not health.local_worker_alive(StubRedis(True), hostname="other")  # type: ignore[arg-type]


def _client(engine: Any, redis: StubRedis) -> TestClient:
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_redis] = lambda: redis
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def test_healthz_degraded_when_dependencies_down(workers: list[StubWorker]) -> None:
    dead_db = create_engine(
        "postgresql+psycopg://u:p@127.0.0.1:1/x", connect_args={"connect_timeout": 1}
    )
    response = _client(dead_db, StubRedis(False)).get("/healthz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"database": False, "redis": False, "workers": 0},
    }


@pytest.mark.integration
def test_healthz_ok_with_database(migrated_engine: Any, workers: list[StubWorker]) -> None:
    workers.append(StubWorker("w1", datetime.now(UTC)))
    response = _client(migrated_engine, StubRedis(True)).get("/healthz")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": True, "redis": True, "workers": 1}


def test_request_id_is_echoed_or_generated(workers: list[StubWorker]) -> None:
    client = _client(create_engine("sqlite://"), StubRedis(True))
    assert client.get("/healthz", headers={"X-Request-ID": "abc"}).headers["X-Request-ID"] == "abc"
    assert len(client.get("/healthz").headers["X-Request-ID"]) == 32


def test_access_log_carries_request_id(
    workers: list[StubWorker], caplog: pytest.LogCaptureFixture
) -> None:
    from app.logging import request_id_var

    caplog.set_level(logging.INFO, logger="codepulse.access")
    seen: list[str | None] = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(request_id_var.get())  # type: ignore[method-assign]
    logging.getLogger("codepulse.access").addHandler(handler)
    try:
        _client(create_engine("sqlite://"), StubRedis(True)).get(
            "/healthz", headers={"X-Request-ID": "trace-1"}
        )
    finally:
        logging.getLogger("codepulse.access").removeHandler(handler)
    assert seen == ["trace-1"]
