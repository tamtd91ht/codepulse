"""Re-score one review and the week and month it counts in, after a finding of it changed
(a dispute decided, a finding lowered or found wrong when closed)."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Review
from app.scoring.aggregate import aggregate, recompute_scores
from app.scoring.criteria_store import rescore_stored


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
