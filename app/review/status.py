"""Review status transitions shared by the webhook handler and the review worker."""

from collections.abc import Iterable

from sqlalchemy import ColumnElement, and_, select, update
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


def mark_fixed_findings(session: Session, mr_id: int, latest_review_id: int) -> list[int]:
    """docs/specs/08: a critical/high finding is fixed once a later review of the same MR no
    longer reports its fingerprint. Returns the ids it fixed (their authors are told)."""
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
        .returning(ReviewFinding.id)
        .execution_options(synchronize_session=False)
    )
    return [int(i) for i in result.scalars()]


# A decision on a finding that holds for the same finding (fingerprint: file, category, code,
# title) re-reported by another review of the same MR or commit: a new push or a rerun reports
# it again as a new row, and the decision must not have to be made twice (user report
# 2026-11-04: "khiếu nại đã duyệt nhưng finding chưa đóng"). `fixed` is not carried: a finding
# reported again was not fixed.
CARRIED = ("false_positive", "accepted_risk")


def _same_target(review: Review) -> ColumnElement[bool]:
    if review.mr_id is not None:
        return and_(Review.target_type == "mr", Review.mr_id == review.mr_id)
    return and_(Review.target_type == "commit", Review.commit_sha == review.commit_sha)


def carry_decision(session: Session, finding: ReviewFinding) -> list[int]:
    """The finding was decided wrong (an accepted dispute, closed as "finding sai") or an
    accepted risk (lowered): its copies still open in the other reviews of the same MR / commit
    get the same status (and lowered severity). Returns the ids of the reviews changed, for the
    caller to re-score."""
    if finding.status not in CARRIED or not finding.fingerprint or finding.review_id is None:
        return []
    review = session.get(Review, finding.review_id)
    if review is None:
        return []
    values: dict[str, str] = {"status": finding.status}
    if finding.status == "accepted_risk":
        values["severity"] = finding.severity
    result = session.execute(
        update(ReviewFinding)
        .where(
            ReviewFinding.fingerprint == finding.fingerprint,
            ReviewFinding.status == "open",
            ReviewFinding.id != finding.id,
            ReviewFinding.review_id.in_(select(Review.id).where(_same_target(review))),
        )
        .values(**values)
        .returning(ReviewFinding.review_id)
        # "fetch": copies already loaded in this session see the new status, or the re-score
        # that follows would read them as still open.
        .execution_options(synchronize_session="fetch")
    )
    return sorted({int(rid) for rid in result.scalars() if rid is not None})


def decided_before(session: Session, review: Review) -> dict[str, tuple[str, str]]:
    """fingerprint -> (status, severity) of the findings already decided wrong / accepted in
    the other reviews of this MR / commit: a new review applies them to what it reports again.
    The newest decision wins."""
    rows = session.execute(
        select(ReviewFinding.fingerprint, ReviewFinding.status, ReviewFinding.severity)
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(
            _same_target(review),
            Review.id != review.id,
            ReviewFinding.status.in_(CARRIED),
            ReviewFinding.fingerprint.is_not(None),
        )
        .order_by(ReviewFinding.id)
    ).all()
    return {str(fp): (str(status), str(severity)) for fp, status, severity in rows}


def carry_all(session: Session, findings: Iterable[ReviewFinding]) -> set[int]:
    """`carry_decision` for many decided findings (the one-off repair of the data from before
    this rule, `python -m app.cli findings carry-decisions`)."""
    changed: set[int] = set()
    for finding in findings:
        changed.update(carry_decision(session, finding))
    return changed
