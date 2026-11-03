"""Re-score one review and the week and month it counts in, after a finding of it changed
(a dispute decided, a finding lowered or found wrong when closed)."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Review
from app.scoring.aggregate import aggregate, recompute_scores


def rescore_review(session: Session, review: Review, settings: Settings) -> None:
    moment = review.counted_at
    if moment is None:
        return
    tz = ZoneInfo(settings.report_tz)
    threshold = settings.verify_min_confidence_unverified
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
