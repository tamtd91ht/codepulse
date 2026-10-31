"""Stetho, the AI assistant (app/assistant, app/api/routers/assistant.py)."""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.main import app
from app.api.routers.assistant import get_assistant_recorder, get_assistant_streamer
from app.api.security import load_principal
from app.assistant.context import Screen, build_context
from app.config import Settings
from app.models import AuditLog, CommandRun, ReviewKnowledge
from app.workers.knowledge_jobs import month_spend_breakdown
from tests.api.conftest import World

TOKEN = "sk-ant-oat01-SECRETsecretSECRETsecret-WXYZ"


def stream_events(*texts: str, cost: float = 0.02, ok: bool = True) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"type": "system", "subtype": "init", "model": "claude-x"}]
    for text in texts:
        events.append(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": text},
                },
            }
        )
    events.append(
        {
            "type": "result",
            "subtype": "success" if ok else "error_during_execution",
            "is_error": not ok,
            "result": "".join(texts) if ok else "boom",
            "total_cost_usd": cost,
            "modelUsage": {"claude-x": {}},
        }
    )
    return events


class FakeStreamer:
    def __init__(self, events: list[dict[str, Any]], code: int = 0) -> None:
        self.events = events
        self.code = code
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, cmd: list[str], *, stdin: str, on_line: Callable[[str], None], **kw: Any
    ) -> tuple[int, str]:
        self.calls.append({"cmd": cmd, "stdin": stdin, **kw})
        for event in self.events:
            on_line(json.dumps(event) + "\n")
        return self.code, ""


@pytest.fixture
def connected(settings: Settings) -> Settings:
    settings.claude_code_oauth_token = SecretStr(TOKEN)
    return settings


@pytest.fixture
def streamer(db_session: Session) -> Iterator[FakeStreamer]:
    fake = FakeStreamer(stream_events("Xin ", "chào"))

    def record(user_id: int, details: dict[str, Any]) -> None:
        audit(db_session, user_id, "assistant.chat", "assistant", **details)
        db_session.flush()

    app.dependency_overrides[get_assistant_streamer] = lambda: fake
    app.dependency_overrides[get_assistant_recorder] = lambda: record
    yield fake
    app.dependency_overrides.pop(get_assistant_streamer, None)
    app.dependency_overrides.pop(get_assistant_recorder, None)


def sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.mark.parametrize("who", ["lead", "an", "outsider"])
def test_only_admins_and_managers(
    client: TestClient, world: World, as_user: Any, connected: Settings, who: str
) -> None:
    user = getattr(world, who)
    assert client.get("/api/v1/assistant/status", headers=as_user(user)).status_code == 403
    response = client.post("/api/v1/assistant/chat", json={"question": "hi"}, headers=as_user(user))
    assert response.status_code == 403


def test_status_without_claude(client: TestClient, world: World, as_user: Any) -> None:
    body = client.get("/api/v1/assistant/status", headers=as_user(world.manager)).json()
    assert body["name"] == "Stetho"
    assert body["available"] is False
    assert "Kết nối Claude" in body["reason"]
    assert [m["id"] for m in body["models"]] == ["sonnet", "opus", "haiku"]


def test_chat_streams_and_audits(
    client: TestClient,
    world: World,
    as_user: Any,
    connected: Settings,
    streamer: FakeStreamer,
    db_session: Session,
) -> None:
    assert client.get("/api/v1/assistant/status", headers=as_user(world.admin)).json()["available"]
    response = client.post(
        "/api/v1/assistant/chat",
        json={
            "question": "Tóm tắt review này",
            "history": [
                {"role": "user", "content": "trước"},
                {"role": "assistant", "content": "ok"},
            ],
            "screen": {"path": f"/reviews/{world.an_review.id}", "title": "Review"},
            "model": "haiku",
        },
        headers=as_user(world.admin),
    )
    assert response.status_code == 200
    events = sse(response.text)
    assert [e for e, _ in events] == ["delta", "delta", "done"]
    assert "".join(d["text"] for e, d in events if e == "delta") == "Xin chào"
    assert events[-1][1]["cost_usd"] == 0.02

    call = streamer.calls[0]
    cmd = call["cmd"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert TOKEN not in " ".join(cmd)
    assert call["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert "SQL injection" in call["stdin"]  # the finding of the review on screen
    assert "Người dùng: trước" in call["stdin"]

    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "assistant.chat")).one()
    assert row.actor_user_id == world.admin.id
    assert row.details is not None
    assert row.details["status"] == "ok"
    assert row.details["cost_usd"] == 0.02
    assert month_spend_breakdown(db_session, datetime.now(UTC))["assistant_usd"] == 0.02


def test_chat_error_is_an_event(
    client: TestClient,
    world: World,
    as_user: Any,
    connected: Settings,
    streamer: FakeStreamer,
) -> None:
    streamer.events = stream_events(ok=False)
    streamer.code = 1
    response = client.post(
        "/api/v1/assistant/chat", json={"question": "x"}, headers=as_user(world.manager)
    )
    events = sse(response.text)
    assert events[-1][0] == "error"
    assert "boom" in events[-1][1]["error"]


def test_over_budget_turns_stetho_off(
    client: TestClient,
    world: World,
    as_user: Any,
    connected: Settings,
    streamer: FakeStreamer,
    db_session: Session,
) -> None:
    connected.monthly_llm_budget_usd = 1
    audit(db_session, world.admin.id, "assistant.chat", "assistant", cost_usd=1.5)
    db_session.flush()
    status = client.get("/api/v1/assistant/status", headers=as_user(world.admin)).json()
    assert status["available"] is False
    assert "ngân sách" in status["reason"]
    response = client.post(
        "/api/v1/assistant/chat", json={"question": "x"}, headers=as_user(world.admin)
    )
    assert response.status_code == 429
    assert not streamer.calls


def test_chat_rejects_unknown_model(
    client: TestClient, world: World, as_user: Any, connected: Settings, streamer: FakeStreamer
) -> None:
    response = client.post(
        "/api/v1/assistant/chat",
        json={"question": "x", "model": "--dangerously-skip-permissions"},
        headers=as_user(world.admin),
    )
    assert response.status_code == 422
    assert not streamer.calls


def test_manager_context_keeps_people_private(
    db_session: Session, world: World, settings: Settings
) -> None:
    manager = load_principal(db_session, world.manager.id, settings)
    member = build_context(
        db_session, manager, settings, Screen(path=f"/members/{world.an.id}", title="An")
    )
    assert "không có quyền xem" in member
    assert "Số liệu cá nhân của" not in member

    review = build_context(
        db_session, manager, settings, Screen(path=f"/reviews/{world.an_review.id}")
    )
    assert "SQL injection" not in review

    admin = load_principal(db_session, world.admin.id, settings)
    shown = build_context(
        db_session, admin, settings, Screen(path=f"/reviews/{world.an_review.id}")
    )
    assert "SQL injection" in shown
    assert "app/a.py:3" in shown


def test_context_adds_the_team_the_question_names(
    db_session: Session, world: World, settings: Settings
) -> None:
    manager = load_principal(db_session, world.manager.id, settings)
    text = build_context(db_session, manager, settings, None, "Team backend tuần này thế nào?")
    assert "## Team Backend — báo cáo week" in text
    assert "bản manager: chỉ tổng hợp" in text
    # Mobile is not the manager's: never named, even when asked about.
    assert "Team Mobile" not in build_context(db_session, manager, settings, None, "mobile?")


def test_admin_llm_costs_show_every_budget_source_per_month(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    audit(db_session, world.manager.id, "assistant.chat", "assistant", cost_usd=0.25)
    audit(
        db_session,
        world.admin.id,
        "assistant.chat",
        "assistant",
        cost_usd=0.5,
        cost_estimated=True,
    )
    audit(db_session, world.admin.id, "assistant.chat", "assistant", status="cancelled")
    for version, cost in ((1, 0.4), (2, "odd")):  # a failed build's odd meta is skipped
        db_session.add(
            ReviewKnowledge(
                scope="project",
                project_id=world.project.id,
                version=version,
                schema_version=1,
                data={"meta": {"cost_usd": cost}},
                status="failed",
                built_by="manual",
            )
        )
    db_session.add(
        CommandRun(
            project_id=world.project.id,
            source="repo",
            command="report:weekly",
            status="succeeded",
            cost_usd=Decimal("1.1"),
        )
    )
    db_session.flush()
    body = client.get("/api/v1/admin/llm-costs", headers=as_user(world.admin)).json()
    month = body["months"][0]
    assert month["month"] == datetime.now(UTC).strftime("%Y-%m")
    assert month["assistant_usd"] == 0.75
    assert month["assistant_turns"] == 3
    assert month["assistant_estimated"] == 1
    assert month["reviews"] == 0
    assert (month["knowledge_usd"], month["knowledge_builds"]) == (0.4, 1)
    assert (month["commands_usd"], month["commands_runs"]) == (1.1, 1)
    assert month["total_usd"] == 2.25
    # The same figure the budget compares.
    assert month_spend_breakdown(db_session, datetime.now(UTC))["total_usd"] == 2.25
