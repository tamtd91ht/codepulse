"""Data retention (docs/specs/11): after RETENTION_DAYS, drop code excerpts and raw GitLab
payloads; reviews, findings and aggregated metrics stay (review history is never deleted).
Evidence packs (docs/specs/20 §11) keep their month and status, but their notes and items go
after EVIDENCE_RETENTION_MONTHS. In-app notifications (docs/specs/24) go after
INBOX_KEEP_DAYS."""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import (
    EvidenceItem,
    EvidencePack,
    RawGitlab,
    Review,
    ReviewFinding,
    UserNotification,
)

log = logging.getLogger(__name__)

INBOX_KEEP_DAYS = 180


@dataclass
class PurgeResult:
    excerpts_cleared: int
    raw_rows_deleted: int
    evidence_cleared: int = 0
    notifications_deleted: int = 0


def _months_before(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    return date(index // 12, index % 12 + 1, 1)


def purge(
    session: Session,
    retention_days: int,
    now: datetime | None = None,
    evidence_months: int | None = None,
) -> PurgeResult:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    old_reviews = select(Review.id).where(Review.finished_at < cutoff)
    excerpts = session.execute(
        update(ReviewFinding)
        .where(ReviewFinding.review_id.in_(old_reviews), ReviewFinding.code_excerpt.is_not(None))
        .values(code_excerpt=None)
    )
    raw = session.execute(delete(RawGitlab).where(RawGitlab.fetched_at < cutoff))
    evidence_cleared = 0
    if evidence_months is not None:
        oldest = _months_before((now or datetime.now(UTC)).date(), evidence_months)
        old_packs = select(EvidencePack.id).where(EvidencePack.period_start < oldest)
        session.execute(delete(EvidenceItem).where(EvidenceItem.pack_id.in_(old_packs)))
        cleared = session.execute(
            update(EvidencePack)
            .where(
                EvidencePack.period_start < oldest,
                (EvidencePack.self_notes.is_not(None))
                | (EvidencePack.lead_notes.is_not(None))
                | (EvidencePack.goals_next.is_not(None))
                | (EvidencePack.ai_draft.is_not(None)),
            )
            .values(self_notes=None, lead_notes=None, goals_next=None, ai_draft=None)
        )
        evidence_cleared = int(getattr(cleared, "rowcount", 0) or 0)
    notes = session.execute(
        delete(UserNotification).where(
            UserNotification.created_at
            < (now or datetime.now(UTC)) - timedelta(days=INBOX_KEEP_DAYS)
        )
    )
    result = PurgeResult(
        excerpts_cleared=int(getattr(excerpts, "rowcount", 0) or 0),
        raw_rows_deleted=int(getattr(raw, "rowcount", 0) or 0),
        evidence_cleared=evidence_cleared,
        notifications_deleted=int(getattr(notes, "rowcount", 0) or 0),
    )
    log.info("retention purge", extra={"cutoff": cutoff.isoformat(), **result.__dict__})
    return result


def purge_job() -> dict[str, int]:
    """Scheduler entry point (daily)."""
    from app.config import get_settings
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session, session.begin():
        settings = get_settings()
        result = purge(
            session, settings.retention_days, evidence_months=settings.evidence_retention_months
        )
    return result.__dict__
