from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from redis import Redis
from sqlalchemy.orm import Session

from app import health, monitoring
from app.models import Review
from app.monitoring import CodePulseCollector

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


class StubQueue:
    def __init__(self, name: str, connection: Any) -> None:
        self.name = name

    def __len__(self) -> int:
        return 7


def review(
    sha: str, status: str, seconds: int, cost: str = "0", error: str | None = None
) -> Review:
    return Review(
        target_type="commit",
        reviewed_sha=sha,
        status=status,
        error=error,
        created_at=NOW - timedelta(minutes=30, seconds=seconds),
        started_at=NOW - timedelta(minutes=30),
        finished_at=NOW - timedelta(minutes=30) + timedelta(seconds=0),
        cost_usd=Decimal(cost),
    )


def test_collector_reports_queue_latency_errors_and_cost(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(monitoring, "Queue", StubQueue)
    monkeypatch.setattr(health, "live_workers", lambda redis, now: ["w1", "w2"])
    db_session.add_all(
        [
            review("a", "done", 60, "0.10"),
            review("b", "done", 120, "0.20"),
            review("c", "failed", 0, "0.05", error="llm: claude failed after 4 attempts"),
            review("d", "failed", 0, error="gitlab: GET /x -> HTTP 404"),  # not an LLM error
        ]
    )
    db_session.flush()
    registry = CollectorRegistry()
    registry.register(
        CodePulseCollector(
            lambda: db_session.connection(), lambda: cast(Redis, None), lambda: 200.0, lambda: NOW
        )
    )
    text = generate_latest(registry).decode()
    assert 'codepulse_queue_jobs{queue="default"} 7.0' in text
    assert "codepulse_workers_alive 2.0" in text
    assert 'codepulse_review_latency_seconds{quantile="0.5"}' in text
    assert "codepulse_llm_error_ratio 0.3333333333333333" in text  # 1 LLM failure / 3
    assert "codepulse_llm_cost_usd_month 0.35" in text
    assert "codepulse_llm_budget_usd 200.0" in text


def test_metrics_endpoint_requires_the_token_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.api import main
    from app.config import get_settings

    monkeypatch.setattr(main, "generate_latest", lambda registry: b"ok 1\n")
    monkeypatch.setenv("METRICS_TOKEN", "scrape-secret")
    get_settings.cache_clear()
    try:
        client = TestClient(main.app)
        assert client.get("/metrics").status_code == 401
        good = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
        assert (good.status_code, good.text) == (200, "ok 1\n")
    finally:
        get_settings.cache_clear()
