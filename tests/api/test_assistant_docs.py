"""Teaching Stetho (docs/specs/28): documents, permissions, and the chat reading them."""

import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.main import app
from app.assistant import importers, knowledge
from app.config import Settings
from app.models import AssistantDoc, PermissionSet, PermissionSetItem, UserPermissionSet
from tests.api.conftest import World
from tests.api.test_assistant import (  # noqa: F401 - connected, streamer: fixtures
    FakeStreamer,
    connected,
    sse,
    stream_events,
    streamer,
)
from tests.test_assistant_knowledge import docx

API = "/api/v1/assistant/docs"


@pytest.fixture(autouse=True)
def _fresh_builtin_flag(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test database starts without the built-ins; their sync (a session of its own in
    the app) runs on the test's session."""
    from contextlib import contextmanager

    import app.db

    @contextmanager
    def same() -> Any:
        yield db_session

    monkeypatch.setattr(app.db, "get_sessionmaker", lambda: same)
    knowledge._synced = False


def grant_trainer(session: Session, user_id: int) -> None:
    """A permission set holding assistant.train, given to one person (what an admin does)."""
    held = PermissionSet(name=f"Dạy Stetho {user_id}", description="")
    session.add(held)
    session.flush()
    session.add(PermissionSetItem(set_id=held.id, permission="assistant.train", scope="flag"))
    session.add(UserPermissionSet(user_id=user_id, set_id=held.id))
    session.flush()


def test_only_admins_by_default_then_whoever_is_granted(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    for who in (world.lead, world.manager, world.an):
        assert client.get(API, headers=as_user(who)).status_code == 403
        assert (
            client.post(API, json={"title": "x", "body": "y"}, headers=as_user(who)).status_code
            == 403
        )
    body = client.get(API, headers=as_user(world.admin)).json()
    assert {d["title"] for d in body["docs"] if d["builtin"]}  # the built-ins are there
    assert body["web"] is False and ".docx" in body["file_kinds"]
    grant_trainer(db_session, world.lead.id)
    assert client.get(API, headers=as_user(world.lead)).status_code == 200


def test_files_links_and_saved_answers(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = as_user(world.admin)
    word = docx(("Heading1", "Quy trình hotfix"), (None, "Hotfix cần lead duyệt trong 2 giờ."))
    res = client.post(
        f"{API}/upload", params={"filename": "Quy trình.docx"}, content=word, headers=admin
    )
    assert res.status_code == 201, res.text
    assert res.json()["source"] == "file" and res.json()["title"] == "Quy trình"

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Môi trường", "URL"])
    sheet.append(["staging", "https://staging.example"])
    buf = io.BytesIO()
    book.save(buf)
    res = client.post(
        f"{API}/upload",
        params={"filename": "moi-truong.xlsx", "audience": "trainers"},
        content=buf.getvalue(),
        headers=admin,
    )
    assert res.status_code == 201 and res.json()["audience"] == "trainers"

    bad = client.post(f"{API}/upload", params={"filename": "a.pdf"}, content=b"%PDF", headers=admin)
    assert bad.status_code == 422

    monkeypatch.setattr(
        importers,
        "from_url",
        lambda url, hosts=(): ("Wiki release", "# Release\n\nRelease vào thứ ba."),
    )
    res = client.post(f"{API}/url", json={"url": "https://wiki.example/release"}, headers=admin)
    assert res.status_code == 201 and res.json()["source_name"] == "https://wiki.example/release"

    res = client.post(
        API,
        json={
            "title": "Ai duyệt hotfix?",
            "body": "Lead của team duyệt, trong 2 giờ.",
            "source": "qa",
            "question": "ai duyệt hotfix",
        },
        headers=admin,
    )
    assert res.status_code == 201 and res.json()["question"] == "ai duyệt hotfix"
    # A saved answer came from the trainer's own view of the numbers: trainers only by default.
    assert res.json()["audience"] == "trainers"

    found = client.post(
        f"{API}/search", json={"question": "ai duyệt hotfix?"}, headers=admin
    ).json()
    assert {h["title"] for h in found["hits"]} >= {"Ai duyệt hotfix?", "Quy trình"}


def test_built_ins_can_only_be_switched_off(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    builtin = next(d for d in client.get(API, headers=admin).json()["docs"] if d["builtin"])
    path = f"{API}/{builtin['id']}"
    assert client.patch(path, json={"title": "khác"}, headers=admin).status_code == 409
    assert client.delete(path, headers=admin).status_code == 409
    assert client.patch(path, json={"enabled": False}, headers=admin).json()["enabled"] is False


def test_the_chat_reads_the_documents_and_web_is_a_switch(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    connected: Settings,  # noqa: F811
    streamer: FakeStreamer,  # noqa: F811
) -> None:
    admin = as_user(world.admin)
    client.post(
        API,
        json={"title": "Quy trình hotfix", "body": "Hotfix cần lead duyệt trong 2 giờ."},
        headers=admin,
    )
    client.post(
        API,
        json={"title": "Lương thưởng", "body": "Thưởng hotfix đêm.", "audience": "trainers"},
        headers=admin,
    )
    manager = as_user(world.manager)
    status = client.get("/api/v1/assistant/status", headers=manager).json()
    assert status["can_train"] is False and status["web"] is False

    res = client.post(
        "/api/v1/assistant/chat", json={"question": "hotfix cần ai duyệt?"}, headers=manager
    )
    assert [e for e, _ in sse(res.text)][-1] == "done"
    sent = streamer.calls[-1]
    assert "<tai_lieu>" in sent["stdin"] and "Hotfix cần lead duyệt" in sent["stdin"]
    assert "Thưởng hotfix" not in sent["stdin"]  # trainers only
    cmd = sent["cmd"]
    assert cmd[cmd.index("--tools") + 1] == ""

    assert (
        client.put("/api/v1/assistant/settings", json={"web": True}, headers=manager).status_code
        == 403
    )
    assert client.put("/api/v1/assistant/settings", json={"web": True}, headers=admin).json() == {
        "web": True
    }
    streamer.events = stream_events("ok")
    client.post("/api/v1/assistant/chat", json={"question": "hotfix?"}, headers=manager)
    cmd = streamer.calls[-1]["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "WebSearch"
    assert db_session.scalars(select(AssistantDoc).where(AssistantDoc.source == "builtin")).first()
    _ = app  # the app under test (dependency overrides live in the fixtures)
