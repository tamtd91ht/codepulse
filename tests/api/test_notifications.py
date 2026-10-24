"""Notification webhooks: admin API, scope matching, delivery bookkeeping (docs/specs/16)."""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.main import app
from app.api.routers.notifications import get_http_client
from app.models import AuditLog, BusinessProject, Connection, NotificationWebhook, Project
from app.notifications.jobs import (
    DISABLE_AFTER,
    enqueue_review_notifications,
    notify_review,
    send_test,
)
from tests.api.conftest import BOX, FakeQueue, World

NOW = datetime(2026, 10, 25, 9, tzinfo=UTC)
DASH = "https://dash.example.com"


def hook(session: Session, url: str, events: list[str], **kw: Any) -> NotificationWebhook:
    row = NotificationWebhook(name=url.rsplit("/", 1)[-1], url=url, events=events, **kw)
    session.add(row)
    session.flush()
    return row


def test_admin_crud_never_returns_the_secret_and_checks_the_scope(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    bp = BusinessProject(name="OmiCRM")
    other = BusinessProject(name="Other")
    db_session.add_all([bp, other])
    db_session.flush()
    world.project.business_project_id = bp.id
    db_session.flush()

    body = {
        "name": "Ops channel",
        "url": "https://hooks.example.com/abc",
        "format": "slack",
        "secret": "topsecret-topsecret",
        "events": ["review.severe"],
        "business_project_id": bp.id,
    }
    created = client.post("/api/v1/webhooks", headers=admin, json=body)
    assert created.status_code == 201, created.text
    out = created.json()
    assert out["has_secret"] is True and "topsecret" not in created.text
    assert out["events"] == ["review.severe"] and out["enabled"] is True

    # A repo pinned under the wrong dự án, an ftp URL and an unknown event are refused.
    wrong = client.post(
        "/api/v1/webhooks",
        headers=admin,
        json={**body, "business_project_id": other.id, "project_id": world.project.id},
    )
    assert wrong.status_code == 422
    with_creds = client.post(
        "/api/v1/webhooks", headers=admin, json={**body, "url": "https://u:pw@hooks.example.com/x"}
    )
    assert with_creds.status_code == 422 and "pw" not in with_creds.json()["detail"][0]["msg"]
    assert (
        client.post("/api/v1/webhooks", headers=admin, json={**body, "url": "ftp://x"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/webhooks", headers=admin, json={**body, "events": ["nope"]}
        ).status_code
        == 422
    )

    listed = client.get("/api/v1/webhooks", headers=admin).json()
    assert [h["name"] for h in listed] == ["Ops channel"] and "secret" not in json.dumps(
        listed
    ).replace("has_secret", "")

    patched = client.patch(
        f"/api/v1/webhooks/{out['id']}",
        headers=admin,
        json={"secret": "", "enabled": False, "events": ["review.done", "review.failed"]},
    )
    assert patched.status_code == 200 and patched.json()["has_secret"] is False
    assert patched.json()["enabled"] is False
    assert client.patch(f"/api/v1/webhooks/{out['id']}", headers=admin, json={}).status_code == 422
    short = client.patch(f"/api/v1/webhooks/{out['id']}", headers=admin, json={"secret": "short"})
    assert short.status_code == 422
    audits = db_session.scalars(
        select(AuditLog).where(AuditLog.action.like("webhook.%")).order_by(AuditLog.id)
    ).all()
    assert [a.action for a in audits] == ["webhook.create", "webhook.update"]
    assert audits[1].details == {"fields": ["enabled", "events", "secret"]}
    assert "topsecret" not in json.dumps([a.details for a in audits])
    created_details = audits[0].details or {}
    assert created_details["host"] == "hooks.example.com" and "url" not in created_details

    assert client.delete(f"/api/v1/webhooks/{out['id']}", headers=admin).status_code == 204
    assert client.get("/api/v1/webhooks", headers=admin).json() == []
    assert client.get("/api/v1/webhooks", headers=as_user(world.lead)).status_code == 403


@respx.mock
def test_notify_review_matches_scope_and_events_and_signs_the_body(
    world: World, db_session: Session
) -> None:
    bp = BusinessProject(name="OmiCRM")
    other = BusinessProject(name="Other")
    db_session.add_all([bp, other])
    db_session.flush()
    world.project.business_project_id = bp.id
    everything = hook(db_session, "https://h.example.com/all", ["review.done"])
    severe_only = hook(
        db_session,
        "https://h.example.com/severe",
        ["review.severe"],
        business_project_id=bp.id,
        secret_encrypted=BOX.encrypt("s3cret-s3cret-s3cret"),
    )
    pinned = hook(
        db_session, "https://h.example.com/repo", ["review.done"], project_id=world.project.id
    )
    hook(db_session, "https://h.example.com/other", ["review.done"], business_project_id=other.id)
    hook(db_session, "https://h.example.com/off", ["review.done"], enabled=False)
    failed_only = hook(db_session, "https://h.example.com/failed", ["review.failed"])
    db_session.flush()
    routes = {
        name: respx.post(f"https://h.example.com/{name}").mock(return_value=httpx.Response(200))
        for name in ("all", "severe", "repo", "other", "off", "failed")
    }

    # an_review: done with one critical finding -> done + severe.
    sent = notify_review(db_session, world.an_review, BOX, DASH, sleep=lambda s: None, now=NOW)
    assert sent == 3
    assert {n for n, r in routes.items() if r.called} == {"all", "severe", "repo"}
    body = json.loads(routes["severe"].calls[0].request.content)
    assert body["event"] == "review.severe" and body["review"]["findings"]["critical"] == 1
    assert body["review"]["top_findings"][0]["title"] == "SQL injection"
    assert body["business_project"] == {"id": bp.id, "name": "OmiCRM"}
    assert body["author"] == {"user_id": world.an.id, "name": "An"}
    assert body["review"]["url"] == f"{DASH}/reviews/{world.an_review.id}"
    assert body["review"]["web_url"] == "https://gitlab.example.com/demo/p/-/merge_requests/7"
    assert routes["severe"].calls[0].request.headers["X-CodePulse-Signature"].startswith("sha256=")
    assert "X-CodePulse-Signature" not in routes["all"].calls[0].request.headers
    assert json.loads(routes["all"].calls[0].request.content)["event"] == "review.done"
    for h in (everything, severe_only, pinned):
        assert (h.deliveries, h.failures, h.last_status_code, h.last_delivery_at) == (
            1,
            0,
            200,
            NOW,
        )

    # binh_review: failed -> only the hook that listens to failures; error detail stays home.
    world.binh_review.error = "gitlab: GET https://gitlab.example.com/api/v4/projects/1001: 404"
    db_session.flush()
    sent = notify_review(db_session, world.binh_review, BOX, DASH, sleep=lambda s: None, now=NOW)
    assert sent == 1 and routes["failed"].call_count == 1 and failed_only.deliveries == 1
    failed_body = json.loads(routes["failed"].calls[0].request.content)
    assert (
        failed_body["review"]["status"] == "failed" and failed_body["review"]["error"] == "gitlab"
    )
    assert "api/v4" not in routes["failed"].calls[0].request.content.decode()  # web_url is fine


@respx.mock
def test_failures_are_counted_and_the_hook_is_disabled_after_too_many(
    world: World, db_session: Session
) -> None:
    broken = hook(db_session, "https://h.example.com/broken", ["review.done"])
    broken.failures = DISABLE_AFTER - 1
    db_session.flush()
    respx.post("https://h.example.com/broken").mock(return_value=httpx.Response(500))
    notify_review(db_session, world.an_review, BOX, DASH, sleep=lambda s: None, now=NOW)
    db_session.refresh(broken)
    assert broken.failures == DISABLE_AFTER and broken.enabled is False
    assert broken.last_error and "tắt sau" in broken.last_error and "HTTP 500" in broken.last_error
    # Disabled: nothing more is sent, even for a matching review.
    assert notify_review(db_session, world.an_review, BOX, DASH, sleep=lambda s: None) == 0
    # A later "Gửi thử" on the disabled hook counts, but keeps the reason it was switched off.
    reason = broken.last_error
    send_test(broken, db_session, BOX, DASH)
    db_session.refresh(broken)
    assert broken.deliveries == 2 and broken.last_error == reason and broken.enabled is False


def test_enqueue_only_when_a_hook_listens_and_the_review_is_final(
    world: World, db_session: Session, queue: FakeQueue
) -> None:
    assert enqueue_review_notifications(db_session, queue, world.an_review) is False
    hook(db_session, "https://h.example.com/x", ["review.done"])
    assert enqueue_review_notifications(db_session, queue, world.an_review) is True
    ((func, args, kwargs),) = queue.jobs
    assert func == "app.notifications.jobs.deliver_review_job" and args == (world.an_review.id,)
    assert kwargs["job_id"] == f"notify-review-{world.an_review.id}"
    world.an_review.status = "skipped"
    assert enqueue_review_notifications(db_session, queue, world.an_review) is False


def test_test_button_posts_a_ping_and_reports_the_answer(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    row = hook(db_session, "https://h.example.com/ping", ["review.done"], format="teams")
    db_session.commit()
    app.dependency_overrides[get_http_client] = lambda: httpx.Client()
    try:
        with respx.mock(assert_all_called=False) as router:
            router.route(host="testserver").pass_through()
            ping = router.post("https://h.example.com/ping").mock(return_value=httpx.Response(401))
            answer = client.post(f"/api/v1/webhooks/{row.id}/test", headers=as_user(world.admin))
    finally:
        app.dependency_overrides.pop(get_http_client, None)
    assert answer.status_code == 200
    assert answer.json() == {
        "ok": False,
        "status_code": 401,
        "error": "HTTP 401",
        "attempts": 1,
        "enabled": True,
    }
    sent = json.loads(ping.calls[0].request.content)
    assert (
        sent["@type"] == "MessageCard"
        and ping.calls[0].request.headers["X-CodePulse-Event"] == "ping"
    )
    db_session.refresh(row)
    assert (row.deliveries, row.failures, row.last_status_code) == (1, 1, 401)


def test_a_connection_goes_only_when_no_repo_uses_it(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    listed = client.get("/api/v1/connections", headers=admin).json()
    assert listed[0]["repos"] == 1
    busy = client.delete(f"/api/v1/connections/{world.project.connection_id}", headers=admin)
    assert busy.status_code == 409 and "1 repo" in busy.json()["detail"]

    spare = Connection(
        name="old", base_url="https://old.example.com", token_encrypted=BOX.encrypt("t")
    )
    db_session.add(spare)
    db_session.flush()
    renamed = client.patch(
        f"/api/v1/connections/{spare.id}", headers=admin, json={"name": "legacy"}
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "legacy"
    assert client.delete(f"/api/v1/connections/{spare.id}", headers=admin).status_code == 204
    assert db_session.get(Connection, spare.id) is None
    assert db_session.get(Project, world.project.id) is not None
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "connection.delete")).one()
    assert audit.details == {"name": "legacy", "base_url": "https://old.example.com"}
