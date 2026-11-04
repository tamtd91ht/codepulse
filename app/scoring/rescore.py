"""Re-score one review and the week and month it counts in, after a finding of it changed
(a dispute decided, a finding lowered or found wrong when closed)."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Review, ReviewFinding
from app.scoring.aggregate import aggregate, recompute_scores
from app.scoring.criteria_store import rescore_stored
from app.scoring.verdict import assess


def rescore_review(session: Session, review: Review, settings: Settings) -> None:
    threshold = settings.verify_min_confidence_unverified
    moment = review.counted_at
    if moment is None:
        # Counted nowhere, but its criteria scores still follow its findings (docs/specs/22
        # §4); otherwise recompute_scores below rescores them, at their stored versions.
        rescore_stored(session, review, settings.scoring_weights, threshold)
        return
    tz = ZoneInfo(settings.report_tz)
    # [moment, moment + 1us): exactly this review's moment (its change's time).
    recompute_scores(
        session,
        moment,
        moment + timedelta(microseconds=1),
        settings.scoring_weights,
        threshold,
        review_ids=[review.id],
    )
    day = moment.astimezone(tz).date()
    aggregate(session, "week", day, tz, settings.scoring_weights, settings.min_sample, threshold)
    aggregate(session, "month", day, tz, settings.scoring_weights, settings.min_sample, threshold)


def rescore_copies(session: Session, review_ids: list[int], settings: Settings) -> None:
    """Reviews whose findings took a decision made elsewhere (app/review/status.carry_decision):
    their merge verdict follows the findings that still count, then their scores and periods."""
    for review_id in review_ids:
        review = session.get(Review, review_id)
        if review is None:
            continue
        siblings = session.execute(
            select(ReviewFinding.severity, ReviewFinding.status, ReviewFinding.verified).where(
                ReviewFinding.review_id == review.id
            )
        ).all()
        review.verdict, review.risk_score = assess((str(s), st, v) for s, st, v in siblings)
        session.flush()
        rescore_review(session, review, settings)
