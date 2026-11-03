"""Dispute flow (docs/specs/01 F11, 09; task 5.2): dispute -> lead decides -> recompute."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session

from app.api.access import review_repo, review_scope, reviews_in_projects
from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.schemas import ORM
from app.api.security import CurrentPrincipal, Principal
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.models import FindingClosure, FindingDispute, Review, ReviewFinding, User
from app.models.review import CLOSURE_OPEN
from app.scoring.aggregate import aggregate, recompute_scores
from app.scoring.verdict import assess
from app.workers.dispute_jobs import running as ai_running

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
    created_at: datetime | None
    finding_title: str | None
    finding_severity: str | None
    finding_category: str | None
    finding_status: str | None
    finding_file_path: str | None
    finding_line_start: int | None
    review_id: int | None
    raised_by_name: str | None
    decided_by_name: str | None
    # The AI second look (POST /disputes/{id}/ai-check); only for who may decide.
    ai_status: str | None = None
    ai_verdict: str | None = None
    ai_confidence: float | None = None
    ai_reason: str | None = None
    ai_evidence: str | None = None
    ai_finished_at: datetime | None = None
    can_ai_check: bool = False


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
    # One flow at a time: a request to close it as fixed is being checked.
    pending_close = select(FindingClosure.id).where(
        FindingClosure.finding_id == finding.id, FindingClosure.status.in_(CLOSURE_OPEN)
    )
    if session.scalar(pending_close) is not None:
        raise HTTPException(409, "finding has a close request being checked")
    dispute = FindingDispute(finding_id=finding.id, raised_by=principal.user_id, reason=body.reason)
    finding.status = "disputed"
    session.add(dispute)
    session.flush()
    audit(session, principal.user_id, "dispute.raise", f"finding:{finding.id}")
    session.commit()
    return dispute


@router.get("/disputes", response_model=list[DisputeListItem])
def list_disputes(
    principal: CurrentPrincipal,
    session: SessionDep,
    pending: bool = True,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Your own disputes, plus those of the people you lead (all for admins). `user_id`: only
    the disputes that person raised (their member page's log), among those you may see."""
    query = select(FindingDispute).order_by(FindingDispute.id.desc())
    if pending:
        query = query.where(FindingDispute.decision.is_(None))
    if user_id is not None:
        query = query.where(FindingDispute.raised_by == user_id)
    # In SQL, before the limit: your own; those of the people you lead on reviews of dự án you
    # see; every one in a dự án you manage.
    if (allowed := review_scope(principal)) is not None:
        visible = principal.visible_users() or set()
        managed = principal.managed_projects() or set()
        seen: list[ColumnElement[bool]] = [
            FindingDispute.raised_by == principal.user_id,
            and_(FindingDispute.raised_by.in_(visible), allowed),
        ]
        if managed:
            seen.append(reviews_in_projects(managed))
        query = (
            query.outerjoin(ReviewFinding, ReviewFinding.id == FindingDispute.finding_id)
            .outerjoin(Review, Review.id == ReviewFinding.review_id)
            .where(or_(*seen))
        )
    return [_item(session, principal, d) for d in session.scalars(query.limit(200))]


def _recompute_for(session: Session, review: Review, settings: Settings) -> None:
    """Re-score the review and re-aggregate the week and month it belongs to."""
    moment = review.counted_at
    if moment is None:
        return
    tz = ZoneInfo(settings.report_tz)
    # [moment, moment + 1µs): exactly this review's moment (its change's time).
    threshold = settings.verify_min_confidence_unverified
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


def _item(session: Session, principal: Principal, dispute: FindingDispute) -> dict[str, Any]:
    finding = session.get(ReviewFinding, dispute.finding_id) if dispute.finding_id else None
    raiser = session.get(User, dispute.raised_by) if dispute.raised_by else None
    decider = session.get(User, dispute.decided_by) if dispute.decided_by else None
    return {
        **DisputeOut.model_validate(dispute).model_dump(),
        "created_at": dispute.created_at,
        "finding_title": finding.title if finding else None,
        "finding_severity": finding.severity if finding else None,
        "finding_category": finding.category if finding else None,
        "finding_status": finding.status if finding else None,
        "finding_file_path": finding.file_path if finding else None,
        "finding_line_start": finding.line_start if finding else None,
        "review_id": finding.review_id if finding else None,
        "raised_by_name": raiser.full_name if raiser else None,
        "decided_by_name": decider.full_name if decider else None,
        **_ai_view(principal, dispute),
    }


def _decider(principal: Principal, dispute: FindingDispute) -> bool:
    """Who may decide a dispute (and so ask the AI about it): the developer's lead or an
    admin, never the person who raised it."""
    return (
        dispute.raised_by is not None
        and dispute.raised_by != principal.user_id
        and (principal.is_admin or dispute.raised_by in principal.lead_members)
    )


def _ai_view(principal: Principal, dispute: FindingDispute) -> dict[str, Any]:
    """The AI second look, for who may decide only (the developer does not see it)."""
    if not _decider(principal, dispute):
        return {}
    live = ai_running(dispute)
    stuck = dispute.ai_status in ("queued", "running") and not live
    return {
        "ai_status": "failed" if stuck else dispute.ai_status,
        "ai_verdict": dispute.ai_verdict,
        "ai_confidence": float(dispute.ai_confidence)
        if dispute.ai_confidence is not None
        else None,
        "ai_reason": "Phân tích dừng giữa chừng (worker khởi động lại?); thử lại."
        if stuck
        else dispute.ai_reason,
        "ai_evidence": dispute.ai_evidence,
        "ai_finished_at": dispute.ai_finished_at,
        "can_ai_check": dispute.decision is None and not live,
    }


@router.post("/disputes/{dispute_id}/ai-check", response_model=DisputeListItem, status_code=202)
def ai_check(
    dispute_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, Any]:
    """Queue an AI second look at a pending dispute (app/review/dispute_check.py): the lead
    reads it, then decides as before; the AI never decides."""
    from app.admin_settings import monthly_budget_usd
    from app.workers.knowledge_jobs import month_spend_usd  # lazy: heavy module

    dispute = session.scalars(
        select(FindingDispute).where(FindingDispute.id == dispute_id).with_for_update()
    ).first()
    if dispute is None:
        raise HTTPException(404, "dispute not found")
    if not _decider(principal, dispute) or not _sees(session, principal, dispute):
        raise HTTPException(403, "only the developer's lead or an admin can ask for it")
    if dispute.decision is not None:
        raise HTTPException(409, "dispute already decided")
    if ai_running(dispute):
        raise HTTPException(409, "an analysis is already running")
    finding = session.get(ReviewFinding, dispute.finding_id) if dispute.finding_id else None
    review = session.get(Review, finding.review_id) if finding and finding.review_id else None
    repo = review_repo(session, review) if review else None
    if repo is None:
        raise HTTPException(404, "the finding's repo is gone")
    if repo.allow_external_llm is False:
        raise HTTPException(409, "this repo does not allow sending code to an external LLM")
    budget = monthly_budget_usd(session, settings)
    if budget > 0 and month_spend_usd(session) >= budget:
        raise HTTPException(409, "the month's LLM budget is used up")
    now = datetime.now(UTC)
    dispute.ai_status = "queued"
    dispute.ai_requested_by = principal.user_id
    dispute.ai_requested_at = now
    dispute.ai_verdict = dispute.ai_reason = dispute.ai_evidence = dispute.ai_model = None
    dispute.ai_confidence = dispute.ai_cost_usd = None
    dispute.ai_finished_at = None
    audit(session, principal.user_id, "dispute.ai_check", f"dispute:{dispute.id}")
    session.commit()
    try:
        queue.enqueue(
            "app.workers.dispute_jobs.dispute_check_job",
            dispute.id,
            now.isoformat(),
            job_id=f"dispute-check-{dispute.id}-{int(now.timestamp() * 1000)}",
            job_timeout=600,
        )
    except Exception:
        dispute.ai_status = "failed"
        dispute.ai_reason = "Không xếp hàng được; thử lại."
        dispute.ai_finished_at = datetime.now(UTC)
        session.commit()
        raise HTTPException(503, "could not queue the analysis") from None
    return _item(session, principal, dispute)


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
