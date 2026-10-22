"""Review status transitions shared by the webhook handler and the review worker."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import MergeRequest, Project, Review, ReviewFinding

ACTIVE_REVIEW_STATUSES = ("queued", "running", "done")


def supersede_mr_reviews(session: Session, project: Project, iid: int, head_sha: str) -> int:
    """New push to an MR: earlier reviews are kept for history but marked superseded."""
    mr_id = session.scalar(
        select(MergeRequest.id).where(
            MergeRequest.project_id == project.id, MergeRequest.iid == iid
        )
    )
    if mr_id is None:
        return 0
    result = session.execute(
        update(Review)
        .where(
            Review.target_type == "mr",
            Review.mr_id == mr_id,
            Review.reviewed_sha != head_sha,
            Review.status.in_(ACTIVE_REVIEW_STATUSES),
        )
        .values(status="superseded")
    )
    return int(getattr(result, "rowcount", 0) or 0)


SEVERE = ("critical", "high")


def mark_fixed_findings(session: Session, mr_id: int, latest_review_id: int) -> int:
    """docs/specs/08: a critical/high finding is fixed once a later review of the same MR no
    longer reports its fingerprint."""
    still_present = select(ReviewFinding.fingerprint).where(
        ReviewFinding.review_id == latest_review_id, ReviewFinding.fingerprint.is_not(None)
    )
    earlier = select(Review.id).where(
        Review.target_type == "mr", Review.mr_id == mr_id, Review.id != latest_review_id
    )
    result = session.execute(
        update(ReviewFinding)
        .where(
            ReviewFinding.review_id.in_(earlier),
            ReviewFinding.severity.in_(SEVERE),
            ReviewFinding.status == "open",
            ReviewFinding.fingerprint.not_in(still_present),
        )
        .values(status="fixed")
    )
    return int(getattr(result, "rowcount", 0) or 0)
