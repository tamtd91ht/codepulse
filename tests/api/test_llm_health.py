"""GET /admin/llm-health and "Kiểm tra ngay" (app/review/llm_recovery.py)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import LlmHealth
from app.review import llm_recovery
from tests.api.conftest import World

pytestmark = pytest.mark.integration


def test_only_who_edits_review_settings_sees_and_probes(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    assert client.get("/api/v1/admin/llm-health", headers=as_user(world.an)).status_code == 403
    seen = client.get("/api/v1/admin/llm-health", headers=as_user(world.admin))
    assert seen.status_code == 200
    assert seen.json()["status"] == "ok" and seen.json()["waiting_reviews"] == 0
    probe = "/api/v1/admin/llm-health/probe"
    assert client.post(probe, headers=as_user(world.an)).status_code == 403
    assert client.post(probe, headers=as_user(world.admin)).status_code == 409  # not down

    llm_recovery.mark_down(db_session, "llm: (HTTP 429)", 30)
    db_session.flush()
    later = datetime.now(UTC) + timedelta(minutes=29)
    asked = client.post(probe, headers=as_user(world.admin))
    assert asked.status_code == 200 and asked.json()["status"] == "down"
    row = db_session.get(LlmHealth, llm_recovery.HEALTH_ID)
    assert row is not None
    db_session.refresh(row)
    assert row.next_probe_at is not None and row.next_probe_at < later
