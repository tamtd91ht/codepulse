"""Data retention (docs/specs/11): after RETENTION_DAYS, drop code excerpts and raw GitLab
payloads; reviews, findings and aggregated metrics stay (review history is never deleted)."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import RawGitlab, Review, ReviewFinding

log = logging.getLogger(__name__)


@dataclass
class PurgeResult:
    excerpts_cleared: int
    raw_rows_deleted: int


def purge(session: Session, retention_days: int, now: datetime | None = None) -> PurgeResult:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    old_reviews = select(Review.id).where(Review.finished_at < cutoff)
    excerpts = session.execute(
        update(ReviewFinding)
        .where(ReviewFinding.review_id.in_(old_reviews), ReviewFinding.code_excerpt.is_not(None))
        .values(code_excerpt=None)
    )
    raw = session.execute(delete(RawGitlab).where(RawGitlab.fetched_at < cutoff))
    result = PurgeResult(
        excerpts_cleared=int(getattr(excerpts, "rowcount", 0) or 0),
        raw_rows_deleted=int(getattr(raw, "rowcount", 0) or 0),
    )
    log.info("retention purge", extra={"cutoff": cutoff.isoformat(), **result.__dict__})
    return result


def purge_job() -> dict[str, int]:
    """Scheduler entry point (daily)."""
    from app.config import get_settings
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session, session.begin():
        result = purge(session, get_settings().retention_days)
    return result.__dict__
