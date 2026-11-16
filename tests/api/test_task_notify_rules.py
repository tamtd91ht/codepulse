"""docs/specs/31 §20.5: channels per kind, alert past quiet hours, grouping, the daily summary,
snooze, Telegram buttons, the rule log with dry runs, and the five round 2 rule templates."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Sprint,
    Task,
    TaskGitLink,
    TaskStatus,
    TaskVersion,
    TestRunTest,
)
from app.models.tasks import TaskRuleRun, TaskTestLink
from app.notifications import inbox
from app.tasks import automation
from tests.api.conftest import World, grant
from tests.api.test_task_automation import at_local, move, notes, turn_on
from tests.api.test_tasks import bp_of, create, enable, status_id
from tests.api.test_testcase_bugs import _setup

pytestmark = pytest.mark.integration

API = "/api/v1"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def link_telegram(db: Session, user_id: int, chat: str = "777") -> None:
    db.add(Account(kind="telegram", external_key=chat, user_id=user_id))
    db.flush()


def runs(db: Session, rule: str) -> list[TaskRuleRun]:
    return list(
        db.scalars(select(TaskRuleRun).where(TaskRuleRun.rule == rule).order_by(TaskRuleRun.id))
    )


# -- channels, alerts, grouping ------------------------------------------------------------------


def test_channel_per_kind_and_telegram_queue(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable(client, world, as_user)
    admin, binh = as_user(world.admin), as_user(world.binh)
    queued: list[int] = []
    monkeypatch.setattr(automation, "telegram_note_enqueuer", queued.extend)
    prefs = client.get(f"{API}/me/task-notify", headers=binh).json()
    assert prefs["channels"]["task.assigned"] == "inbox"
    assert prefs["summary"] == {"on": False, "time": "17:30", "channel": "inbox"}
    assert prefs["telegram_linked"] is False
    # Telegram needs a linked account.
    res = client.put(
        f"{API}/me/task-notify", headers=binh, json={"channels": {"task.assigned": "telegram"}}
    )
    assert res.status_code == 422
    bad = client.put(f"{API}/me/task-notify", headers=binh, json={"channels": {"x": "inbox"}})
    assert bad.status_code == 422
    link_telegram(db_session, world.binh.id)
    res = client.put(
        f"{API}/me/task-notify",
        headers=binh,
        json={"channels": {"task.assigned": "telegram", "task.commented": "off"}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["telegram_linked"] is True
    assert body["channels"]["task.assigned"] == "telegram"
    assert body["channels"]["task.commented"] == "off" and body["kinds"] == ["task.commented"]
    task = create(client, admin, world, title="A", assignee_id=world.binh.id)
    [row] = notes(db_session, world.binh.id, "task.assigned")
    assert queued == [row.id]  # the inbox row stays; Telegram is sent after the commit
    res = client.post(f"{API}/tasks/{task['key']}/comments", headers=admin, json={"body": "x"})
    assert res.status_code == 201
    assert notes(db_session, world.binh.id, "task.commented") == []  # off
    # Back to the inbox: nothing more queued.
    client.put(f"{API}/me/task-notify", headers=binh, json={"channels": {"task.assigned": "inbox"}})
    create(client, admin, world, title="B", assignee_id=world.binh.id)
    assert len(queued) == 1


def test_alert_passes_quiet_hours_and_digest_but_not_off(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    binh = as_user(world.binh)
    hour = datetime.now(TZ).hour
    res = client.put(
        f"{API}/me/task-notify",
        headers=binh,
        json={"mode": "digest", "quiet_hours": [hour, (hour + 1) % 24]},
    )
    assert res.status_code == 200, res.text
    kw: dict[str, Any] = {"link": "/tasks?task=CRM-9", "ref": "task:999"}
    inbox.notify(db_session, [world.binh.id], kind="task.status", title="s", **kw)
    inbox.notify(db_session, [world.binh.id], kind="task.alert", title="Bug cao nhất", **kw)
    assert [n.kind for n in notes(db_session, world.binh.id)] == ["task.alert"]
    client.put(f"{API}/me/task-notify", headers=binh, json={"channels": {"task.alert": "off"}})
    inbox.notify(db_session, [world.binh.id], kind="task.alert", title="again", **kw)
    assert len(notes(db_session, world.binh.id)) == 1
    noon = datetime(2026, 11, 16, 12, tzinfo=TZ)
    quiet = {**automation.default_prefs(), "quiet_hours": [11, 13]}
    assert automation.should_deliver(quiet, "task.alert", noon) == "now"
    assert automation.should_deliver(quiet, "task.summary", noon) == "now"


def test_updates_on_one_task_group_within_ten_minutes(
    world: World, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[dict[str, Any]] = []
    monkeypatch.setattr(inbox, "publisher", published.extend)
    kw: dict[str, Any] = {"link": "/tasks?task=OCX-2", "ref": "task:42"}
    user = world.binh.id
    inbox.notify(db_session, [user], kind="task.status", title="OCX-2 sang Đang làm", **kw)
    inbox.notify(db_session, [user], kind="task.commented", title="An bình luận", **kw)
    inbox.notify(db_session, [user], kind="task.status", title="OCX-2 sang Xong", **kw)
    rows = notes(db_session, user)
    assert len(rows) == 1
    assert rows[0].title == "3 cập nhật trên OCX-2" and rows[0].body == "OCX-2 sang Xong"
    assert rows[0].read_at is None
    # A mention stays its own row; another task too.
    inbox.notify(db_session, [user], kind="task.mentioned", title="@binh", **kw)
    inbox.notify(db_session, [user], kind="task.status", title="x", link=None, ref="task:43")
    assert len(notes(db_session, user)) == 3
    # Read, or older than ten minutes: a new row.
    rows[0].read_at = datetime.now(UTC)
    db_session.flush()
    inbox.notify(db_session, [user], kind="task.status", title="y", **kw)
    fresh = notes(db_session, user)[-1]
    assert fresh.title == "y"
    fresh.created_at = datetime.now(UTC) - timedelta(minutes=11)
    db_session.flush()
    inbox.notify(db_session, [user], kind="task.status", title="z", **kw)
    assert notes(db_session, user)[-1].title == "z"
    db_session.commit()
    assert any(m.get("updated_at") for m in published)


# -- snooze, Telegram buttons --------------------------------------------------------------------


def test_snooze_hides_from_unread_for_a_day(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    binh = as_user(world.binh)
    [row] = inbox.notify(db_session, [world.binh.id], kind="review_done", title="Review xong")
    other = inbox.notify(db_session, [world.binh.id], kind="review_done", title="Khác")[0]
    db_session.flush()
    before = client.get(f"{API}/inbox/unread-count", headers=binh).json()["unread"]
    res = client.post(f"{API}/inbox/snooze", headers=binh, json={"ids": [row.id]})
    assert res.status_code == 200, res.text
    assert res.json()["unread"] == before - 1
    unread = client.get(f"{API}/inbox?unread=true", headers=binh).json()["items"]
    assert row.id not in [i["id"] for i in unread] and other.id in [i["id"] for i in unread]
    history = client.get(f"{API}/inbox", headers=binh).json()["items"]
    assert next(i for i in history if i["id"] == row.id)["snoozed_until"]
    assert client.post(f"{API}/inbox/snooze", headers=binh, json={"ids": []}).status_code == 422
    # Someone else's row is not touched.
    client.post(f"{API}/inbox/snooze", headers=as_user(world.an), json={"ids": [other.id]})
    db_session.refresh(other)
    assert other.snoozed_until is None
    # Once the day is over it counts again.
    db_session.refresh(row)
    row.snoozed_until = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()
    assert client.get(f"{API}/inbox/unread-count", headers=binh).json()["unread"] == before
    assert client.get(f"{API}/inbox?kind=task.status", headers=binh).status_code == 200


def test_telegram_buttons_and_callbacks(world: World, db_session: Session) -> None:
    [row] = inbox.notify(
        db_session, [world.binh.id], kind="task.assigned", title="Giao OCX-1", link="/tasks?task=X"
    )
    text, markup = automation.telegram_note(row, "https://cp.example.com")
    first = markup["inline_keyboard"][0][0]
    assert first == {"text": "Mở", "url": "https://cp.example.com/tasks?task=X"}
    assert [b["text"] for b in markup["inline_keyboard"][1]] == ["Đã xem", "Ẩn 1 ngày"]
    # A local dashboard: no URL button (Telegram refuses it), the link goes in the text.
    text, markup = automation.telegram_note(row, "http://localhost:3000")
    assert "localhost:3000/tasks?task=X" in text and len(markup["inline_keyboard"]) == 1
    seen, hide = (b["callback_data"] for b in markup["inline_keyboard"][0])
    # Only the person linked to the Telegram account acts on their row.
    link_telegram(db_session, world.an.id, "111")
    assert "không phải của bạn" in automation.note_action(db_session, 111, seen)
    link_telegram(db_session, world.binh.id, "222")
    assert automation.note_action(db_session, 222, hide).startswith("Đã ẩn tới")
    assert row.snoozed_until is not None and row.read_at is None
    assert automation.note_action(db_session, 222, seen) == "Đã đánh dấu đã xem."
    assert row.read_at is not None
    assert "hiệu lực" in automation.note_action(db_session, 222, "nt|x|r")


# -- daily summary --------------------------------------------------------------------------------


def test_daily_summary_at_the_persons_time(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    space = enable(client, world, as_user)
    admin, binh = as_user(world.admin), as_user(world.binh)
    queued: list[int] = []
    monkeypatch.setattr(automation, "telegram_note_enqueuer", queued.extend)
    today = datetime.now(TZ).date()
    late = create(client, admin, world, title="Trễ", assignee_id=world.binh.id)
    res = client.patch(
        f"{API}/tasks/{late['key']}",
        headers=admin,
        json={"due_date": (today - timedelta(days=1)).isoformat()},
    )
    assert res.status_code == 200
    done = create(client, admin, world, title="Xong", assignee_id=world.binh.id)
    move(client, admin, done["key"], status_id(space, "Xong"))
    bad = client.put(
        f"{API}/me/task-notify",
        headers=binh,
        json={"summary": {"on": True, "channel": "telegram"}},
    )
    assert bad.status_code == 422  # not linked
    res = client.put(
        f"{API}/me/task-notify", headers=binh, json={"summary": {"on": True, "time": "17:30"}}
    )
    assert res.json()["summary"] == {"on": True, "time": "17:30", "channel": "inbox"}
    assert automation.send_summaries(db_session, at_local(17, today)) == 0  # before 17:30
    evening = at_local(17, today) + timedelta(minutes=45)
    assert automation.send_summaries(db_session, evening) == 1
    [summary] = notes(db_session, world.binh.id, "task.summary")
    assert "1 xong, 1 quá hạn, 0 MR chờ review" in summary.title
    assert done["key"] in (summary.body or "") and late["key"] in (summary.body or "")
    assert summary.link == "/tasks/me"
    assert automation.send_summaries(db_session, evening + timedelta(hours=1)) == 0  # once a day
    assert queued == []
    # By Telegram the next day.
    link_telegram(db_session, world.binh.id)
    client.put(f"{API}/me/task-notify", headers=binh, json={"summary": {"channel": "telegram"}})
    assert automation.send_summaries(db_session, evening + timedelta(days=1)) == 1
    db_session.commit()
    assert len(queued) == 1


# -- the rule log and dry runs -------------------------------------------------------------------


def test_rule_log_and_dry_run(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    late = create(client, admin, world, title="Trễ", assignee_id=world.an.id)
    client.patch(
        f"{API}/tasks/{late['key']}",
        headers=admin,
        json={"due_date": (datetime.now(TZ).date() - timedelta(days=2)).isoformat()},
    )
    rules = client.get(f"{API}/tasks/spaces/{bp}/automation", headers=admin).json()["rules"]
    flags = {r["key"]: r["can_dry_run"] for r in rules}
    assert flags["overdue_remind"] is True and flags["assign_on_start"] is False
    # Off, and a dry run: what it would do, nothing sent.
    res = client.post(f"{API}/tasks/spaces/{bp}/automation/overdue_remind/dry-run", headers=admin)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["count"] == 1 and out["items"][0]["task_key"] == late["key"]
    assert out["items"][0]["action"] == "notify"
    assert notes(db_session, world.an.id, "task.remind") == []
    [dry] = runs(db_session, "overdue_remind")
    assert dry.dry_run is True and dry.detail["count"] == 1
    for path, code in (
        ("assign_on_start", 409),
        ("nope", 404),
    ):
        got = client.post(f"{API}/tasks/spaces/{bp}/automation/{path}/dry-run", headers=admin)
        assert got.status_code == code
    denied = client.post(
        f"{API}/tasks/spaces/{bp}/automation/overdue_remind/dry-run", headers=as_user(world.an)
    )
    assert denied.status_code == 403
    # On: the real run writes its log row.
    turn_on(client, world, as_user, overdue_remind={"on": True})
    automation.run_schedule(db_session, at_local(10))
    assert len(notes(db_session, world.an.id, "task.remind")) == 1
    log = client.get(f"{API}/tasks/spaces/{bp}/automation/log?limit=10", headers=admin).json()
    assert [(i["rule"], i["action"], i["dry_run"]) for i in log["items"]] == [
        ("overdue_remind", "notify", False),
        ("overdue_remind", "dry_run", True),
    ]
    assert log["items"][0]["task_key"] == late["key"]
    assert log["items"][0]["rule_title"] == "Quá hạn → nhắc người làm"
    # Event rules log too.
    turn_on(client, world, as_user, assign_on_start={"on": True})
    started = create(client, admin, world, title="Bắt đầu")
    space = client.get(f"{API}/tasks/spaces", headers=admin).json()[0]
    move(client, admin, started["key"], status_id(space, "Đang làm"))
    assert [r.action for r in runs(db_session, "assign_on_start")] == ["assign"]


# -- round 2 rule templates ----------------------------------------------------------------------


def test_release_branch_sets_the_version_once(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    task = create(client, admin, world, title="Tính năng")
    version = TaskVersion(business_project_id=bp, name="2.1")
    db_session.add(version)
    mr = world.mr
    mr.state, mr.target_branch, mr.merged_at = "merged", "release/2.1", datetime.now(UTC)
    db_session.add(
        TaskGitLink(task_id=task["id"], kind="mr", project_id=mr.project_id, mr_iid=mr.iid)
    )
    db_session.flush()
    assert automation.release_name("release-2.1") == "2.1"
    assert automation.release_name("main") is None
    turn_on(client, world, as_user, release_version={"on": True})
    # Runs on every tick, at any hour.
    assert automation.run_schedule(db_session, at_local(3))["release_version"] == 1
    row = db_session.get(Task, task["id"])
    assert row is not None and row.version_id == version.id
    # Taken off by hand: the rule does not put it back.
    row.version_id = None
    db_session.flush()
    assert automation.run_schedule(db_session, at_local(4)).get("release_version", 0) == 0
    assert row.version_id is None
    assert [r.action for r in runs(db_session, "release_version")] == ["set_version"]


def test_column_age_tells_managers_once(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    grant(db_session, world.lead, bp_of(world), "manage")
    doing = db_session.get(TaskStatus, status_id(space, "Đang làm"))
    assert doing is not None
    doing.age_warning_hours = 24
    task = create(client, admin, world, title="Lâu")
    move(client, admin, task["key"], doing.id)
    row = db_session.get(Task, task["id"])
    assert row is not None
    row.status_changed_at = at_local(10) - timedelta(hours=30)
    db_session.flush()
    turn_on(client, world, as_user, column_age_warn={"on": True})
    assert automation.run_schedule(db_session, at_local(10))["column_age_warn"] == 1
    [note] = notes(db_session, world.lead.id, "task.remind")
    assert note.title == f"{task['key']} nằm ở Đang làm quá 24 giờ"
    assert (
        automation.run_schedule(db_session, at_local(10) + timedelta(days=1)).get(
            "column_age_warn", 0
        )
        == 0
    )


def test_failed_test_in_review_moves_back_and_tells(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    task = create(client, admin, world, title="Story", assignee_id=world.binh.id)
    move(client, admin, task["key"], status_id(space, "Đang review"))
    made = _setup(client, db_session, admin, bp_of(world))
    test = db_session.get(TestRunTest, made["test"]["id"])
    assert test is not None
    test.tested_at = datetime.now(UTC) + timedelta(seconds=5)
    db_session.add(TaskTestLink(task_id=task["id"], case_id=made["case"].id, kind="covers"))
    db_session.flush()
    # Off by default: nothing.
    assert automation.run_schedule(db_session, at_local(10)).get("test_failed_back", 0) == 0
    dry = client.post(
        f"{API}/tasks/spaces/{bp_of(world)}/automation/test_failed_back/dry-run", headers=admin
    ).json()
    assert dry["count"] == 1 and dry["items"][0]["action"] == "move"
    row = db_session.get(Task, task["id"])
    assert row is not None and row.status_id == status_id(space, "Đang review")
    turn_on(client, world, as_user, test_failed_back={"on": True})
    now = datetime.now(UTC)
    assert automation.run_schedule(db_session, now)["test_failed_back"] == 1
    db_session.refresh(row)
    assert row.status_id == status_id(space, "Đang làm")
    [alert] = notes(db_session, world.binh.id, "task.alert")
    assert "testcase Failed" in alert.title
    # Back in review the same day: not moved again today.
    move(client, admin, task["key"], status_id(space, "Đang review"))
    test.tested_at = datetime.now(UTC) + timedelta(seconds=10)
    db_session.flush()
    assert automation.run_schedule(db_session, now).get("test_failed_back", 0) == 0


def test_sprint_risk_and_unassigned_tell_managers(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    grant(db_session, world.lead, bp, "manage")
    today: date = datetime.now(TZ).date()
    sprint = Sprint(
        business_project_id=bp,
        name="Sprint 7",
        state="active",
        start_date=today - timedelta(days=12),
        end_date=today + timedelta(days=1),
    )
    db_session.add(sprint)
    db_session.flush()
    left = create(client, admin, world, title="Còn", assignee_id=world.an.id)
    done = create(client, admin, world, title="Xong", assignee_id=world.an.id)
    lonely = create(client, admin, world, title="Chưa ai làm")
    move(client, admin, done["key"], status_id(space, "Xong"))
    for key, points in ((left["id"], 5), (done["id"], 3), (lonely["id"], 0)):
        row = db_session.get(Task, key)
        assert row is not None
        row.sprint_id, row.story_points = sprint.id, Decimal(points)
    old = db_session.get(Task, lonely["id"])
    assert old is not None
    old.created_at = datetime.now(UTC) - timedelta(days=4)
    db_session.flush()
    turn_on(
        client,
        world,
        as_user,
        sprint_risk={"on": True},
        unassigned_in_sprint={"on": True},
    )
    sent = automation.run_schedule(db_session, at_local(10))
    assert sent["sprint_risk"] == 1 and sent["unassigned_in_sprint"] == 1
    titles = {n.title for n in notes(db_session, world.lead.id, "task.remind")}
    assert "Sprint Sprint 7 kết thúc sau 1 ngày, còn 62% điểm chưa xong" in titles
    assert f"{lonely['key']} chưa có người làm sau 3 ngày trong Sprint 7" in titles
    again = automation.run_schedule(db_session, at_local(10) + timedelta(hours=24))
    assert again.get("sprint_risk", 0) == 0 and again.get("unassigned_in_sprint", 0) == 0
    assert automation.config_of(None)["sprint_risk"] == {"on": False, "days": 2, "percent": 40}
