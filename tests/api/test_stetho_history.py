"""Lịch sử Stetho (docs/specs/28 §9): kept 30 days, tagged cheaply, admins only."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant import history
from app.config import Settings
from app.models import (
    AssistantConversation,
    AssistantTurn,
    AuditLog,
    PermissionSet,
    PermissionSetItem,
    UserPermissionSet,
)
from app.review.providers.base import ReviewResult
from tests.api.conftest import World
from tests.api.test_assistant import (  # noqa: F401 - connected, streamer: fixtures
    FakeStreamer,
    connected,
    stream_events,
    streamer,
)

API = "/api/v1/stetho/history"
NOW = datetime(2026, 11, 10, 9, tzinfo=UTC)
ANSWER = (
    "## Cách ghi commit sửa bug\n\n- Thêm dòng `Fixes: CRM-415` ở cuối message.\n"
    "- Tiêu đề theo Conventional Commits, mã ở cuối.\n\n```sh\ngit commit\n```\n"
    "| a | b |\n|---|---|\n\n(Nguồn: [1] Chuẩn liên kết Jira)"
)


def test_the_gist_of_an_answer_is_a_few_points_without_code() -> None:
    points = history.key_points(ANSWER)
    assert points is not None
    lines = points.split("\n")
    assert lines[0] == "Cách ghi commit sửa bug"
    assert "Fixes: CRM-415" in lines[1] and len(lines) == 3
    assert "git commit" not in points and "| a |" not in points
    short = history.key_points("Một câu trả lời ngắn. Câu thứ hai.")
    assert short == "Một câu trả lời ngắn.\nCâu thứ hai."
    assert history.key_points("") is None


@pytest.mark.parametrize(
    ("text", "tag"),
    [
        ("commit sửa bug phải ghi mã jira thế nào", "jira_commit"),
        ("điểm chất lượng review của dự án tuần này", "quality_analysis"),
        ("DPI tháng này của tôi", "dpi"),
        ("thời tiết hôm nay", "other"),
    ],
)
def test_keyword_rules_are_the_fallback(text: str, tag: str) -> None:
    assert history.rule_tag(text) == tag


def turn(session: Session, cid: str, user_id: int, question: str, when: datetime) -> str:
    return history.record_turn(
        session,
        conversation_id=cid,
        user_id=user_id,
        question=question,
        answer=ANSWER,
        status="ok",
        screen="/jira",
        model="claude-x",
        cost_usd=0.01,
        duration_ms=900,
        docs=2,
        web=False,
        now=when,
    )


def test_turns_group_into_conversations_and_tags_are_cheap(
    db_session: Session, world: World
) -> None:
    turn(db_session, "c1", world.an.id, "Commit sửa bug ghi gì?", NOW - timedelta(minutes=40))
    turn(db_session, "c1", world.an.id, "Còn MR thì sao?", NOW - timedelta(minutes=30))
    # Someone else reusing the id gets a conversation of their own.
    other = turn(db_session, "c1", world.binh.id, "DPI tháng này?", NOW - timedelta(minutes=30))
    assert other != "c1"
    # ... and their next turn on that id goes to the same one.
    again = turn(db_session, "c1", world.binh.id, "Còn tháng trước?", NOW - timedelta(minutes=29))
    assert again == other
    one = db_session.get(AssistantConversation, "c1")
    assert one is not None and one.turns == 2 and one.title == "Commit sửa bug ghi gì?"
    turns = db_session.scalars(
        select(AssistantTurn).where(AssistantTurn.conversation_id == "c1")
    ).all()
    assert [t.question for t in turns] == ["Commit sửa bug ghi gì?", "Còn MR thì sao?"]
    assert "Fixes: CRM-415" in (turns[0].answer_points or "")
    assert turns[0].answer_chars == len(ANSWER)

    sent: list[str] = []

    def classify(prompt: Any) -> ReviewResult:
        sent.append(prompt.user)
        return ReviewResult(
            output={"tags": [{"n": 1, "tag": "jira_commit"}]}, model="haiku", cost_usd=0.001
        )

    out = history.tag_pending(db_session, classify, NOW)
    assert out == {"tagged": 2, "source": "ai", "cost_usd": 0.001}
    # One call for the batch, questions only (no answers), cut short.
    assert len(sent) == 1 and "Còn MR thì sao?" in sent[0] and "Fixes" not in sent[0]
    assert (one.tag, one.tag_source) == ("jira_commit", "ai")
    second = db_session.get(AssistantConversation, other)
    assert second is not None
    assert (second.tag, second.tag_source) == ("dpi", "rule")  # the AI left it out

    # Going on reopens it for tagging, unless an admin tagged it.
    turn(db_session, "c1", world.an.id, "Thêm câu nữa", NOW)
    assert one.tag is None
    one.tag, one.tag_source = "other", "manual"
    turn(db_session, "c1", world.an.id, "Và câu nữa", NOW)
    assert (one.tag, one.tag_source, one.turns) == ("other", "manual", 4)


def test_a_failed_call_tags_by_rules_and_old_conversations_go(
    db_session: Session, world: World
) -> None:
    turn(db_session, "old", world.an.id, "DPI tháng trước", NOW - timedelta(days=31))
    turn(db_session, "new", world.an.id, "jira commit", NOW - timedelta(hours=1))

    def down(prompt: Any) -> ReviewResult:
        raise RuntimeError("monthly LLM budget reached")

    assert history.tag_pending(db_session, down, NOW)["source"] == "rule"
    assert history.purge(db_session, NOW) == 1
    assert db_session.get(AssistantConversation, "old") is None
    left = db_session.scalars(
        select(AssistantTurn).where(AssistantTurn.conversation_id == "old")
    ).all()
    assert left == []


def test_only_admins_even_not_holders_of_admin_rights(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    held = PermissionSet(name="Quản trị hệ thống")
    db_session.add(held)
    db_session.flush()
    for perm in ("admin.people", "admin.projects", "assistant.train", "admin.llm"):
        db_session.add(PermissionSetItem(set_id=held.id, permission=perm, scope="flag"))
    db_session.add(UserPermissionSet(user_id=world.manager.id, set_id=held.id))
    db_session.flush()
    for who in (world.manager, world.lead, world.an):
        for path in (f"{API}/summary", f"{API}/conversations"):
            assert client.get(path, headers=as_user(who)).status_code == 403
    assert client.get(f"{API}/summary", headers=as_user(world.admin)).status_code == 200


def test_the_report_and_a_conversation(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    now = datetime.now(UTC)
    turn(db_session, "a1", world.an.id, "Commit sửa bug ghi gì?", now - timedelta(hours=2))
    turn(db_session, "a2", world.an.id, "DPI của tôi", now - timedelta(hours=1))
    turn(db_session, "b1", world.binh.id, "Điểm review dự án", now - timedelta(hours=1))
    for cid, tag in (("a1", "jira_commit"), ("a2", "dpi")):
        row = db_session.get(AssistantConversation, cid)
        assert row is not None
        row.tag, row.tag_source = tag, "ai"
    db_session.flush()
    admin = as_user(world.admin)

    body = client.get(f"{API}/summary", headers=admin).json()
    assert body["kpis"]["conversations"] == 3 and body["kpis"]["people"] == 2
    assert body["kpis"]["untagged"] == 1
    an = next(m for m in body["by_member"] if m["user"]["id"] == world.an.id)
    assert an["conversations"] == 2 and {t["tag"] for t in an["tags"]} == {"jira_commit", "dpi"}
    assert {t["tag"] for t in body["by_tag"]} == {"jira_commit", "dpi", None}

    listed = client.get(f"{API}/conversations", params={"tag": "dpi"}, headers=admin).json()
    assert [c["id"] for c in listed["items"]] == ["a2"]
    found = client.get(f"{API}/conversations", params={"q": "điểm review"}, headers=admin).json()
    assert [c["id"] for c in found["items"]] == ["b1"]

    one = client.get(f"{API}/conversations/a1", headers=admin).json()
    assert one["turns"][0]["question"] == "Commit sửa bug ghi gì?"
    assert any("Fixes" in p for p in one["turns"][0]["answer_points"])
    read = select(AuditLog).where(AuditLog.action == "stetho.history.read")
    assert db_session.scalars(read).first() is not None

    res = client.patch(f"{API}/conversations/b1", json={"tag": "quality_analysis"}, headers=admin)
    assert res.json() == {"id": "b1", "tag": "quality_analysis", "tag_source": "manual"}
    bad = client.patch(f"{API}/conversations/b1", json={"tag": "bogus"}, headers=admin)
    assert bad.status_code == 422
    assert client.delete(f"{API}/conversations/b1", headers=admin).status_code == 204


def test_a_chat_turn_is_kept_without_the_question_in_the_audit(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    connected: Settings,  # noqa: F811
    streamer: FakeStreamer,  # noqa: F811
) -> None:
    streamer.events = stream_events("## Kết luận\n\n- Ghi `Fixes: KEY`.")
    cid = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    client.post(
        "/api/v1/assistant/chat",
        json={"question": "commit sửa bug?", "conversation_id": cid},
        headers=as_user(world.manager),
    )
    row = db_session.get(AssistantConversation, cid)
    assert row is not None and row.user_id == world.manager.id and row.turns == 1
    kept = db_session.scalars(
        select(AssistantTurn).where(AssistantTurn.conversation_id == cid)
    ).one()
    assert kept.question == "commit sửa bug?" and kept.answer_points == "Kết luận\nGhi Fixes: KEY."
    audits = db_session.scalars(select(AuditLog).where(AuditLog.action == "assistant.chat")).all()
    assert "question" not in audits[-1].details and audits[-1].details["conversation"] == cid
