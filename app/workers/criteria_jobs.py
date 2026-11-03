"""Recompute the criteria scores of a dự án under one set (docs/specs/22 §4), queued when a set
is given to a dự án or by "Tính lại". Every finished review of the dự án is scored with the
set's current version; findings the model never placed go by their category. No LLM call."""

import logging
from collections.abc import Mapping
from datetime import datetime

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
