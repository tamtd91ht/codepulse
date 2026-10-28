"""Dispute flow (docs/specs/01 F11, 09; task 5.2): dispute -> lead decides -> recompute."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.access import review_repo, review_scope
from app.api.audit import audit
from app.api.deps import get_session
from app.api.schemas import ORM
from app.api.security import CurrentPrincipal, Principal
from app.config import Settings, get_settings
from app.models import FindingDispute, Review, ReviewFinding, User
from app.scoring.aggregate import aggregate, recompute_scores
from app.scoring.verdict import assess

router = APIRouter(tags=["disputes"])
SessionDep = Annotated[Session, Depends(get_session)]


class DisputeIn(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


class DecisionIn(BaseModel):
    decision: Literal["accepted", "rejected"]


class DisputeOut(ORM):
    id: int
    finding_id: int | None
    raised_by: int | None
    reason: str | None
    decision: str | None
    decided_by: int | None
    decided_at: datetime | None


class DisputeListItem(DisputeOut):
    finding_title: str | None
    finding_severity: str | None
    review_id: int | None
    raised_by_name: str | None


def _review_of(session: Session, finding: ReviewFinding) -> Review:
    review = session.get(Review, finding.review_id) if finding.review_id else None
    if review is None:
        raise HTTPException(404, "review not found")
    return review


def _sees(session: Session, principal: Principal, dispute: FindingDispute) -> bool:
    """A lead sees a member's dispute only in a dự án they are granted (docs/specs/09)."""
    if principal.is_admin or dispute.raised_by == principal.user_id:
        return True
    finding = session.get(ReviewFinding, dispute.finding_id) if dispute.finding_id else None
    review = session.get(Review, finding.review_id) if finding and finding.review_id else None
    repo = review_repo(session, review) if review else None
    return principal.can_see_work(dispute.raised_by, repo.business_project_id if repo else None)


@router.post("/findings/{finding_id}/dispute", response_model=DisputeOut, status_code=201)
def raise_dispute(
    finding_id: int, body: DisputeIn, principal: CurrentPrincipal, session: SessionDep
) -> FindingDispute:
    finding = session.get(ReviewFinding, finding_id)
    if finding is None:
        raise HTTPException(404, "finding not found")
    if _review_of(session, finding).author_user_id != principal.user_id:
        raise HTTPException(403, "only the author of the change can dispute its findings")
    if finding.status != "open":
        raise HTTPException(409, f"finding is {finding.status}")
    dispute = FindingDispute(finding_id=finding.id, raised_by=principal.user_id, reason=body.reason)
    finding.status = "disputed"
    session.add(dispute)
    session.flush()
    audit(session, principal.user_id, "dispute.raise", f"finding:{finding.id}")
    session.commit()
    return dispute


@router.get("/disputes", response_model=list[DisputeListItem])
def list_disputes(
    principal: CurrentPrincipal, session: SessionDep, pending: bool = True
) -> list[dict[str, Any]]:
    """Your own disputes, plus those of the people you lead (all for admins)."""
    query = select(FindingDispute).order_by(FindingDispute.id.desc())
    if pending:
        query = query.where(FindingDispute.decision.is_(None))
    visible = principal.visible_users()
    if visible is not None:
        query = query.where(FindingDispute.raised_by.in_(visible))
    # In SQL, before the limit: only disputes on reviews of dự án this person sees.
    if (allowed := review_scope(principal)) is not None:
        query = (
            query.outerjoin(ReviewFinding, ReviewFinding.id == FindingDispute.finding_id)
            .outerjoin(Review, Review.id == ReviewFinding.review_id)
            .where(or_(FindingDispute.raised_by == principal.user_id, allowed))
        )
    items = []
    for dispute in session.scalars(query.limit(200)):
        finding = session.get(ReviewFinding, dispute.finding_id) if dispute.finding_id else None
        raiser = session.get(User, dispute.raised_by) if dispute.raised_by else None
        items.append(
            {
                **DisputeOut.model_validate(dispute).model_dump(),
                "finding_title": finding.title if finding else None,
                "finding_severity": finding.severity if finding else None,
                "review_id": finding.review_id if finding else None,
                "raised_by_name": raiser.full_name if raiser else None,
            }
        )
    return items


def _recompute_for(session: Session, review: Review, settings: Settings) -> None:
    """Re-score the review and re-aggregate the week and month it belongs to."""
    if review.finished_at is None:
        return
    tz = ZoneInfo(settings.report_tz)
    moment = review.finished_at
    # [moment, moment + 1µs): exactly this review's finish time.
    threshold = settings.verify_min_confidence_unverified
    recompute_scores(
        session,
        moment,
        moment + timedelta(microseconds=1),
        settings.scoring_weights,
        threshold,
    )
    day = moment.astimezone(tz).date()
    aggregate(session, "week", day, tz, settings.scoring_weights, settings.min_sample, threshold)
    aggregate(session, "month", day, tz, settings.scoring_weights, settings.min_sample, threshold)


@router.post("/disputes/{dispute_id}/decide", response_model=DisputeOut)
def decide(
    dispute_id: int,
    body: DecisionIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FindingDispute:
    dispute = session.get(FindingDispute, dispute_id)
    if dispute is None:
        raise HTTPException(404, "dispute not found")
    if dispute.decision is not None:
        raise HTTPException(409, "dispute already decided")
    if dispute.raised_by == principal.user_id:
        raise HTTPException(403, "you cannot decide your own dispute")
    if dispute.raised_by is None or not (
        principal.is_admin or dispute.raised_by in principal.lead_members
    ):
        raise HTTPException(403, "only the developer's lead or an admin can decide")
    if not _sees(session, principal, dispute):
        raise HTTPException(403, "this dispute is in a dự án you are not granted")
    finding = session.get(ReviewFinding, dispute.finding_id) if dispute.finding_id else None
    if finding is None:
        raise HTTPException(404, "finding not found")

    dispute.decision = body.decision
    dispute.decided_by = principal.user_id
    dispute.decided_at = datetime.now(UTC)
    finding.status = "false_positive" if body.decision == "accepted" else "open"
    # The verdict follows the findings that still count (docs/specs/17).
    review = session.get(Review, finding.review_id) if finding.review_id else None
    if review is not None:
        siblings = session.execute(
            select(ReviewFinding.severity, ReviewFinding.status, ReviewFinding.verified).where(
                ReviewFinding.review_id == review.id
            )
        ).all()
        review.verdict, review.risk_score = assess((str(s), st, v) for s, st, v in siblings)
    session.flush()
    _recompute_for(session, _review_of(session, finding), settings)
    audit(
        session,
        principal.user_id,
        "dispute.decide",
        f"dispute:{dispute.id}",
        decision=body.decision,
        finding_id=finding.id,
    )
    session.commit()
    return dispute
