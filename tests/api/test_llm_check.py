import json
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.main import app
from app.api.routers.llm import get_llm_runner
from app.config import Settings
from app.models import AuditLog, Review
from tests.api.conftest import World

TOKEN = "sk-ant-oat01-SECRETsecretSECRETsecret-WXYZ"

PING_OK = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "structured_output": {"ok": True},
    "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 2, "output_tokens": 4},
    "modelUsage": {"claude-sonnet-5-5": {}},
}


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if "--version" in cmd:
            out = "2.1.309 (Claude Code)"
        elif "auth" in cmd:
            out = json.dumps({"loggedIn": True, "authMethod": "oauth_token"})
        else:
            out = json.dumps(PING_OK)
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def runner(settings: Settings) -> Iterator[FakeRunner]:
    settings.claude_code_oauth_token = SecretStr(TOKEN)
    fake = FakeRunner()
    app.dependency_overrides[get_llm_runner] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_llm_runner, None)


@pytest.mark.parametrize("who", ["lead", "manager", "an"])
def test_llm_check_is_admin_only(
    client: TestClient, world: World, as_user: Any, runner: FakeRunner, who: str
) -> None:
    response = client.post("/api/v1/admin/llm-check", headers=as_user(getattr(world, who)))
    assert response.status_code == 403
    assert runner.calls == []  # nothing is spent for a rejected caller


def test_llm_check_reports_status_and_usage_and_is_audited(
    client: TestClient, world: World, as_user: Any, runner: FakeRunner, db_session: Session
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            Review(
                target_type="mr",
                mr_id=world.mr.id,
                reviewed_sha=sha * 40,
                status="done",
                rubric_version="v1",
                cost_usd=Decimal("1.5"),
                started_at=started,
            )
            for sha, started in (("c", now), ("d", now), ("e", datetime(2020, 1, 15, tzinfo=UTC)))
        ]
    )
    db_session.flush()

    response = client.post("/api/v1/admin/llm-check", headers=as_user(world.admin))
    assert response.status_code == 200
    body = response.json()
    assert body["status"]["ok"] is True and body["status"]["model"] == "claude-sonnet-5-5"
    assert body["auth"]["token_hint"] == "sk-ant-oat01-…WXYZ"
    assert body["usage"]["reviews"] == 2
    assert body["usage"]["cost_usd"] == pytest.approx(3.0)
    assert body["usage"]["budget_pct"] == pytest.approx(1.5)  # of the default 200 USD
    assert TOKEN not in response.text and "SECRET" not in response.text

    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "llm.check")).one()
    assert audit.actor_user_id == world.admin.id
    assert audit.target == "llm:claude_code"
    assert audit.details is not None and audit.details["ok"] is True
    assert TOKEN not in json.dumps(audit.details)


def test_llm_check_needs_a_session(client: TestClient, runner: FakeRunner) -> None:
    assert client.post("/api/v1/admin/llm-check").status_code == 401
    assert runner.calls == []


def test_failed_llm_check_is_reported_and_still_audited(
    client: TestClient, world: World, as_user: Any, runner: FakeRunner, db_session: Session
) -> None:
    def missing_cli(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(cmd[0])

    app.dependency_overrides[get_llm_runner] = lambda: missing_cli
    response = client.post("/api/v1/admin/llm-check", headers=as_user(world.admin))
    assert response.status_code == 200
    assert response.json()["status"]["ok"] is False
    assert response.json()["status"]["error"] == "Claude Code CLI not found"
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "llm.check")).one()
    assert audit.details == {"ok": False}  # latency_ms is None and dropped by audit()
