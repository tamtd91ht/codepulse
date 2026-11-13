"""Reviews left `queued` or `running` with no job behind them (user 2026-11-13: "rất nhiều commit
đang chờ hoặc đang chạy nhưng … không biết là có đang review hay đang treo").

A review row says `queued` from the moment it is asked for and `running` while a worker has it.
The job itself lives in Redis. A worker killed mid-review (a deploy, the machine running out
of memory) or a Redis restart leaves the row as it was with nothing left to run it: the list
shows "đang chờ / đang chạy" forever, and the live review panel, which reads Redis, shows
nothing. Before this sweep, a `running` row was only taken over 67 minutes later, and only if
a new job came for the same change; a lost `queued` row never moved.

Every SWEEP_MIN minutes the scheduler runs `requeue_orphans_job`. It looks at rows in those
states for more than GRACE (never one just queued or just started) and checks for their job
everywhere a review job can be: waiting in any review lane, waiting to be retried, or taken by
a worker. A row with no job is queued again in its dự án's lane, with the same requester and
trigger. A row lost more than MAX_REQUEUES times in a day is marked `failed` with the reason,
so a review that keeps killing its worker cannot loop. If Redis cannot be read completely, the
sweep does nothing: queueing every review twice is worse than waiting for the next sweep.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import Redis
from rq import Queue
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models import MergeRequest, Review
from app.review.llm_recovery import _cannot_run, _job, _project
from app.review.progress import _parse_jobs
from app.workers.queues import (
    ASKED_REVIEW_QUEUE,
    ASKED_TRIGGERS,
    DEFAULT_QUEUE,
    REVIEW_QUEUE,
    bp_of_repo,
    lane_names,
    review_queue,
    tier,
)

log = logging.getLogger(__name__)

SWEEP_MIN = 5
GRACE = timedelta(minutes=10)
MAX_REQUEUES = 2  # per review per COUNT_TTL_S
COUNT_TTL_S = 24 * 60 * 60
COUNT_KEY = "cp:orphan-requeue:{}"
BATCH = 200  # rows looked at per sweep
LOST_ERROR = (
    "Review bị gián đoạn nhiều lần (worker dừng giữa chừng hoặc hàng đợi mất job); "
    "bấm Chạy lại sau khi kiểm tra worker."
)

MrKey = tuple[str, str, str]  # (repo id, MR iid, sha prefix)


def _review_queue_names(redis: Redis) -> list[str]:
    """Every review queue name, the lanes RQ knows in Redis included. Unlike the readers of
    app/workers/queues.py this raises when Redis cannot list them: a lane left out would make
    its live jobs look lost."""
    names = [*lane_names(ASKED_REVIEW_QUEUE), *lane_names(REVIEW_QUEUE), DEFAULT_QUEUE]
    for q in Queue.all(connection=redis):
        if tier(q.name) in (ASKED_REVIEW_QUEUE, REVIEW_QUEUE) and q.name not in names:
            names.append(q.name)
    return names


def present_jobs(redis: Redis) -> tuple[set[MrKey], set[str]]:
    """Every review job in Redis: (MR keys, commit sha prefixes), waiting, waiting to be
    retried or taken by a worker. Raises on any Redis error: the caller must not act on a
    partial view."""
    ids: list[str] = []
    for name in _review_queue_names(redis):
        q = Queue(name, connection=redis)
        ids += q.get_job_ids()
        ids += q.scheduled_job_registry.get_job_ids()
        ids += q.started_job_registry.get_job_ids()
    mrs: set[MrKey] = set()
    commits: set[str] = set()
    for item in _parse_jobs([i.decode() if isinstance(i, bytes) else str(i) for i in ids]):
        if item["kind"] == "mr":
            mrs.add((str(item["project_id"]), str(item["ref"]), item["sha"]))
        elif item["kind"] == "commit":
            commits.add(item["sha"])
    return mrs, commits


def _same_sha(a: str, b: str) -> bool:
    """Two sha prefixes of the same commit (job ids carry 7 to 40 characters)."""
    return a.startswith(b) or b.startswith(a)


def _has_job(job_id: str, mrs: set[MrKey], commits: set[str]) -> bool:
    """Whether the review that `job_id` would run has a job in Redis (any suffix: a rerun,
    a new round, an earlier requeue of this sweep)."""
    found = _parse_jobs([job_id])
    if not found:
        return False
    item = found[0]
    if item["kind"] == "mr":
        return any(
            pid == str(item["project_id"])
            and iid == str(item["ref"])
            and _same_sha(sha, item["sha"])
            for pid, iid, sha in mrs
        )
    return any(_same_sha(prefix, item["sha"]) for prefix in commits)


def candidates(session: Session, now: datetime) -> list[Review]:
    """Rows queued or running for longer than GRACE, oldest first."""
    cutoff = now - GRACE
    return list(
        session.scalars(
            select(Review)
            .where(
                or_(
                    (Review.status == "queued") & (Review.updated_at < cutoff),
                    (Review.status == "running")
                    & (func.coalesce(Review.started_at, Review.updated_at) < cutoff),
                )
            )
            .order_by(Review.id)
            .limit(BATCH)
        )
    )


Present = Callable[[], tuple[set[MrKey], set[str]]]


def _job_of(session: Session, review: Review) -> tuple[tuple[Any, ...], str, str] | None:
    """(job args, job function, job id) of the review, None when its repo or MR is gone."""
    if _project(session, review) is None:
        return None
    if review.mr_id is not None and session.get(MergeRequest, review.mr_id) is None:
        return None
    return _job(session, review)


def _move(session: Session, review: Review, seen: tuple[str, Any], **values: Any) -> bool:
    """Change the row only if it is still as the sweep saw it (same status, same updated_at):
    a review that finished, failed or was claimed meanwhile is left alone. Committed."""
    status, updated_at = seen
    moved = session.execute(
        update(Review)
        .where(Review.id == review.id, Review.status == status, Review.updated_at == updated_at)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return bool(getattr(moved, "rowcount", 0))


def requeue_orphans(
    session: Session,
    present: Present,
    lost_count: Callable[[int], int],
    enqueue: Callable[..., Any],
    now: datetime | None = None,
) -> dict[str, int]:
    """Queue again (or fail) every review row whose job is gone; returns the counts.
    `present`: the jobs in Redis (`present_jobs`; raises when Redis cannot be read),
    `lost_count`: counts one more loss of a review and returns its losses in the last day.

    Redis is read BEFORE the rows: a job that exists when a row is read was already listed,
    so a review is never taken for lost because its job moved in between; each row is then
    changed only if it is still as read (`_move`), and enqueued after that commit."""
    now = now or datetime.now(UTC)
    try:
        mrs, commits = present()
    except Exception as exc:
        log.warning("orphan sweep skipped: queues not readable", extra={"error": repr(exc)})
        return {"requeued": 0, "failed": 0, "checked": 0}
    rows = candidates(session, now)
    seen = [(review, (review.status, review.updated_at)) for review in rows]
    session.commit()  # no transaction held while the rows are worked through
    requeued = failed = 0
    for review, snapshot in seen:
        job = _job_of(session, review)
        if job is not None and _has_job(job[2], mrs, commits):
            continue  # alive: whatever else changed, its worker decides
        reason = _cannot_run(session, review)
        if reason is not None or job is None:
            why = reason or "không còn biết repo"
            if _move(
                session,
                review,
                snapshot,
                status="failed",
                error=f"Review bị gián đoạn — không chạy lại: {why}",
                finished_at=now,
            ):
                failed += 1
            continue
        lost = lost_count(review.id)
        if lost > MAX_REQUEUES:
            if _move(session, review, snapshot, status="failed", error=LOST_ERROR, finished_at=now):
                failed += 1
                log.warning("orphan review failed", extra={"review_id": review.id, "lost": lost})
            continue
        if not _move(session, review, snapshot, status="queued", error=None):
            continue
        args, func_name, job_id = job
        try:
            enqueue(
                f"app.workers.review_jobs.{func_name}",
                *args,
                asked=review.trigger in ASKED_TRIGGERS,
                trigger=review.trigger,
                requested_by=review.requested_by_user_id,
                job_id=f"{job_id}-rerun-orphan-{review.id}-{review.round}-{lost}",
            )
        except Exception as exc:  # the row stays queued with no job: the next sweep retries
            log.warning(
                "orphan review not queued", extra={"review_id": review.id, "error": repr(exc)}
            )
            continue
        requeued += 1
        log.info("orphan review requeued", extra={"review_id": review.id, "lost": lost})
    return {"requeued": requeued, "failed": failed, "checked": len(rows)}


def requeue_orphans_job() -> dict[str, int]:
    """Scheduler entry (every SWEEP_MIN minutes)."""
    from app.config import get_settings
    from app.db import get_redis, get_sessionmaker
    from app.webhook.jobs import REVIEW_RETRY
    from app.workers.review_jobs import review_job_timeout

    settings = get_settings()
    redis = get_redis()
    base = Queue("default", connection=redis)
    timeout = review_job_timeout(settings)
    with get_sessionmaker()() as session:

        def enqueue(func: str, *args: Any, asked: bool, **kwargs: Any) -> None:
            # args[0] is the repo (review_commit_job / review_mr_job): its dự án's lane.
            target = review_queue(base, asked=asked, bp_id=bp_of_repo(session, args[0]))
            target.enqueue(func, *args, job_timeout=timeout, retry=REVIEW_RETRY, **kwargs)

        def lost_count(review_id: int) -> int:
            key = COUNT_KEY.format(review_id)
            lost = int(redis.incr(key))
            redis.expire(key, COUNT_TTL_S)
            return lost

        result = requeue_orphans(session, lambda: present_jobs(redis), lost_count, enqueue)
    if result["requeued"] or result["failed"]:
        log.info("orphan sweep done", extra=result)
    return result
