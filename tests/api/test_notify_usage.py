"""docs/specs/31 §21.2 "Không bỏ sót": the notification settings page (event table, quiet
weekends, organisation defaults, "việc ngày mai"), the new personal events (MR review asked,
testcase Failed on my task), Telegram "Bắt đầu", and the anonymous usage telemetry."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import usage
from app.models import Account, MergeRequest, Task, TaskHistory, TestRunTest, UsageEvent
from app.models.tasks import TaskRuleRun, TaskTestLink
from app.notifications import inbox
from app.tasks import automation
from tests.api.conftest import World
from tests.api.test_task_automation import at_local, notes
from tests.api.test_task_notify_rules import link_telegram
from tests.api.test_tasks import bp_of, create, enable, status_id
from tests.api.test_testcase_bugs import _setup

pytestmark = pytest.mark.integration

API = "/api/v1"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def events(db: Session, event: str | None = None) -> list[tuple[str, str, str]]:
    q = select(UsageEvent.role, UsageEvent.event, UsageEvent.surface).order_by(UsageEvent.id)
    if event:
        q = q.where(UsageEvent.event == event)
    return [(str(r), str(e), str(s)) for r, e, s in db.execute(q).all()]


# -- the settings page ---------------------------


def test_event_table_telegram_only_where_meaningful_and_quiet_weekend(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    binh = as_user(world.binh)
    prefs = client.get(f"{API}/me/task-notify", headers=binh).json()
    assert list(prefs["kind_labels"]) == [
        "task.assigned",
        "task.mentioned",
        "task.status",
        "task.commented",
        "task.unblocked",
        "task.remind",
        "task.review",
        "task.test_failed",
        "task.alert",
    ]
    assert "task.status" not in prefs["telegram_kinds"]
    assert "task.review" in prefs["telegram_kinds"]
    assert prefs["quiet_days"] == [] and prefs["customised"] is False
    link_telegram(db_session, world.binh.id)
    bad = client.put(
        f"{API}/me/task-notify", headers=binh, json={"channels": {"task.status": "telegram"}}
    )
    assert bad.status_code == 422
    bad = client.put(f"{API}/me/task-notify", headers=binh, json={"quiet_days": [3]})
    assert bad.status_code == 422
    res = client.put(
        f"{API}/me/task-notify",
        headers=binh,
        json={"quiet_days": [5, 6], "channels": {"task.review": "telegram"}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["quiet_days"] == [5, 6] and body["customised"] is True
    assert body["channels"]["task.review"] == "telegram"
    saturday = datetime(2026, 11, 21, 12, tzinfo=TZ)
    monday = datetime(2026, 11, 23, 12, tzinfo=TZ)
    weekend = {**automation.default_prefs(), "quiet_days": [5, 6]}
    assert automation.should_deliver(weekend, "task.assigned", saturday) == "hold"
    assert automation.should_deliver(weekend, "task.assigned", monday) == "now"
    # The "highest" bug alert and production incidents still come through.
    assert automation.should_deliver(weekend, "task.alert", saturday) == "now"
    assert automation.should_deliver(weekend, "task.incident", saturday) == "now"


def test_org_defaults_apply_until_a_person_customises(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin, binh = as_user(world.admin), as_user(world.binh)
    body = {
        "channels": {"task.commented": "off"},
        "quiet_days": [6],
        "summary": {"on": True, "time": "17:00"},
    }
    assert client.put(f"{API}/task-notify/defaults", headers=binh, json=body).status_code == 403
    res = client.put(f"{API}/task-notify/defaults", headers=admin, json=body)
    assert res.status_code == 200, res.text
    assert res.json()["set"] is True and res.json()["summary"]["on"] is True
    mine = client.get(f"{API}/me/task-notify", headers=binh).json()
    assert mine["customised"] is False and mine["org_defaults"] is True
    assert mine["channels"]["task.commented"] == "off" and mine["quiet_days"] == [6]
    kw: dict[str, Any] = {"link": "/tasks?task=CRM-9", "ref": "task:999"}
    inbox.notify(db_session, [world.binh.id], kind="task.commented", title="c", **kw)
    assert notes(db_session, world.binh.id, "task.commented") == []  # muted by the defaults
    # The daily summary from the defaults, with "việc ngày mai".
    today = datetime.now(TZ).date()
    if today.weekday() == 6:
        today += timedelta(days=1)  # Sunday is quiet in these defaults
    task = create(client, admin, world, title="Mai", assignee_id=world.binh.id)
    res = client.patch(
        f"{API}/tasks/{task['key']}",
        headers=admin,
        json={"due_date": (today + timedelta(days=1)).isoformat()},
    )
    assert res.status_code == 200, res.text
    evening = at_local(17, today) + timedelta(minutes=30)
    assert automation.send_summaries(db_session, evening) >= 1
    [summary] = notes(db_session, world.binh.id, "task.summary")
    assert "1 việc ngày mai" in summary.title and task["key"] in (summary.body or "")
    # Saving one's own stops following the defaults; resetting follows them again.
    res = client.put(f"{API}/me/task-notify", headers=binh, json={"quiet_days": []})
    assert res.json()["customised"] is True and res.json()["quiet_days"] == []
    inbox.notify(db_session, [world.binh.id], kind="task.commented", title="c2", **kw)
    # Saved on top of the defaults: comments stay off.
    assert notes(db_session, world.binh.id, "task.commented") == []
    res = client.delete(f"{API}/me/task-notify", headers=binh)
    assert res.json()["customised"] is False and res.json()["quiet_days"] == [6]


# -- personal events on the tick -----------------


def test_review_request_once_per_mr(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    author = Account(kind="gitlab_user", external_key="an", user_id=world.an.id)
    reviewer = Account(kind="gitlab_user", external_key="binh", user_id=world.binh.id)
    db_session.add_all([author, reviewer])
    db_session.flush()
    mr = db_session.get(MergeRequest, world.mr.id)
    assert mr is not None
    mr.state, mr.title = "opened", "Thêm form khách hàng"
    mr.author_account_id, mr.reviewer_account_ids = author.id, [author.id, reviewer.id]
    mr.updated_at = datetime.now(UTC)
    db_session.flush()
    assert automation.send_personal(db_session)["reviews"] == 1
    [note] = notes(db_session, world.binh.id, "task.review")
    assert "!7 chờ bạn review" in note.title and note.link == "/tasks/me"
    assert notes(db_session, world.an.id, "task.review") == []  # the author is not asked
    assert automation.send_personal(db_session)["reviews"] == 0  # once per MR


def test_failed_testcase_on_my_task_once_per_result(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    task = create(client, admin, world, title="Story", assignee_id=world.binh.id)
    made = _setup(client, db_session, admin, bp_of(world))
    test = db_session.get(TestRunTest, made["test"]["id"])
    assert test is not None and test.status == "failed"
    db_session.add(TaskTestLink(task_id=task["id"], case_id=made["case"].id, kind="covers"))
    db_session.flush()
    assert automation.send_personal(db_session)["test_failed"] == 1
    [note] = notes(db_session, world.binh.id, "task.test_failed")
    assert note.title.startswith(f"{task['key']}:") and note.title.endswith("Failed")
    assert automation.send_personal(db_session)["test_failed"] == 0
    # Muted: nothing more.
    client.put(
        f"{API}/me/task-notify",
        headers=as_user(world.binh),
        json={"channels": {"task.test_failed": "off"}},
    )
    test.tested_at = datetime.now(UTC) + timedelta(seconds=5)
    db_session.flush()
    automation.send_personal(db_session)
    assert len(notes(db_session, world.binh.id, "task.test_failed")) == 1


# -- Telegram "Bắt đầu" --------------------------


def test_telegram_start_button(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    task = create(client, admin, world, title="Làm form", assignee_id=world.binh.id)
    [note] = notes(db_session, world.binh.id, "task.assigned")
    assert automation.can_start(db_session, note)
    _text, markup = automation.telegram_note(note, "https://cp.example.com", True)
    start = markup["inline_keyboard"][1][0]
    assert start == {"text": "Bắt đầu", "callback_data": f"nt|{note.id}|b"}
    link_telegram(db_session, world.an.id, "111")
    assert "không phải của bạn" in automation.note_action(db_session, 111, f"nt|{note.id}|b")
    link_telegram(db_session, world.binh.id, "222")
    answer = automation.note_action(db_session, 222, f"nt|{note.id}|b")
    assert answer == f"{task['key']} → Đang làm.", answer
    row = db_session.get(Task, task["id"])
    assert row is not None and row.status_id == status_id(space, "Đang làm")
    assert note.read_at is not None
    assert not automation.can_start(db_session, note)
    assert "đang ở Đang làm" in automation.note_action(db_session, 222, f"nt|{note.id}|b")
    assert ("member", "status_change", "telegram") in events(db_session, "status_change")


# -- usage telemetry -----------------------------


def test_usage_beacon_server_events_and_admin_summary(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin, an = as_user(world.admin), as_user(world.an)
    res = client.post(
        f"{API}/usage",
        headers=an,
        json={
            "events": [
                {"event": "page_open", "surface": "board"},
                {"event": "palette", "surface": "command"},
                {"event": "page_open", "surface": "nowhere"},  # unknown: ignored
                {"event": "status_change", "surface": "drag"},  # the server records these
            ]
        },
    )
    assert res.status_code == 204
    assert events(db_session) == [
        ("member", "page_open", "board"),
        ("member", "palette", "command"),
    ]
    task = create(client, an, world, title="A")
    assert events(db_session, "task_create")[-1][2] == "button"
    doing, review = status_id(space, "Đang làm"), status_id(space, "Đang review")
    res = client.post(f"{API}/tasks/{task['key']}/move", headers=an, json={"status_id": doing})
    assert res.status_code == 200, res.text
    res = client.post(
        f"{API}/tasks/{task['key']}/transition",
        headers={**an, "Cookie": "cp_surface=palette"},
        json={"status_id": review},
    )
    assert res.status_code == 200, res.text
    res = client.post(
        f"{API}/tasks/{task['key']}/transition", headers=an, json={"status_id": doing}
    )
    assert res.status_code == 200, res.text
    surfaces = [s for _r, _e, s in events(db_session, "status_change")]
    assert surfaces == ["drag", "palette", "detail"]
    # Nothing about the person or the task is kept.
    assert set(UsageEvent.__table__.columns.keys()) == {"id", "role", "event", "surface", "at"}
    # Git moves count as automated.
    usage.record(db_session, usage.SYSTEM, "status_change", "git")
    db_session.flush()
    assert client.get(f"{API}/usage/summary", headers=an).status_code == 403
    out = client.get(f"{API}/usage/summary?weeks=4", headers=admin).json()
    assert len(out["weeks"]) == 4
    assert out["automation"] == {"total": 4, "automated": 1, "share": 0.25, "target": 0.6}
    assert out["keyboard"]["keyboard"] == 1 and out["keyboard"]["total"] == 2
    board = next(e for e in out["events"] if e["event"] == "page_open")
    assert board["surface"] == "board" and board["total"] == 1 and board["counts"][-1] == 1
    assert {r["role"] for r in out["roles"]} == {"member", "system"}


def test_rule_undone_within_a_day_and_retention(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    task = create(client, as_user(world.admin), world, title="A", assignee_id=world.binh.id)
    now = datetime.now(UTC)
    assert usage.detect_undone(db_session, now) == 0  # the first run only sets the cursor
    at = now - timedelta(hours=30)
    db_session.add_all(
        [
            TaskRuleRun(
                business_project_id=bp_of(world),
                rule="subtasks_done_review",
                task_id=task["id"],
                action="move",
                detail={},
                at=at,
            ),
            TaskHistory(
                task_id=task["id"],
                user_id=None,
                field="status",
                old="Đang làm",
                new="Đang review",
                at=at,
            ),
            TaskHistory(
                task_id=task["id"],
                user_id=world.binh.id,
                field="status",
                old="Đang review",
                new="Đang làm",
                at=at + timedelta(hours=2),
            ),
            # A rule action nobody undid.
            TaskRuleRun(
                business_project_id=bp_of(world),
                rule="assign_on_start",
                task_id=task["id"],
                action="assign",
                detail={},
                at=at,
            ),
        ]
    )
    db_session.flush()
    assert usage.detect_undone(db_session, now) == 1
    assert events(db_session, "rule_undone") == [("member", "rule_undone", "subtasks_done_review")]
    assert usage.detect_undone(db_session, now) == 0  # the cursor moved on
    db_session.add(
        UsageEvent(role="member", event="page_open", surface="board", at=now - timedelta(days=181))
    )
    db_session.flush()
    assert usage.cleanup(db_session, now) == 1
    left = db_session.scalar(
        select(func.count())
        .select_from(UsageEvent)
        .where(UsageEvent.at < now - timedelta(days=180))
    )
    assert left == 0
