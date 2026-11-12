"""Testcase events on the project webhooks (docs/specs/30 §15, G11): run.closed, result.failed,
case.status_changed, plan.closed stored as deliveries and queued after commit; HMAC signed, 3
tries, failed then listed and sent again."""

import hashlib
import hmac
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BusinessProject,
    NotificationWebhook,
    TestCase,
    TestSuite,
    WebhookDelivery,
)
from app.testcases import events
from tests.api.conftest import BOX, FakeQueue, World

pytestmark = pytest.mark.integration

API = "/api/v1"
SECRET = "s3cret-s3cret-s3cret"


@pytest.fixture(autouse=True)
def _queue(queue: FakeQueue) -> Iterator[None]:
    saved = events.queue_provider
    events.queue_provider = lambda: queue
    yield
    events.queue_provider = saved


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _hook(db: Session, name: str, evs: list[str], **kw: Any) -> NotificationWebhook:
    row = NotificationWebhook(
        name=name, url=f"https://h.example.com/{name}", events=evs, format="json", **kw
    )
    db.add(row)
    db.flush()
    return row


def _suite(db: Session, bp: int) -> tuple[TestSuite, TestCase]:
    suite = TestSuite(business_project_id=bp, name="Bộ", position=1)
    db.add(suite)
    db.flush()
    case = TestCase(suite_id=suite.id, title="Đăng nhập", position=1)
    db.add(case)
    db.flush()
    return suite, case


def _deliveries(db: Session, hook: NotificationWebhook) -> list[WebhookDelivery]:
    return list(
        db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == hook.id)
            .order_by(WebhookDelivery.id)
        )
    )


def _jobs(queue: FakeQueue) -> list[int]:
    return [args[0] for f, args, _ in queue.jobs if f == events.JOB]


def test_events_are_stored_for_subscribed_hooks_and_queued_after_commit(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    other = BusinessProject(name="Khác")
    db_session.add(other)
    db_session.flush()
    runs = _hook(db_session, "runs", ["run.closed", "result.failed"], business_project_id=bp)
    everything = _hook(db_session, "all", ["run.closed", "plan.closed", "case.status_changed"])
    _hook(db_session, "other", ["run.closed"], business_project_id=other.id)
    _hook(db_session, "off", ["run.closed"], business_project_id=bp, enabled=False)
    _hook(db_session, "reviews", ["review.done"], business_project_id=bp)
    suite, case = _suite(db_session, bp)
    h = as_user(world.admin)

    run = client.post(
        f"{API}/testcases/runs",
        headers=h,
        json={"business_project_id": bp, "selection": {"suite_id": suite.id}, "name": "Sprint"},
    ).json()
    test_id = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=h).json()["items"][0][
        "id"
    ]
    # A passed result raises nothing; a failed one raises result.failed.
    ok = client.post(
        f"{API}/testcases/tests/{test_id}/results", headers=h, json={"status": "passed"}
    )
    assert ok.status_code == 201
    assert _deliveries(db_session, runs) == []
    failed = client.post(
        f"{API}/testcases/tests/{test_id}/results",
        headers=h,
        json={"status": "failed", "comment": "Lỗi 500", "defects": ["OMX-9"]},
    )
    assert failed.status_code == 201, failed.text
    (fail,) = _deliveries(db_session, runs)
    assert fail.event == "result.failed" and fail.status == "pending"
    assert fail.payload["test_key"] == f"T{test_id}" and fail.payload["case_key"] == f"C{case.id}"
    assert fail.payload["run_key"] == f"R{run['id']}" and fail.payload["comment"] == "Lỗi 500"
    assert fail.payload["defects"] == ["OMX-9"]
    assert fail.payload["business_project"] == {"id": bp, "name": "Demo"}
    assert _jobs(queue) == [fail.id]

    # Closing the run: both subscribed hooks, not the other dự án's, the disabled or review one.
    closed = client.post(f"{API}/testcases/runs/{run['id']}/close", headers=h)
    assert closed.status_code == 200
    run_events = [d for d in _deliveries(db_session, runs) if d.event == "run.closed"]
    (all_closed,) = _deliveries(db_session, everything)
    assert len(run_events) == 1 and all_closed.event == "run.closed"
    assert all_closed.payload["run_key"] == f"R{run['id']}"
    assert all_closed.payload["name"] == "Sprint"
    assert all_closed.payload["counts"]["failed"] == 1
    total = db_session.scalars(select(WebhookDelivery)).all()
    assert len(total) == 3

    # A case's status change.
    detail = client.get(f"{API}/testcases/{case.id}", headers=h).json()
    patched = client.patch(
        f"{API}/testcases/{case.id}",
        headers=h,
        json={"revision": detail["revision"], "status": "approved"},
    )
    assert patched.status_code == 200, patched.text
    changed = [d for d in _deliveries(db_session, everything) if d.event == "case.status_changed"]
    assert len(changed) == 1
    assert changed[0].payload["case_key"] == f"C{case.id}"
    assert (changed[0].payload["from"], changed[0].payload["to"]) == ("draft", "approved")
    # A change of another field raises nothing.
    detail = client.get(f"{API}/testcases/{case.id}", headers=h).json()
    client.patch(
        f"{API}/testcases/{case.id}",
        headers=h,
        json={"revision": detail["revision"], "title": "Đăng nhập mới"},
    )
    assert len(_deliveries(db_session, everything)) == 2
    # Bulk status.
    bulk = client.post(
        f"{API}/testcases/bulk",
        headers=h,
        json={"ids": [case.id], "action": "status", "status": "obsolete"},
    )
    assert bulk.status_code == 200, bulk.text
    assert _deliveries(db_session, everything)[-1].payload["to"] == "obsolete"
    assert len(_jobs(queue)) == len(db_session.scalars(select(WebhookDelivery)).all())


def test_plan_close_raises_plan_closed(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    hook = _hook(db_session, "plans", ["plan.closed"], business_project_id=bp)
    h = as_user(world.admin)
    plan = client.post(
        f"{API}/testcases/plans", headers=h, json={"business_project_id": bp, "name": "Q4"}
    )
    assert plan.status_code == 201, plan.text
    assert client.post(f"{API}/testcases/plans/{plan.json()['id']}/close", headers=h).is_success
    (row,) = _deliveries(db_session, hook)
    assert row.event == "plan.closed" and row.payload["plan_key"] == f"P{plan.json()['id']}"


def test_nothing_is_queued_when_the_change_rolls_back(
    world: World, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    _hook(db_session, "h", ["case.status_changed"], business_project_id=bp)
    db_session.commit()
    events.case_status_changed(db_session, bp, 1, "draft", "approved")
    db_session.rollback()
    db_session.commit()
    assert _jobs(queue) == []


@respx.mock
def test_failing_delivery_is_marked_failed_listed_and_resent(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    """AC4: a webhook failing 3 times is failed, listed, and sent again from the screen."""
    bp = _bp(world)
    hook = _hook(
        db_session,
        "broken",
        ["case.status_changed"],
        business_project_id=bp,
        secret_encrypted=BOX.encrypt(SECRET),
    )
    events.case_status_changed(db_session, bp, 42, "draft", "approved")
    db_session.commit()
    (row,) = _deliveries(db_session, hook)
    route = respx.post("https://h.example.com/broken").mock(return_value=httpx.Response(500))
    sleeps: list[float] = []
    events.send(db_session, row.id, BOX, sleep=sleeps.append)
    db_session.refresh(row)
    assert route.call_count == 3 and len(sleeps) == 2  # 3 tries with backoff
    assert row.status == "failed" and row.attempts == 3
    assert row.last_status_code == 500 and row.last_error == "HTTP 500"
    db_session.refresh(hook)
    assert hook.failures == 1

    # The body is signed (HMAC over "<timestamp>.<body>").
    sent = route.calls[0].request
    expected = hmac.new(
        SECRET.encode(),
        sent.headers["X-CodePulse-Timestamp"].encode() + b"." + sent.content,
        hashlib.sha256,
    ).hexdigest()
    assert sent.headers["X-CodePulse-Signature"] == f"sha256={expected}"
    assert sent.headers["X-CodePulse-Event"] == "case.status_changed"

    h = as_user(world.admin)
    listed = client.get(f"{API}/webhooks/{hook.id}/deliveries", headers=h)
    assert listed.status_code == 200
    (item,) = listed.json()
    assert item["status"] == "failed" and item["last_status_code"] == 500
    assert item["attempts"] == 3 and item["event"] == "case.status_changed"
    only_ok = client.get(
        f"{API}/webhooks/{hook.id}/deliveries", headers=h, params={"status": "delivered"}
    )
    assert only_ok.json() == []
    assert (
        client.get(f"{API}/webhooks/{hook.id}/deliveries", headers=as_user(world.an)).status_code
        == 403
    )

    queue.jobs.clear()
    resent = client.post(f"{API}/webhooks/{hook.id}/deliveries/{row.id}/resend", headers=h)
    assert resent.status_code == 200, resent.text
    assert resent.json()["status"] == "pending"
    assert _jobs(queue) == [row.id]
    again = client.post(f"{API}/webhooks/{hook.id}/deliveries/{row.id}/resend", headers=h)
    assert again.status_code == 409
    wrong = client.post(f"{API}/webhooks/{hook.id + 1}/deliveries/{row.id}/resend", headers=h)
    assert wrong.status_code == 404

    route.mock(return_value=httpx.Response(204))
    events.send(db_session, row.id, BOX, sleep=sleeps.append)
    db_session.refresh(row)
    assert row.status == "delivered" and row.delivered_at is not None
    assert row.attempts == 4 and row.last_error is None


def test_bulk_failed_results_raise_one_event(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    hook = _hook(db_session, "fails", ["result.failed"], business_project_id=bp)
    suite, _case = _suite(db_session, bp)
    db_session.add_all(
        [TestCase(suite_id=suite.id, title=f"Case {i}", position=2 + i) for i in range(2)]
    )
    db_session.flush()
    h = as_user(world.admin)
    run = client.post(
        f"{API}/testcases/runs",
        headers=h,
        json={"business_project_id": bp, "selection": {"suite_id": suite.id}, "name": "Bulk"},
    ).json()
    items = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=h).json()["items"]
    ids = [t["id"] for t in items]
    got = client.post(
        f"{API}/testcases/runs/{run['id']}/results",
        headers=h,
        json={"test_ids": ids, "status": "failed", "comment": "Hỏng"},
    )
    assert got.status_code == 200, got.text
    (row,) = _deliveries(db_session, hook)
    assert row.payload["failed_count"] == 3 and row.payload["name"] == "Bulk"
    assert [t["test_key"] for t in row.payload["tests"]] == [f"T{i}" for i in sorted(ids)]
    assert row.payload["tests"][0]["comment"] == "Hỏng"
    assert "3 test" in row.payload["text"]
    assert _jobs(queue) == [row.id]


@respx.mock
def test_disabled_webhook_is_not_sent(world: World, db_session: Session) -> None:
    bp = _bp(world)
    hook = _hook(db_session, "later-off", ["case.status_changed"], business_project_id=bp)
    events.case_status_changed(db_session, bp, 42, "draft", "approved")
    db_session.commit()
    (row,) = _deliveries(db_session, hook)
    hook.enabled = False
    db_session.commit()
    route = respx.post("https://h.example.com/later-off").mock(return_value=httpx.Response(200))
    events.send(db_session, row.id, BOX, sleep=lambda s: None)
    db_session.refresh(row)
    assert route.call_count == 0
    assert row.status == "failed" and row.last_error == events.HOOK_OFF


@respx.mock
def test_chat_formats_get_a_text_line(world: World, db_session: Session) -> None:
    bp = _bp(world)
    hook = _hook(db_session, "slack", ["run.closed"], business_project_id=bp)
    hook.format = "slack"
    events.emit(db_session, bp, "run.closed", {"run_key": "R1", "name": "Sprint", "counts": {}})
    db_session.commit()
    (row,) = _deliveries(db_session, hook)
    route = respx.post("https://h.example.com/slack").mock(return_value=httpx.Response(200))
    events.send(db_session, row.id, BOX, sleep=lambda s: None)
    body = route.calls[0].request.content.decode()
    assert "Đóng test run" in body and "R1" in body and "[Demo]" in body


def test_webhook_admin_accepts_the_new_events(
    client: TestClient, world: World, as_user: Any
) -> None:
    got = client.post(
        f"{API}/webhooks",
        headers=as_user(world.admin),
        json={
            "name": "QA",
            "url": "https://h.example.com/qa",
            "events": ["review.done", "run.closed", "result.failed", "case.status_changed"],
            "business_project_id": _bp(world),
        },
    )
    assert got.status_code == 201, got.text
    assert "result.failed" in got.json()["events"]
