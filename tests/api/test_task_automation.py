"""docs/specs/31 §19.7: task automation rules and task notification preferences."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AppSetting,
    Commit,
    Review,
    ReviewFinding,
    Task,
    TaskHistory,
    TelegramBot,
    UserNotification,
)
from app.review.rubric import RUBRIC_VERSION
from app.tasks import automation
from tests.api.conftest import World, grant
from tests.api.test_tasks import bp_of, create, enable, status_id

pytestmark = pytest.mark.integration

API = "/api/v1"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def turn_on(client: TestClient, world: World, as_user: Any, **rules: dict[str, Any]) -> Any:
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}/automation", headers=as_user(world.admin), json=rules
    )
    assert res.status_code == 200, res.text
    return res.json()


def notes(session: Session, user_id: int, kind: str | None = None) -> list[UserNotification]:
    q = select(UserNotification).where(UserNotification.user_id == user_id)
    if kind:
        q = q.where(UserNotification.kind == kind)
    return list(session.scalars(q.order_by(UserNotification.id)))


def move(client: TestClient, headers: dict[str, str], key: str, status: int) -> Any:
    res = client.post(f"{API}/tasks/{key}/transition", headers=headers, json={"status_id": status})
    assert res.status_code == 200, res.text
    return res.json()


def at_local(hour: int, day: date | None = None) -> datetime:
    day = day or datetime.now(TZ).date()
    return datetime(day.year, day.month, day.day, hour, tzinfo=TZ).astimezone(UTC)


def test_rule_library_read_and_change(client: TestClient, world: World, as_user: Any) -> None:
    space = enable(client, world, as_user)
    an = as_user(world.an)
    res = client.get(f"{API}/tasks/spaces/{bp_of(world)}/automation", headers=an)
    assert res.status_code == 200
    body = res.json()
    assert body["can_manage"] is False
    rules = {r["key"]: r for r in body["rules"]}
    assert len(rules) >= 8
    assert rules["parent_done_ask"]["on"] is True  # the only one on by default
    assert rules["overdue_remind"]["on"] is False
    assert rules["review_stale_remind"]["params"][0]["value"] == 48
    # Only a Task manager changes them.
    put = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}/automation",
        headers=an,
        json={"overdue_remind": {"on": True}},
    )
    assert put.status_code == 403
    out = turn_on(
        client, world, as_user, overdue_remind={"on": True}, review_stale_remind={"hours": 24}
    )
    rules = {r["key"]: r for r in out["rules"]}
    assert rules["overdue_remind"]["on"] is True
    assert rules["review_stale_remind"]["params"][0]["value"] == 24
    admin = as_user(world.admin)
    for bad in (
        {"nope": {"on": True}},
        {"overdue_remind": {"on": "yes"}},
        {"review_stale_remind": {"hours": 0}},
        {"review_stale_remind": {"days": 3}},
    ):
        res = client.put(f"{API}/tasks/spaces/{bp_of(world)}/automation", headers=admin, json=bad)
        assert res.status_code == 422, bad
    # The git rules form sends the whole rules object back, automation included: accepted, and
    # the automation settings stay as they are.
    spaces = client.get(f"{API}/tasks/spaces", headers=admin).json()
    rules_back = spaces[0]["rules"]
    assert rules_back["automation"]["overdue_remind"]["on"] is True
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}",
        headers=admin,
        json={"key": "CRM", "rules": {**rules_back, "automation": {}, "mr_comment": True}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["rules"]["automation"]["overdue_remind"]["on"] is True
    assert space["key"] == "CRM"


def test_last_subtask_done_moves_parent_to_review(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    parent = create(client, admin, world, title="Story", type="story")
    one = create(client, admin, world, title="a", type="subtask", parent_key=parent["key"])
    two = create(client, admin, world, title="b", type="subtask", parent_key=parent["key"])
    done = status_id(space, "Xong")
    # Off: nothing moves.
    move(client, admin, one["key"], done)
    move(client, admin, two["key"], done)
    assert client.get(f"{API}/tasks/{parent['key']}", headers=admin).json()["status"]["name"] == (
        "Cần làm"
    )
    turn_on(client, world, as_user, subtasks_done_review={"on": True})
    move(client, admin, two["key"], status_id(space, "Đang làm"))
    # Not all done yet: the parent stays.
    assert client.get(f"{API}/tasks/{parent['key']}", headers=admin).json()["status"]["name"] == (
        "Cần làm"
    )
    move(client, admin, two["key"], done)
    detail = client.get(f"{API}/tasks/{parent['key']}", headers=admin).json()
    assert detail["status"]["name"] == "Đang review"
    # The system moved it: history without a person.
    row = db_session.scalars(
        select(TaskHistory).where(
            TaskHistory.task_id == detail["id"], TaskHistory.field == "status"
        )
    ).one()
    assert row.user_id is None and row.new == "Đang review"


def test_start_assigns_and_unblock_notifies(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    turn_on(client, world, as_user, assign_on_start={"on": True}, unblock_notify={"on": True})
    blocker = create(client, admin, world, title="API")
    blocked = create(client, admin, world, title="UI", assignee_id=world.binh.id)
    res = client.post(
        f"{API}/tasks/{blocker['key']}/links",
        headers=admin,
        json={"key": blocked["key"], "kind": "blocks"},
    )
    assert res.status_code == 201, res.text
    started = move(client, admin, blocker["key"], status_id(space, "Đang làm"))
    assert started["assignee"]["id"] == world.admin.id
    move(client, admin, blocker["key"], status_id(space, "Xong"))
    alerts = notes(db_session, world.binh.id, "task.unblocked")
    assert [n.title for n in alerts] == [f"{blocked['key']} không còn bị chặn: CRM-1 đã xong"]


def test_highest_bug_tells_managers_and_telegram(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable(client, world, as_user)
    grant(db_session, world.lead, bp_of(world), "manage")
    queued: list[int] = []
    monkeypatch.setattr(automation, "telegram_enqueuer", queued.extend)
    db_session.add(
        TelegramBot(
            name="bot",
            token_encrypted="x",
            business_project_id=bp_of(world),
            allowed_chat_ids=[-100123],
        )
    )
    db_session.flush()
    an = as_user(world.an)
    create(client, an, world, title="Sập đăng nhập", type="bug", priority="highest")
    assert notes(db_session, world.lead.id, "task.alert") == []  # rule off
    assert queued == []
    turn_on(client, world, as_user, bug_highest_notify={"on": True})
    create(client, an, world, title="Thường", type="bug", priority="high")
    bug = create(client, an, world, title="Mất dữ liệu", type="bug", priority="highest")
    alerts = notes(db_session, world.lead.id, "task.alert")
    assert [n.title for n in alerts] == [f"Bug ưu tiên cao nhất: {bug['key']}"]
    assert notes(db_session, world.an.id, "task.alert") == []  # not the reporter themselves
    assert queued == [bug["id"]]
    task = db_session.get(Task, bug["id"])
    assert task is not None
    assert "Mất dữ liệu" in automation.telegram_text(db_session, task, "https://cp.example")


def _critical(db_session: Session, world: World) -> None:
    sha = "e" * 40
    db_session.add(Commit(sha=sha, project_id=world.project.id, message="feat: x [CRM-1]"))
    db_session.flush()
    review = Review(
        target_type="commit",
        commit_sha=sha,
        reviewed_sha=sha,
        status="done",
        score=Decimal(40),
        finished_at=datetime.now(UTC),
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(
        ReviewFinding(review_id=review.id, severity="critical", category="security", title="SQLi")
    )
    db_session.flush()


def test_critical_code_tells_assignee_once_a_day(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    turn_on(client, world, as_user, critical_reassign={"on": True})
    create(client, admin, world, assignee_id=world.binh.id)
    _critical(db_session, world)
    res = client.post(
        f"{API}/tasks/CRM-1/git", headers=admin, json={"kind": "commit", "sha": "e" * 8}
    )
    assert res.status_code == 201, res.text
    move(client, admin, "CRM-1", status_id(space, "Đang review"))
    alerts = notes(db_session, world.binh.id, "task.alert")
    assert [n.title for n in alerts] == ["CRM-1: code còn 1 finding critical mở"]
    # The daily check finds the same task: already told today.
    automation.run_schedule(db_session, at_local(10))
    assert len(notes(db_session, world.binh.id, "task.alert")) == 1
    # Tomorrow it tells again; the assignee is never changed.
    automation.run_schedule(db_session, at_local(10) + timedelta(days=1))
    assert len(notes(db_session, world.binh.id, "task.alert")) == 2
    task = db_session.get(Task, int(str(alerts[0].ref).split(":")[2]))
    assert task is not None and task.assignee_id == world.binh.id


def test_scheduled_reminders(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    today = datetime.now(TZ).date()
    late = create(client, admin, world, title="Trễ", assignee_id=world.an.id)
    soon = create(client, admin, world, title="Sắp", assignee_id=world.an.id)
    stuck = create(client, admin, world, title="Chờ", assignee_id=world.binh.id)
    for key, due in ((late["key"], today - timedelta(days=2)), (soon["key"], today)):
        res = client.patch(f"{API}/tasks/{key}", headers=admin, json={"due_date": due.isoformat()})
        assert res.status_code == 200, res.text
    move(client, admin, stuck["key"], status_id(space, "Đang review"))
    row = db_session.get(Task, stuck["id"])
    assert row is not None
    row.status_changed_at = at_local(10) - timedelta(hours=50)
    db_session.flush()
    # Everything off: nothing.
    assert automation.run_schedule(db_session, at_local(10)) == {}
    turn_on(
        client,
        world,
        as_user,
        overdue_remind={"on": True},
        due_soon_remind={"on": True, "days": 1},
        review_stale_remind={"on": True, "hours": 48},
    )
    # Not before the morning.
    assert sum(automation.run_schedule(db_session, at_local(6)).values()) == 0
    sent = automation.run_schedule(db_session, at_local(10))
    assert sent == {"overdue": 1, "due_soon": 1, "review_stale": 2}
    titles = {n.title for n in notes(db_session, world.an.id, "task.remind")}
    assert titles == {f"{late['key']} quá hạn 2 ngày", f"{soon['key']} tới hạn hôm nay"}
    assert [n.title for n in notes(db_session, world.binh.id, "task.remind")] == [
        f"{stuck['key']} chờ review 2 ngày 2 giờ"
    ]
    # The reporter (admin) hears too; a second tick the same day sends nothing.
    assert len(notes(db_session, world.admin.id, "task.remind")) == 1
    assert sum(automation.run_schedule(db_session, at_local(14)).values()) == 0


def test_close_open_subtasks_with_parent(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    parent = create(client, admin, world, title="Story", type="story")
    create(client, admin, world, title="a", type="subtask", parent_key=parent["key"])
    create(client, admin, world, title="b", type="subtask", parent_key=parent["key"])
    ask = client.get(f"{API}/tasks/{parent['key']}/open-subtasks", headers=admin).json()
    assert ask["ask"] is True and [i["key"] for i in ask["items"]] == ["CRM-2", "CRM-3"]
    move(client, admin, parent["key"], status_id(space, "Xong"))
    res = client.post(
        f"{API}/tasks/{parent['key']}/close-subtasks", headers=admin, json={"resolution": "wont_do"}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"closed": ["CRM-2", "CRM-3"], "skipped": []}
    child = client.get(f"{API}/tasks/CRM-2", headers=admin).json()
    assert child["status"]["name"] == "Xong" and child["resolution"] == "wont_do"
    ask = client.get(f"{API}/tasks/{parent['key']}/open-subtasks", headers=admin).json()
    assert ask == {"ask": False, "items": []}
    # Someone without the right to move them closes nothing.
    other = create(client, admin, world, title="Story 2", type="story")
    create(client, admin, world, title="c", type="subtask", parent_key=other["key"])
    # Only once the parent is closed: the workflow is not skipped for an open parent.
    res = client.post(f"{API}/tasks/{other['key']}/close-subtasks", headers=admin, json={})
    assert res.status_code == 409
    move(client, admin, other["key"], status_id(space, "Xong"))
    res = client.post(
        f"{API}/tasks/{other['key']}/close-subtasks", headers=as_user(world.binh), json={}
    )
    assert res.json()["closed"] == [] and len(res.json()["skipped"]) == 1
    # Rule off: no bulk close at all.
    turn_on(client, world, as_user, parent_done_ask={"on": False})
    res = client.post(f"{API}/tasks/{other['key']}/close-subtasks", headers=admin, json={})
    assert res.status_code == 409


def test_notification_preferences_mute_hold_and_digest(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin, binh = as_user(world.admin), as_user(world.binh)
    prefs = client.get(f"{API}/me/task-notify", headers=binh).json()
    assert prefs["mode"] == "immediate" and prefs["kinds"] == []
    assert "task.assigned" in prefs["kind_labels"]
    bad = client.put(f"{API}/me/task-notify", headers=binh, json={"kinds": ["review_done"]})
    assert bad.status_code == 422
    bad = client.put(f"{API}/me/task-notify", headers=binh, json={"quiet_hours": [22, 22]})
    assert bad.status_code == 422
    # Muted: nothing arrives.
    res = client.put(f"{API}/me/task-notify", headers=binh, json={"kinds": ["task.assigned"]})
    assert res.status_code == 200 and res.json()["kinds"] == ["task.assigned"]
    create(client, admin, world, title="A", assignee_id=world.binh.id)
    assert notes(db_session, world.binh.id) == []
    # Digest: held, then one summary with the held ones in the history (read).
    res = client.put(f"{API}/me/task-notify", headers=binh, json={"mode": "digest", "kinds": []})
    assert res.json()["mode"] == "digest"
    create(client, admin, world, title="B", assignee_id=world.binh.id)
    res = client.post(f"{API}/tasks/CRM-2/comments", headers=admin, json={"body": "@binh xem"})
    assert res.status_code == 201, res.text
    assert notes(db_session, world.binh.id) == []
    held = db_session.get(AppSetting, f"task_notify_held:{world.binh.id}")
    assert held is not None and len(held.value) >= 2
    assert automation.send_digests(db_session, at_local(6)) == 0  # before the morning
    assert automation.send_digests(db_session, at_local(9)) == 1
    rows = notes(db_session, world.binh.id)
    digest = [n for n in rows if n.kind == "task.digest"]
    assert len(digest) == 1 and digest[0].read_at is None
    assert digest[0].title.startswith(f"{len(rows) - 1} cập nhật trên CRM-2")
    assert all(n.read_at is not None for n in rows if n.kind != "task.digest")
    assert db_session.get(AppSetting, f"task_notify_held:{world.binh.id}") is None
    # Once a day.
    create(client, admin, world, title="C", assignee_id=world.binh.id)
    assert automation.send_digests(db_session, at_local(11)) == 0
    # Quiet hours (immediate mode): held at night, out once they end.
    client.put(
        f"{API}/me/task-notify", headers=binh, json={"mode": "immediate", "quiet_hours": [22, 7]}
    )
    assert automation.send_digests(db_session, at_local(23)) == 0
    assert automation.send_digests(db_session, at_local(12)) == 1


def test_should_deliver_rules() -> None:
    noon = datetime(2026, 11, 16, 12, tzinfo=TZ)
    night = datetime(2026, 11, 16, 23, tzinfo=TZ)
    base = automation.default_prefs()
    assert automation.should_deliver(base, "task.status", noon) == "now"
    assert automation.should_deliver({**base, "kinds": ["task.status"]}, "task.status", noon) == (
        "drop"
    )
    quiet = {**base, "quiet_hours": [22, 7]}
    assert automation.should_deliver(quiet, "task.status", night) == "hold"
    assert automation.should_deliver(quiet, "task.status", noon) == "now"
    assert automation.should_deliver({**base, "mode": "digest"}, "review_done", noon) == "now"
    assert automation.should_deliver({**base, "mode": "digest"}, "task.digest", noon) == "now"
    assert automation.in_quiet([1, 5], 3) and not automation.in_quiet([1, 5], 5)
