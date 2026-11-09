"""Review again once the LLM works again (user request 2026-11-04).

When a review fails because the LLM is unreachable or out of quota, and the admin switched
"Tự review lại khi LLM hoạt động lại" on, the review is marked `llm_retry` and the one row of
`llm_health` goes from `ok` to `down`, by a conditional UPDATE: however many reviews fail at
once, the first one flips it and the others change nothing. While it is down, new reviews do
not call the LLM (they are marked and wait).

A scheduler tick (every few minutes) probes the LLM only when the row is `down` and its next
probe is due (the configured interval, 30 min by default), under `FOR UPDATE SKIP LOCKED`, so
two schedulers never probe together. A probe that answers moves the row to `recovering` and
queues ONE resume job (its id names the outage). That job sets the row back to `ok` and claims
the marked reviews in batches with `UPDATE … RETURNING`: a review is claimed, so re-queued,
exactly once, even if the job ran twice.
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import LlmHealth, MergeRequest, Project, Review
from app.review.rubric import RUBRIC_VERSION
from app.workers.queues import ASKED_TRIGGERS

log = logging.getLogger(__name__)

HEALTH_ID = 1
TICK_MIN = 5  # the scheduler's tick; the probe interval is the admin's setting
DEFAULT_INTERVAL_MIN = 30
RESUME_BATCH = 200
PROBE_TIMEOUT_S = 60.0
WAITING_ERROR = "llm unavailable: chờ LLM hoạt động lại để tự review lại"
# The LLM is unreachable or out of quota (connection, timeout, rate / usage limits, overload,
# server errors, an expired token): a later attempt may succeed. A bad answer (unparsable
# output) or a budget stop is not an outage.
OUTAGE = re.compile(
    r"\(HTTP (?:401|403|408|409|425|429|5\d\d)\)|rate.?limit|usage.?limit"
    r"|limit (?:reached|exceeded)|quota|overload|credit balance|too many requests"
    r"|timed? ?out|timeout|connection|network"
    r"|econn|enotfound|eai_again|unavailable|failed after \d+ attempts|exited \d+ without a result"
    r"|failed to authenticate|authentication",
    re.IGNORECASE,
)
NOT_OUTAGE = re.compile(r"monthly llm budget|non-json|not an object|parse", re.IGNORECASE)


def is_llm_outage(error: str | None) -> bool:
    text = error or ""
    return bool(OUTAGE.search(text)) and not NOT_OUTAGE.search(text)


def _row(session: Session) -> LlmHealth:
    row = session.get(LlmHealth, HEALTH_ID)
    if row is None:  # created by the migration; a fresh test database may lack it
        row = LlmHealth(id=HEALTH_ID, status="ok", probes=0)
        session.add(row)
        session.flush()
    return row


def is_down(session: Session) -> bool:
    # Not while `recovering`: the probe answered, so a new review may call the LLM (and a review
    # gated then could miss the resume's scan).
    status = session.scalar(select(LlmHealth.status).where(LlmHealth.id == HEALTH_ID))
    return status == "down"


def mark_down(session: Session, error: str, interval_min: int) -> bool:
    """`ok` -> `down` once per outage (True for the one call that flipped it)."""
    _row(session)
    now = datetime.now(UTC)
    result = session.execute(
        update(LlmHealth)
        .where(LlmHealth.id == HEALTH_ID, LlmHealth.status == "ok")
        .values(
            status="down",
            down_since=now,
            last_error=error[:500],
            next_probe_at=now + timedelta(minutes=interval_min),
            probes=0,
        )
        .execution_options(synchronize_session=False)
    )
    flipped = bool(getattr(result, "rowcount", 0))
    if flipped:
        log.warning("llm marked down", extra={"error": error[:200]})
    return flipped


def waiting_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).where(Review.llm_retry.is_(True))) or 0)


def status(session: Session) -> dict[str, Any]:
    row = _row(session)
    return {
        "status": row.status,
        "down_since": row.down_since,
        "last_error": row.last_error,
        "last_probe_at": row.last_probe_at,
        "next_probe_at": row.next_probe_at if row.status == "down" else None,
        "probes": row.probes,
        "recovered_at": row.recovered_at,
        "requeued_last": row.requeued_last,
        "waiting_reviews": waiting_count(session),
    }


def probe_due(session: Session, now: datetime | None = None) -> LlmHealth | None:
    """The `down` row whose probe is due, locked for this caller only (SKIP LOCKED)."""
    now = now or datetime.now(UTC)
    return session.scalars(
        select(LlmHealth)
        .where(
            LlmHealth.id == HEALTH_ID,
            LlmHealth.status == "down",
            LlmHealth.next_probe_at <= now,
        )
        .with_for_update(skip_locked=True)
    ).first()


def ping(provider: Any) -> None:
    """The simplest call the LLM can answer; raises when it cannot."""
    from app.review.providers.base import ReviewPrompt

    provider.review(
        ReviewPrompt(
            system='Answer with the JSON object {"ok": true}.',
            user="ping",
            json_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )
    )


def requeue_waiting(session: Session, enqueue: Any) -> dict[str, int]:
    """Claim the reviews waiting for the LLM (llm_retry -> false, failed -> queued) batch by
    batch with UPDATE … RETURNING, and queue each: a review is claimed exactly once. One that
    can no longer run (repo untracked, review off, a newer round, another rubric, an MR moved on)
    is left failed with its reason."""
    queued = left = 0
    while True:
        ids = list(
            session.scalars(
                select(Review.id)
                .where(Review.llm_retry.is_(True))
                .order_by(Review.id)
                .limit(RESUME_BATCH)
                .with_for_update(skip_locked=True)
            )
        )
        if not ids:
            break
        claimed = list(
            session.scalars(
                update(Review)
                .where(Review.id.in_(ids), Review.llm_retry.is_(True))
                .values(llm_retry=False)
                .returning(Review)
                .execution_options(synchronize_session=False)
            )
        )
        for review in claimed:
            reason = _cannot_run(session, review)
            if reason is not None or review.status != "failed":
                left += 1
                if reason is not None and review.status == "failed":
                    review.error = f"{WAITING_ERROR} — không review lại: {reason}"[:1000]
                continue
            args, func_name, job_id = _job(session, review)
            review.status, review.error = "queued", None
            session.flush()
            enqueue(
                f"app.workers.review_jobs.{func_name}",
                *args,
                # What a person asked for goes first again (app/workers/queues.py).
                first=review.trigger in ASKED_TRIGGERS,
                # A re-run of the same review (the queue view reads "-rerun" job ids).
                trigger="rerun",
                requested_by=review.requested_by_user_id,
                job_id=f"{job_id}-rerun-llm-{review.id}-{review.round}",
            )
            queued += 1
        session.commit()
    return {"queued": queued, "left": left}


def _cannot_run(session: Session, review: Review) -> str | None:
    if review.superseded_by is not None:
        return "đã có vòng review mới hơn"
    if review.rubric_version != RUBRIC_VERSION:
        return "rubric đã đổi; yêu cầu review mới"
    project = _project(session, review)
    if project is None:
        return "không còn biết repo"
    if not project.tracked or not project.review_enabled:
        return "repo đã tắt theo dõi hoặc tắt review"
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        if mr is None or mr.state != "opened" or mr.head_sha != review.reviewed_sha:
            return "MR đã merge, đóng hoặc có push mới"
    return None


def _project(session: Session, review: Review) -> Project | None:
    from app.models import Commit

    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        return session.get(Project, mr.project_id) if mr and mr.project_id else None
    commit = session.get(Commit, review.commit_sha) if review.commit_sha else None
    return session.get(Project, commit.project_id) if commit and commit.project_id else None


def _job(session: Session, review: Review) -> tuple[tuple[Any, ...], str, str]:
    project = _project(session, review)
    assert project is not None  # noqa: S101 - _cannot_run checked it
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        assert mr is not None  # noqa: S101
        return (
            (project.id, mr.iid, review.reviewed_sha),
            "review_mr_job",
            f"review-mr-{project.id}-{mr.iid}-{review.reviewed_sha[:12]}",
        )
    return (
        (project.id, review.reviewed_sha),
        "review_commit_job",
        f"review-commit-{review.reviewed_sha[:12]}",
    )


def probe_soon(session: Session) -> bool:
    """Admin "Kiểm tra ngay": a down LLM is probed at the next tick (True when it is down)."""
    result = session.execute(
        update(LlmHealth)
        .where(LlmHealth.id == HEALTH_ID, LlmHealth.status == "down")
        .values(next_probe_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(result, "rowcount", 0))
