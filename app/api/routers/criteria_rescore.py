"""Chấm lại điểm theo tiêu chí (docs/specs/22 §10): preview, confirmed run, and its progress.

Scores are recomputed from the findings the reviews already have under the current version of
the dự án's sets; nothing is reviewed again unless the person asks to re-review the thin ones.
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.routers.criteria import _business, _may_assign
from app.api.security import Principal, require
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.models import Project
from app.scoring.criteria_rescore import MAX_REVIEWS, Scope, TooMany, count_reviews, plan
from app.scoring.criteria_store import SetVersion, project_sets
from app.workers.criteria_jobs import (
    RESCORE_JOB,
    RESCORE_KEEP_S,
    RESCORE_TIMEOUT,
    acquire_rescore_lock,
    release_rescore_lock,
    rescore_job_id,
)
from app.workers.knowledge_jobs import job_active
from app.workers.queues import review_queue

log = logging.getLogger(__name__)
router = APIRouter(tags=["criteria"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
QueueDep = Annotated[Enqueuer, Depends(get_queue)]
# Who may give sets to a dự án may also redo its scores (docs/specs/22 §5).
AssignPrincipal = Annotated[Principal, require("criteria.assign")]
MAX_DAYS = 366


class RescoreIn(BaseModel):
    """Which reviews: the dự án's (in the path), optionally one repo, some people, a period;
    which sets: some of the dự án's (default: all of them)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    set_ids: list[int] = Field(default_factory=list, max_length=20)
    project_id: int | None = None
    user_ids: list[int] = Field(default_factory=list, max_length=200)
    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")


class RescoreRun(RescoreIn):
    classify: bool = False  # Claude places the findings never placed in a component
    rereview_thin: bool = False  # review again, for real, the results too thin to score by
    confirm: Literal[True]  # the dialog's explicit confirmation


def _scope(
    session: Session, settings: Settings, bp_id: int, body: RescoreIn
) -> tuple[Scope, list[SetVersion]]:
    sets = project_sets(session, bp_id)
    if not sets:
        raise HTTPException(409, "dự án chưa được gán bộ tiêu chí nào")
    unknown = set(body.set_ids) - {s.set_id for s in sets}
    if unknown:
        raise HTTPException(
            422, "bộ tiêu chí không thuộc dự án: " + ", ".join(map(str, sorted(unknown)))
        )
    versions = [s for s in sets if not body.set_ids or s.set_id in body.set_ids]
    if body.project_id is not None:
        repo = session.get(Project, body.project_id)
        if repo is None or repo.business_project_id != bp_id:
            raise HTTPException(422, "repo không thuộc dự án này")
    tz = ZoneInfo(settings.report_tz)
    if body.date_from and body.date_to and body.date_from > body.date_to:
        raise HTTPException(422, "ngày bắt đầu phải trước ngày kết thúc")
    start = datetime.combine(body.date_from, time.min, tz) if body.date_from else None
    end = datetime.combine(body.date_to + timedelta(days=1), time.min, tz) if body.date_to else None
    return (
        Scope(bp_id, body.project_id, tuple(sorted(set(body.user_ids))), start, end),
        versions,
    )


def _filters(body: RescoreIn) -> dict[str, Any]:
    return {
        "set_ids": body.set_ids,
        "project_id": body.project_id,
        "user_ids": body.user_ids,
        "from": body.date_from.isoformat() if body.date_from else None,
        "to": body.date_to.isoformat() if body.date_to else None,
    }


@router.post("/business-projects/{bp_id}/criteria-rescore/preview")
def preview(
    bp_id: int,
    body: RescoreIn,
    principal: AssignPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """What a rescore would do, nothing written: how many reviews, how many scores move and by
    how much per set, how many findings Claude would place, which results are too thin."""
    _business(session, principal, bp_id)
    _may_assign(principal, bp_id)
    scope, versions = _scope(session, settings, bp_id, body)
    try:
        result = plan(
            session,
            scope,
            versions,
            settings.scoring_weights,
            settings.verify_min_confidence_unverified,
        ).as_dict()
    except TooMany as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        **result,
        "may_rereview": principal.covers("review.rerun", None, bp_id),
    }


@router.post("/business-projects/{bp_id}/criteria-rescore", status_code=202)
def start(
    bp_id: int,
    body: RescoreRun,
    principal: AssignPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
) -> dict[str, Any]:
    _business(session, principal, bp_id)
    _may_assign(principal, bp_id)
    if body.rereview_thin and not principal.covers("review.rerun", None, bp_id):
        raise HTTPException(403, "review lại cần quyền chạy lại review trong dự án này")
    scope, versions = _scope(session, settings, bp_id, body)
    if count_reviews(session, scope) > MAX_REVIEWS:
        raise HTTPException(
            422, f"quá {MAX_REVIEWS} review; thu hẹp bộ lọc (repo, người, thời gian)"
        )
    job_id = rescore_job_id(bp_id)
    redis = getattr(queue, "connection", None)
    # One run per dự án: a lock taken here (two confirms at once) and released by the job.
    if job_active(review_queue(queue), job_id) or (
        redis is not None and not acquire_rescore_lock(redis, bp_id)
    ):
        raise HTTPException(409, "dự án đang được chấm lại; đợi lượt trước xong")
    params = {
        "bp_id": bp_id,
        "set_ids": [v.set_id for v in versions],
        "project_id": scope.project_id,
        "user_ids": list(scope.user_ids),
        "start": scope.start.isoformat() if scope.start else None,
        "end": scope.end.isoformat() if scope.end else None,
        "classify": body.classify,
        "rereview_thin": body.rereview_thin,
        "requested_by": principal.user_id,
    }
    audit(
        session,
        principal.user_id,
        "criteria.rescore",
        f"business_project:{bp_id}",
        **_filters(body),
        classify=body.classify,
        rereview_thin=body.rereview_thin,
    )
    session.commit()
    try:
        # LLM work: on the review workers' queue, never in front of syncs on "default".
        review_queue(queue).enqueue(
            RESCORE_JOB,
            params,
            job_id=job_id,
            job_timeout=RESCORE_TIMEOUT,
            result_ttl=RESCORE_KEEP_S,
            failure_ttl=RESCORE_KEEP_S,
        )
    except Exception:
        if redis is not None:
            release_rescore_lock(redis, bp_id)
        log.exception("criteria rescore not queued", extra={"business_project_id": bp_id})
        raise HTTPException(503, "không xếp được việc chấm lại; thử lại sau") from None
    return {"status": "queued", "job_id": job_id}


@router.get("/business-projects/{bp_id}/criteria-rescore")
def status(
    bp_id: int, principal: AssignPrincipal, session: SessionDep, queue: QueueDep
) -> dict[str, Any]:
    """The dự án's latest run: queued / started / finished / failed, its progress and result."""
    _business(session, principal, bp_id)
    _may_assign(principal, bp_id)
    fetch = getattr(review_queue(queue), "fetch_job", None)
    job = None
    if fetch is not None:
        try:
            job = fetch(rescore_job_id(bp_id))
        except Exception:
            job = None
    if job is None:
        return {"status": "none"}
    state = job.get_status(refresh=True)
    meta = job.get_meta(refresh=True) if hasattr(job, "get_meta") else job.meta
    return {
        "status": str(getattr(state, "value", state)),
        "progress": (meta or {}).get("progress"),
        "result": (meta or {}).get("result"),
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
        "error": (str(job.exc_info or "").strip().splitlines() or [""])[-1][:300]
        if str(getattr(state, "value", state)) == "failed"
        else None,
    }
