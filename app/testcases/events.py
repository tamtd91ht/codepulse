"""Testcase events on the project webhooks (docs/specs/30 §15, G11).

`emit` stores one `WebhookDelivery` (pending) per enabled webhook of the dự án (or of
everything) that subscribes to the event, in the caller's transaction, and remembers the ids on
the session; once that transaction commits, one RQ job per delivery is queued
(`deliver_job`). A rolled-back event sends nothing. The job signs the body like every webhook
(app/notifications/deliver.py: HMAC over `<timestamp>.<body>`), tries up to 3 times with backoff,
then marks the delivery delivered or failed; the admin sees the history and sends a failed one
again (app/api/routers/notifications.py).
"""

import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session, SessionTransaction

from app.crypto import SecretBox
from app.models import (
    BusinessProject,
    NotificationWebhook,
    TestPlan,
    TestRun,
    TestRunTest,
    WebhookDelivery,
)
from app.notifications.deliver import ATTEMPTS, deliver
from app.notifications.payload import EVENT_LABEL
from app.testcases.content import key

log = logging.getLogger(__name__)

RUN_CLOSED = "run.closed"
RESULT_FAILED = "result.failed"
CASE_STATUS_CHANGED = "case.status_changed"
PLAN_CLOSED = "plan.closed"
EVENTS = (RUN_CLOSED, RESULT_FAILED, CASE_STATUS_CHANGED, PLAN_CLOSED)
PENDING = "tc_webhook_pending"
JOB = "app.testcases.events.deliver_job"
JOB_TIMEOUT = 5 * 60
ERROR_MAX = 300
FAILED_LISTED = 20  # tests listed in one result.failed event
HOOK_OFF = "webhook đang tắt"


class Enqueuer(Protocol):
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> Any: ...


def _default_queue() -> Enqueuer:
    from app.api.deps import get_queue  # lazy: pulls in rq

    return get_queue()


# Swapped in tests (a FakeQueue); never raises into the caller (the event has committed).
queue_provider: Callable[[], Enqueuer] = _default_queue


def _text(event_name: str, project: str | None, payload: dict[str, Any]) -> str:
    """One line for chat webhooks (Slack, Teams)."""
    prefix = f"[{project}] " if project else ""
    label = EVENT_LABEL.get(event_name, event_name)
    if event_name == RUN_CLOSED:
        counts = payload.get("counts") or {}
        facts = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
        return f"{prefix}{label}: {payload.get('run_key')} {payload.get('name') or ''} ({facts})"
    if event_name == PLAN_CLOSED:
        return f"{prefix}{label}: {payload.get('plan_key')} {payload.get('name') or ''}"
    if event_name == RESULT_FAILED:
        count = int(payload.get("failed_count") or 0)
        if count == 1 and payload.get("test_key"):
            return (
                f"{prefix}{label}: {payload.get('test_key')} ({payload.get('case_key')}) "
                f"trong {payload.get('run_key')}"
            )
        return f"{prefix}{label}: {count} test trong {payload.get('run_key')}"
    if event_name == CASE_STATUS_CHANGED:
        return (
            f"{prefix}{label}: {payload.get('case_key')} "
            f"{payload.get('from')} -> {payload.get('to')}"
        )
    return f"{prefix}{label}"


def hooks_for(session: Session, business_project_id: int, event_name: str) -> list[int]:
    """Enabled hooks of this dự án (not pinned to a repo) or of everything, subscribed."""
    rows = session.scalars(
        select(NotificationWebhook.id)
        .where(
            NotificationWebhook.enabled,
            NotificationWebhook.project_id.is_(None),
            or_(
                NotificationWebhook.business_project_id == business_project_id,
                NotificationWebhook.business_project_id.is_(None),
            ),
            NotificationWebhook.events.contains([event_name]),
        )
        .order_by(NotificationWebhook.id)
    )
    return [int(i) for i in rows]


def emit(session: Session, business_project_id: int, event: str, payload: dict[str, Any]) -> None:
    """Queue `event` for the webhooks of dự án `business_project_id`, once the caller commits.
    Never raises: a webhook problem must not fail the change that raised the event."""
    try:
        hooks = hooks_for(session, business_project_id, event)
        if not hooks:
            return
        bp = session.get(BusinessProject, business_project_id)
        name = bp.name if bp else None
        body = {
            "sent_at": datetime.now(UTC).isoformat(),
            "business_project": {"id": business_project_id, "name": name},
            **payload,
        }
        body["text"] = _text(event, name, payload)
        rows = [
            WebhookDelivery(webhook_id=hook_id, event=event, payload=body, status="pending")
            for hook_id in hooks
        ]
        with session.begin_nested():
            session.add_all(rows)
        session.info.setdefault(PENDING, []).extend(int(r.id) for r in rows)
    except Exception:
        log.exception("testcase webhook event not stored", extra={"event": event})


# -- the events, as the routers raise them ---------------------------------------------------------


def run_closed(session: Session, run: TestRun) -> None:
    from app.testcases.runs import run_counts

    counts = run_counts(session, [run.id]).get(run.id, {})
    emit(
        session,
        run.business_project_id,
        RUN_CLOSED,
        {
            "run_id": run.id,
            "run_key": key("run", run.id),
            "name": run.name,
            "counts": counts,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        },
    )


def plan_closed(session: Session, plan: TestPlan) -> None:
    emit(
        session,
        plan.business_project_id,
        PLAN_CLOSED,
        {"plan_id": plan.id, "plan_key": key("plan", plan.id), "name": plan.name},
    )


def results_failed(
    session: Session,
    run: TestRun,
    tests: Sequence[TestRunTest],
    *,
    status: str | None,
    comment: str | None,
    defects: list[str] | None,
    failed_count: int | None = None,
) -> None:
    """ONE `result.failed` per call (a result, a bulk result, a CI import), when the result
    recorded is Failed: the run, how many tests failed and the first FAILED_LISTED of them.
    `failed_count` defaults to len(tests) (a CI import passes only the first ones). A single
    test also keeps its fields at the top level (test_id, test_key, case_key, title)."""
    count = len(tests) if failed_count is None else failed_count
    if status != "failed" or count <= 0 or not tests:
        return
    listed: list[dict[str, Any]] = []
    for test in tests[:FAILED_LISTED]:
        item: dict[str, Any] = {
            "test_key": key("test", test.id),
            "case_key": key("case", test.case_id),
            "title": test.title,
        }
        if comment:
            item["comment"] = comment
        listed.append(item)
    payload: dict[str, Any] = {
        "run_id": run.id,
        "run_key": key("run", run.id),
        "name": run.name,
        "run_name": run.name,
        "failed_count": count,
        "tests": listed,
        "comment": comment,
        "defects": list(defects or []),
    }
    if count == 1:
        one = tests[0]
        payload.update(
            test_id=one.id,
            test_key=key("test", one.id),
            case_key=key("case", one.case_id),
            title=one.title,
        )
    emit(session, run.business_project_id, RESULT_FAILED, payload)


def case_status_changed(
    session: Session, business_project_id: int, case_id: int, before: str | None, after: str
) -> None:
    if before == after:
        return
    emit(
        session,
        business_project_id,
        CASE_STATUS_CHANGED,
        {"case_id": case_id, "case_key": key("case", case_id), "from": before, "to": after},
    )


def enqueue(queue: Enqueuer, delivery_ids: list[int]) -> int:
    """Queue one delivery job per id; returns how many were queued (Redis down: fewer)."""
    queued = 0
    for delivery_id in delivery_ids:
        try:
            queue.enqueue(JOB, delivery_id, job_timeout=JOB_TIMEOUT)
            queued += 1
        except Exception:
            log.warning("webhook delivery not queued", extra={"delivery_id": delivery_id})
    return queued


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    ids: list[int] = session.info.pop(PENDING, [])
    if not ids:
        return
    try:
        enqueue(queue_provider(), ids)
    except Exception:
        log.warning("webhook deliveries not queued", exc_info=True)


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    """The outermost transaction ended (committed: already queued; rolled back: dropped)."""
    if transaction.parent is None:
        session.info.pop(PENDING, None)


def send(
    session: Session,
    delivery_id: int,
    box: SecretBox,
    http: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> WebhookDelivery | None:
    """Deliver one pending delivery (up to ATTEMPTS tries) and record the outcome. Commits:
    nothing stays open while the endpoint is called."""
    from app.notifications.jobs import record  # the webhook's health counters

    row = session.get(WebhookDelivery, delivery_id)
    if row is None or row.status != "pending":
        return row
    hook = session.get(NotificationWebhook, row.webhook_id)
    if hook is None:
        return None
    if not hook.enabled:
        # Turned off after the event (or before a resend): not sent, not left pending.
        row.status, row.last_error = "failed", HOOK_OFF
        session.commit()
        return row
    url, fmt, event_name, payload = hook.url, hook.format, row.event, dict(row.payload)
    secret = box.decrypt(hook.secret_encrypted) if hook.secret_encrypted else None
    session.commit()
    result = deliver(url, fmt, secret, event_name, payload, http, sleep, attempts=ATTEMPTS)
    row = session.get(WebhookDelivery, delivery_id)
    if row is None:
        return None
    at = now or datetime.now(UTC)
    row.attempts = (row.attempts or 0) + result.attempts
    row.last_status_code = result.status_code
    if result.ok:
        row.status, row.delivered_at, row.last_error = "delivered", at, None
    else:
        row.status, row.last_error = "failed", (result.error or "failed")[:ERROR_MAX]
    record(session, hook.id, result, at)
    session.commit()
    log.info(
        "testcase webhook delivered" if result.ok else "testcase webhook failed",
        extra={"delivery_id": delivery_id, "webhook_id": hook.id, "event": event_name},
    )
    return row


def deliver_job(delivery_id: int) -> dict[str, Any]:
    """RQ entry point."""
    from app.crypto import secret_box
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        row = send(session, delivery_id, secret_box())
        return {"delivery_id": delivery_id, "status": row.status if row else None}
