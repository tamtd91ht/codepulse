"""The LLM watch (app/review/llm_recovery.py): a scheduler tick that probes a down LLM when its
probe is due, and the one resume job a recovery queues."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from rq import Queue

from app.admin_settings import effective, with_overrides
from app.config import get_settings
from app.db import get_redis, get_sessionmaker
from app.models import LlmHealth
from app.review import llm_recovery
from app.webhook.jobs import REVIEW_RETRY, review_job_timeout
from app.workers.queues import bp_of_repo, review_queue

log = logging.getLogger(__name__)


def _interval(session: Any) -> int:
    settings = get_settings()
    return int(
        effective(session, settings).get("llm_probe_interval_min", settings.llm_probe_interval_min)
    )


def llm_watch_job(provider_factory: Any = None) -> str:
    """Scheduler tick: probe the LLM if it is down and its probe is due; on success queue the
    resume job (one per outage: its id names the outage)."""
    settings = get_settings()
    with get_sessionmaker()() as session:
        row = llm_recovery.probe_due(session)
        if row is None:
            session.rollback()
            return "nothing due"
        edited = effective(session, settings)
        if not edited.get("llm_auto_resume", settings.llm_auto_resume):
            # Switched off meanwhile: no watch; the waiting reviews stay failed.
            row.status = "ok"
            session.commit()
            return "switched off"
        now = datetime.now(UTC)
        row.last_probe_at = now
        row.probes = (row.probes or 0) + 1
        try:
            if provider_factory is not None:
                provider = provider_factory()
            else:
                from app.review.providers import build_provider

                provider = build_provider(
                    with_overrides(session, settings),
                    timeout_s=llm_recovery.PROBE_TIMEOUT_S,
                    max_retries=0,
                )
            llm_recovery.ping(provider)
        except Exception as exc:
            row.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            row.next_probe_at = now + timedelta(minutes=_interval(session))
            session.commit()
            log.info("llm still down", extra={"probes": row.probes})
            return "still down"
        outage = int((row.down_since or now).timestamp())
        row.status = "recovering"
        session.commit()
    Queue("default", connection=get_redis()).enqueue(
        "app.workers.llm_jobs.llm_resume_job",
        job_id=f"llm-resume-{outage}",
        job_timeout=30 * 60,
    )
    log.info("llm works again: resume queued")
    return "recovered"


def llm_resume_job() -> dict[str, int]:
    """The LLM answers again: back to `ok` (new reviews call it), then the waiting reviews are
    claimed and queued, each exactly once."""
    settings = get_settings()
    with get_sessionmaker()() as session:
        row = session.get(LlmHealth, llm_recovery.HEALTH_ID, with_for_update=True)
        if row is None or row.status != "recovering":
            session.rollback()
            return {"queued": 0, "left": 0}  # another run did it, or it went down again
        row.status = "ok"
        row.recovered_at = datetime.now(UTC)
        session.commit()
        redis = get_redis()
        base = Queue("default", connection=redis)
        timeout = review_job_timeout(settings)

        def enqueue(func: str, *args: Any, first: bool = False, **kwargs: Any) -> None:
            # args[0] is the repo (review_commit_job / review_mr_job): its dự án's lane.
            target = review_queue(base, asked=first, bp_id=bp_of_repo(session, args[0]))
            target.enqueue(func, *args, job_timeout=timeout, retry=REVIEW_RETRY, **kwargs)

        result = llm_recovery.requeue_waiting(session, enqueue)
        row = session.get(LlmHealth, llm_recovery.HEALTH_ID)
        if row is not None:
            row.requeued_last = result["queued"]
            session.commit()
    log.info("llm resume done", extra=result)
    return result
