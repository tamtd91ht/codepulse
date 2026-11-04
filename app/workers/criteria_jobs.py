"""Recompute the criteria scores of a dự án under one set (docs/specs/22 §4), queued when a set
is given to a dự án or by "Tính lại". Every finished review of the dự án is scored with the
set's current version; findings the model never placed go by their category. No LLM call."""

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import reviews_in_projects
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import CriteriaSet, CriteriaSetVersion, ProjectCriteriaSet, Review
from app.scoring.criteria_store import as_set_version, store_scores
from app.scoring.formula import MIN_CONFIDENCE_UNVERIFIED

log = logging.getLogger(__name__)

JOB = "app.workers.criteria_jobs.recompute_criteria_job"
JOB_TIMEOUT = 30 * 60
BATCH = 200  # reviews per commit


def recompute_criteria(
    session: Session,
    bp_id: int,
    set_id: int,
    since: datetime | None = None,
    weights_override: Mapping[str, float] | None = None,
    min_confidence_unverified: float = MIN_CONFIDENCE_UNVERIFIED,
    commit: bool = False,
) -> int:
    """How many reviews were scored. Nothing when the set is no longer the dự án's."""
    linked = session.get(ProjectCriteriaSet, (bp_id, set_id))
    criteria_set = session.get(CriteriaSet, set_id)
    if linked is None or criteria_set is None or criteria_set.current_version_id is None:
        return 0
    version_row = session.get(CriteriaSetVersion, criteria_set.current_version_id)
    version = as_set_version(criteria_set, version_row) if version_row else None
    if version is None:
        return 0
    query = (
        select(Review)
        .where(Review.status == "done", reviews_in_projects([bp_id]))
        .order_by(Review.id)
    )
    if since is not None:
        query = query.where(Review.counted_at >= since)
    done = 0
    for review in session.scalars(query):
        store_scores(session, review, [version], weights_override, min_confidence_unverified)
        done += 1
        if commit and done % BATCH == 0:
            session.commit()
    if commit:
        session.commit()
    return done


def recompute_criteria_job(bp_id: int, set_id: int, since: str | None = None) -> int:
    settings = get_settings()
    start = datetime.fromisoformat(since) if since else None
    with get_sessionmaker()() as session:
        done = recompute_criteria(
            session,
            bp_id,
            set_id,
            start,
            settings.scoring_weights,
            settings.verify_min_confidence_unverified,
            commit=True,
        )
    log.info(
        "criteria scores recomputed",
        extra={"business_project_id": bp_id, "set_id": set_id, "reviews": done},
    )
    return done


# -- Chấm lại theo tiêu chí, with filters (docs/specs/22 §10) -----------------------------------

RESCORE_JOB = "app.workers.criteria_jobs.rescore_job"
RESCORE_TIMEOUT = 2 * 60 * 60  # MAX_CLASSIFY_CALLS small calls fit well inside
RESCORE_KEEP_S = 24 * 60 * 60  # the dialog shows the last run's result for a day
CLASSIFY_TIMEOUT_S = 120.0


def rescore_job_id(bp_id: int) -> str:
    return f"criteria-rescore-{bp_id}"


def rescore_lock_key(bp_id: int) -> str:
    return f"cp:criteria-rescore-lock:{bp_id}"


def acquire_rescore_lock(redis: Any, bp_id: int) -> bool:
    """One run per dự án at a time (SET NX): two confirms at once cannot both queue it."""
    return bool(redis.set(rescore_lock_key(bp_id), "1", nx=True, ex=RESCORE_TIMEOUT + 600))


def release_rescore_lock(redis: Any, bp_id: int) -> None:
    try:
        redis.delete(rescore_lock_key(bp_id))
    except Exception:
        log.warning("criteria rescore lock not released", extra={"business_project_id": bp_id})


def _rereview(session: Session, user_id: int | None, queue: Any) -> Callable[[Review], bool]:
    """Queue a real review again for a thin one, the way "Chạy lại" does: a new round of the
    same change under the same rubric (`force`, `expected_round`), with the same job id shape
    (the live queue knows it). MRs only while open at the reviewed head; repos only while
    tracked with reviews on. The new round carries the trigger `criteria_rescore`, so it is
    never re-reviewed for thinness again."""
    from app.models import Commit, MergeRequest, Project
    from app.scoring.criteria_rescore import RESCORE_TRIGGER
    from app.webhook.jobs import REVIEW_RETRY
    from app.workers.queues import review_queue
    from app.workers.review_jobs import review_job_timeout

    target = review_queue(queue)
    timeout = review_job_timeout(get_settings())

    def go(review: Review) -> bool:
        common: dict[str, Any] = {
            "trigger": RESCORE_TRIGGER,
            "requested_by": user_id,
            "force": True,
            "expected_round": review.round,
            "job_timeout": timeout,
            "retry": REVIEW_RETRY,
        }
        suffix = f"-rerun-{review.id}-{review.round + 1}"
        if review.mr_id is not None:
            mr = session.get(MergeRequest, review.mr_id)
            if (
                mr is None
                or mr.project_id is None
                or mr.state != "opened"
                or mr.head_sha != review.reviewed_sha
            ):
                return False
            project = session.get(Project, mr.project_id)
            if project is None or not (project.tracked and project.review_enabled):
                return False
            target.enqueue(
                "app.workers.review_jobs.review_mr_job",
                project.id,
                mr.iid,
                review.reviewed_sha,
                job_id=f"review-mr-{project.id}-{mr.iid}-{review.reviewed_sha[:12]}{suffix}",
                **common,
            )
            return True
        commit = session.get(Commit, review.commit_sha) if review.commit_sha else None
        project = session.get(Project, commit.project_id) if commit and commit.project_id else None
        if commit is None or project is None or not (project.tracked and project.review_enabled):
            return False
        target.enqueue(
            "app.workers.review_jobs.review_commit_job",
            project.id,
            commit.sha,
            job_id=f"review-commit-{commit.sha[:12]}{suffix}",
            **common,
        )
        return True

    return go


def rescore_job(params: dict[str, Any]) -> dict[str, Any]:
    """Run a confirmed rescore (POST /business-projects/{bp}/criteria-rescore). `params`: bp_id,
    set_ids, project_id, user_ids, start, end (ISO), classify, rereview_thin, requested_by."""
    from rq import Queue, get_current_job

    from app.admin_settings import with_overrides
    from app.api.audit import audit
    from app.db import get_redis
    from app.review.providers import build_provider
    from app.scoring.criteria_rescore import Scope, TooMany, run
    from app.scoring.criteria_store import project_sets
    from app.workers.knowledge_jobs import month_spend_usd

    job = get_current_job()
    bp_id = int(params["bp_id"])
    redis = get_redis()

    def progress(done: int, total: int) -> None:
        if job is not None:
            job.meta["progress"] = {"done": done, "total": total}
            job.save_meta()  # type: ignore[no-untyped-call]

    result: dict[str, Any] = {}
    try:
        with get_sessionmaker()() as session:
            settings = with_overrides(session, get_settings())
            wanted = set(params.get("set_ids") or [])
            versions = [v for v in project_sets(session, bp_id) if not wanted or v.set_id in wanted]
            scope = Scope(
                bp_id=bp_id,
                project_id=params.get("project_id"),
                user_ids=tuple(params.get("user_ids") or ()),
                start=datetime.fromisoformat(params["start"]) if params.get("start") else None,
                end=datetime.fromisoformat(params["end"]) if params.get("end") else None,
            )
            provider = (
                build_provider(settings, timeout_s=CLASSIFY_TIMEOUT_S, max_retries=1)
                if params.get("classify")
                else None
            )
            budget = float(settings.monthly_llm_budget_usd or 0)
            requested_by = int(params.get("requested_by") or 0) or None

            def over_budget(unrecorded: float) -> bool:
                # The month's spend now (other jobs included, earlier batches recorded) plus
                # this batch's calls not written yet.
                return budget > 0 and month_spend_usd(session) + unrecorded >= budget

            def on_batch(cost: float, calls: int) -> None:
                # In the batch's transaction: a job that dies later has still recorded it.
                audit(
                    session,
                    requested_by,
                    "criteria.rescore.cost",
                    f"business_project:{bp_id}",
                    cost_usd=round(cost, 6),
                    calls=calls,
                )

            rereview = (
                _rereview(session, requested_by, Queue("default", connection=redis))
                if params.get("rereview_thin")
                else None
            )
            try:
                outcome = run(
                    session,
                    scope,
                    versions,
                    provider=provider,
                    over_budget=over_budget,
                    rereview=rereview,
                    on_batch=on_batch,
                    progress=progress,
                    weights_override=settings.scoring_weights,
                    min_confidence_unverified=settings.verify_min_confidence_unverified,
                )
            except TooMany as exc:
                result = {"error": str(exc)}
            else:
                result = outcome.as_dict()
            audit(
                session,
                requested_by,
                "criteria.rescore.done",
                f"business_project:{bp_id}",
                **result,
            )
            session.commit()
    finally:
        release_rescore_lock(redis, bp_id)
    log.info("criteria rescore done", extra={"business_project_id": bp_id, **result})
    if job is not None:
        job.meta["result"] = result
        job.save_meta()  # type: ignore[no-untyped-call]
    return result
