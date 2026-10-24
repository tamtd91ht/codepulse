"""Which webhooks a review concerns, and the RQ job that delivers to them.

No database transaction stays open while an endpoint is called: hooks and payload are read
first, each delivery's bookkeeping is one atomic UPDATE committed on its own, so a slow or dead
endpoint neither pins a connection nor loses the counters that switch it off.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import Session

from app.collector.sync import Enqueuer
from app.config import get_settings
from app.crypto import SecretBox, secret_box
from app.db import get_sessionmaker
from app.models import Commit, MergeRequest, NotificationWebhook, Project, Review
from app.notifications.deliver import DeliveryResult, deliver
from app.notifications.payload import NOTIFIED_STATUSES, build, events_for, finding_counts, sample

log = logging.getLogger(__name__)

NOTIFY_JOB_TIMEOUT = 10 * 60
# A hook that fails this many deliveries in a row is switched off; the admin sees why.
DISABLE_AFTER = 20
# Deliveries of one review are capped so the job cannot outlive its timeout (3 tries x 10 s each).
MAX_HOOKS_PER_REVIEW = 12


def project_of(session: Session, review: Review) -> Project | None:
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        return session.get(Project, mr.project_id) if mr and mr.project_id else None
    commit = session.get(Commit, review.commit_sha) if review.commit_sha else None
    return session.get(Project, commit.project_id) if commit and commit.project_id else None


def webhooks_in_scope(session: Session, project: Project) -> list[NotificationWebhook]:
    """Enabled hooks for everything, for the repo's dự án, or pinned to the repo itself."""
    scope = [
        NotificationWebhook.project_id == project.id,
        NotificationWebhook.business_project_id.is_(None)
        & NotificationWebhook.project_id.is_(None),
    ]
    if project.business_project_id is not None:
        scope.append(
            (NotificationWebhook.business_project_id == project.business_project_id)
            & NotificationWebhook.project_id.is_(None)
        )
    return list(
        session.scalars(
            select(NotificationWebhook)
            .where(NotificationWebhook.enabled, or_(*scope))
            .order_by(NotificationWebhook.id)
        )
    )


def enqueue_review_notifications(session: Session, queue: Enqueuer, review: Review) -> bool:
    """Queue the delivery job when the review is final and someone listens; cheap otherwise."""
    if review.status not in NOTIFIED_STATUSES:
        return False
    project = project_of(session, review)
    if project is None or not webhooks_in_scope(session, project):
        return False
    try:
        queue.enqueue(
            "app.notifications.jobs.deliver_review_job",
            review.id,
            job_id=f"notify-review-{review.id}",
            job_timeout=NOTIFY_JOB_TIMEOUT,
        )
    except Exception:
        # The review itself is saved; only this notification is lost.
        log.exception("could not queue review notification", extra={"review_id": review.id})
        return False
    return True


def record(
    session: Session, webhook_id: int, result: DeliveryResult, now: datetime | None = None
) -> bool:
    """Atomic bookkeeping of one delivery (safe with concurrent jobs); returns the enabled flag.

    A hook that is already off keeps its last_error (the reason it was switched off).
    """
    error = (result.error or "failed")[:300]
    row = session.execute(
        update(NotificationWebhook)
        .where(NotificationWebhook.id == webhook_id)
        .values(
            last_delivery_at=now or datetime.now(UTC),
            last_status_code=result.status_code,
            deliveries=NotificationWebhook.deliveries + 1,
            failures=0 if result.ok else NotificationWebhook.failures + 1,
            last_error=(
                None
                if result.ok
                else case(
                    (NotificationWebhook.enabled.is_(False), NotificationWebhook.last_error),
                    else_=error,
                )
            ),
        )
        .returning(NotificationWebhook.failures, NotificationWebhook.enabled)
    ).one_or_none()
    if row is None:  # deleted meanwhile
        return False
    failures, enabled = int(row[0]), bool(row[1])
    if result.ok or not enabled or failures < DISABLE_AFTER:
        return enabled
    session.execute(
        update(NotificationWebhook)
        .where(NotificationWebhook.id == webhook_id, NotificationWebhook.enabled.is_(True))
        .values(
            enabled=False,
            last_error=f"tắt sau {failures} lần gửi lỗi liên tiếp ({error})"[:300],
        )
    )
    log.warning("notification webhook disabled", extra={"webhook_id": webhook_id})
    return False


@dataclass(frozen=True)
class Planned:
    webhook_id: int
    url: str
    format: str
    secret: str | None
    event: str


def plan(session: Session, review: Review, box: SecretBox) -> tuple[list[Planned], list[str]]:
    """Which hooks get which event for this review (one message per hook)."""
    if review.status not in NOTIFIED_STATUSES:
        return [], []
    project = project_of(session, review)
    if project is None:
        return [], []
    hooks = webhooks_in_scope(session, project)
    if not hooks:
        return [], []
    events = events_for(review.status, finding_counts(session, review.id))
    planned: list[Planned] = []
    for hook in hooks:
        # A severe review goes out as "review.severe" when the hook subscribes to it, else "done".
        wanted = [e for e in events if e in (hook.events or [])]
        if not wanted:
            continue
        secret = box.decrypt(hook.secret_encrypted) if hook.secret_encrypted else None
        planned.append(Planned(hook.id, hook.url, hook.format, secret, wanted[-1]))
    if len(planned) > MAX_HOOKS_PER_REVIEW:
        log.warning(
            "too many webhooks for one review; extra ones skipped",
            extra={"review_id": review.id, "hooks": len(planned)},
        )
        planned = planned[:MAX_HOOKS_PER_REVIEW]
    return planned, events


def notify_review(
    session: Session,
    review: Review,
    box: SecretBox,
    dashboard_url: str,
    http: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> int:
    """Deliver the review's events to every hook in scope; returns the number of deliveries.

    Commits: once after planning (nothing stays open during HTTP) and once per delivery.
    """
    planned, _ = plan(session, review, box)
    if not planned:
        return 0
    payload = build(session, review, dashboard_url, now)
    review_id = review.id
    session.commit()
    for item in planned:
        result = deliver(item.url, item.format, item.secret, item.event, payload, http, sleep)
        record(session, item.webhook_id, result, now)
        session.commit()
        log.info(
            "review notification delivered" if result.ok else "review notification failed",
            extra={
                "review_id": review_id,
                "webhook_id": item.webhook_id,
                "event": item.event,
                "status": result.status_code,
            },
        )
    return len(planned)


def send_test(
    webhook: NotificationWebhook,
    session: Session,
    box: SecretBox,
    dashboard_url: str,
    http: httpx.Client | None = None,
    now: datetime | None = None,
) -> DeliveryResult:
    """The admin's "Gửi thử": one `ping` with a sample payload, counted like a delivery."""
    secret = box.decrypt(webhook.secret_encrypted) if webhook.secret_encrypted else None
    result = deliver(
        webhook.url, webhook.format, secret, "ping", sample(dashboard_url, now), http, attempts=1
    )
    record(session, webhook.id, result, now)
    return result


def deliver_review_job(review_id: int) -> dict[str, int]:
    settings = get_settings()
    with get_sessionmaker()() as session:
        review = session.get(Review, review_id)
        if review is None:
            return {"sent": 0}
        return {"sent": notify_review(session, review, secret_box(), settings.dashboard_url)}
