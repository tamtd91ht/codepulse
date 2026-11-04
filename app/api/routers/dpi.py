"""DPI: the monthly developer performance scorecard and its cycle (docs/specs/25).

Who sees what (§7): `dpi.view` over the person (own / team / project / all, `covers_person`);
`dpi.final_view` (HR) sees finalized and locked cards only; `dpi.distribution` sees counts per
rating, never people. Viewing someone else's scorecard is audited.
"""

import logging
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import mean, median, pstdev
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.personal import covers_person, person_scope, team_scope
from app.api.security import CurrentPrincipal, Principal
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.dpi import service
from app.dpi.rubric import (
    GROUPS,
    MANAGER_METRICS,
    RATINGS,
    validate_metrics,
    validate_weights,
)
from app.models import (
    DpiAssessment,
    DpiDispute,
    DpiMetricOverride,
    DpiPeerFeedback,
    DpiProfile,
    DpiReview,
    DpiRubric,
    DpiSnapshot,
    DpiUserProfile,
    Issue,
    MrNote,
    ProdDefect,
    ReviewComment,
    Team,
    TeamMember,
    User,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/dpi", tags=["dpi"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
MIN_GROUP = 5  # a distribution of fewer people would point at someone
MIN_PEER = 3  # fewer peer feedbacks than this: the person sees only that some exist


def parse_month(value: str | None) -> date:
    if not value:
        return service.previous_month(datetime.now(UTC).date())
    try:
        year, month = value[:7].split("-")
        return date(int(year), int(month), 1)
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM") from None


def _name(user: User | None) -> str | None:
    return (user.full_name or user.primary_email) if user else None


def _user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "person not found")
    return user


def _review(session: Session, user_id: int, month: date) -> DpiReview:
    review = session.scalars(
        select(DpiReview).where(DpiReview.user_id == user_id, DpiReview.month == month)
    ).first()
    if review is None:
        raise HTTPException(404, "no DPI review for this month yet")
    return review


def _finished(month: date) -> bool:
    return month < service.month_start(datetime.now(UTC).date())


def _acting_review(session: Session, user_id: int, month: date) -> DpiReview:
    """The review an action changes: only of a month that is over (§5, the cycle starts on the
    1st of the next month)."""
    if not _finished(month):
        raise HTTPException(409, "tháng chưa kết thúc: kỳ đánh giá mở từ ngày 1 tháng sau")
    return service.ensure_review(session, user_id, month)


def _only_final_view(session: Session, principal: Principal, user_id: int) -> bool:
    """HR (`dpi.final_view`) without any other right over this person."""
    return not (
        covers_person(session, principal, "dpi.view", user_id)
        or covers_person(session, principal, "dpi.calibrate", user_id)
    )


def _sees(session: Session, principal: Principal, user_id: int, review: DpiReview | None) -> bool:
    if covers_person(session, principal, "dpi.view", user_id):
        return True
    if covers_person(session, principal, "dpi.calibrate", user_id):
        return True
    return (
        principal.can("dpi.final_view") and review is not None and review.status in service.CLOSED
    )


def _assesses(session: Session, principal: Principal, user_id: int) -> bool:
    return user_id != principal.user_id and covers_person(session, principal, "dpi.assess", user_id)


def _calibrates(session: Session, principal: Principal, user_id: int) -> bool:
    return user_id != principal.user_id and covers_person(
        session, principal, "dpi.calibrate", user_id
    )


def _final_card(session: Session, review: DpiReview) -> dict[str, Any] | None:
    row = session.scalars(
        select(DpiSnapshot)
        .where(
            DpiSnapshot.user_id == review.user_id,
            DpiSnapshot.month == review.month,
            DpiSnapshot.kind == "final",
        )
        .order_by(DpiSnapshot.id.desc())
        .limit(1)
    ).first()
    return dict(row.values) if row else None


def _assessments(session: Session, review: DpiReview) -> list[dict[str, Any]]:
    users = {}
    rows = session.scalars(select(DpiAssessment).where(DpiAssessment.review_id == review.id)).all()
    if rows:
        users = {
            u.id: _name(u)
            for u in session.scalars(select(User).where(User.id.in_({r.by for r in rows if r.by})))
        }
    return [
        {
            "metric": a.metric,
            "score": float(a.score),
            "comment": a.comment,
            "evidence": a.evidence,
            "by": users.get(a.by) if a.by is not None else None,
            "updated_at": a.updated_at,
        }
        for a in rows
    ]


def _disputes(session: Session, review: DpiReview) -> list[dict[str, Any]]:
    return [
        {
            "id": d.id,
            "metric": d.metric,
            "reason": d.reason,
            "decision": d.decision,
            "answer": d.answer,
            "decided_at": d.decided_at,
            "created_at": d.created_at,
        }
        for d in service.open_disputes(session, review)
    ]


def _view(
    session: Session,
    settings: Settings,
    principal: Principal,
    user: User,
    month: date,
) -> dict[str, Any]:
    # Read-only: a month nobody acted on yet is computed, not stored (the job of the 1st and
    # each state change store reviews and snapshots).
    review = service.find_review(session, user.id, month) or service.draft_review(
        session, user.id, month
    )
    if not _sees(session, principal, user.id, review):
        raise HTTPException(403, "không có quyền xem scorecard DPI của người này")
    hr_only = _only_final_view(session, principal, user.id)
    card = _final_card(session, review) if review.status in service.CLOSED else None
    if card is None:
        card = service.Calculator(session, month, settings).card(review)
    trend = service.history(session, user.id, month, final_only=hr_only)
    assess = _assesses(session, principal, user.id)
    calibrate = _calibrates(session, principal, user.id)
    own = user.id == principal.user_id
    finished = _finished(month)
    is_open = finished and review.status in service.OPEN
    rubric = service.review_rubric(session, review)
    today = datetime.now(UTC).date()
    lead_may_start = review.status in ("lead_review", "calibration") or (
        review.status in ("draft", "self_review")
        and today > service.deadlines(month)["self_review"]
    )
    if not own:
        audit(session, principal.user_id, "dpi.view", f"user:{user.id}", month=month.isoformat())
    return {
        "user": {"id": user.id, "name": _name(user), "email": user.primary_email},
        "month": month.isoformat(),
        "shadow": service.shadow(session),
        "review": {
            "id": review.id,
            "status": review.status,
            "profile": review.profile,
            "leave_days": float(review.leave_days or 0),
            "adjustment": float(review.adjustment or 0),
            "adjustment_reason": review.adjustment_reason,
            "action_plan": review.action_plan,
            "one_on_one_at": review.one_on_one_at,
            "submitted_at": review.submitted_at,
            "assessed_at": review.assessed_at,
            "finalized_at": review.finalized_at,
            "locked_at": review.locked_at,
            "deadlines": service.deadlines(month),
            "overdue": service.overdue(review, today),
        },
        "card": card,
        "trend": trend,
        "rolling": service.rolling_of(trend, card.get("total")),
        "finished": finished,
        "assessments": [
            {k: v for k, v in a.items() if k in ("metric", "score")} if hr_only else a
            for a in (_assessments(session, review) if review.id else [])
        ],
        "disputes": [] if hr_only or not review.id else _disputes(session, review),
        "peer": {"count": 0, "average": None, "items": []}
        if hr_only
        else service.peer_summary(session, user.id, month, named=assess or calibrate),
        "b5_suggestions": service.b5_suggestions(session, review, card) if assess else [],
        "manager_metrics": [
            {"key": m.key, "label": m.label, "max": m.max}
            for m in rubric.metrics
            if m.source == "manager"
        ],
        "can": {
            "self": own
            and finished
            and review.status in ("draft", "self_review")
            and principal.can("dpi.self"),
            "dispute": own and is_open and principal.can("dpi.dispute"),
            "assess": assess and is_open and lead_may_start,
            "calibrate": calibrate and is_open,
            "finalize": assess
            and finished
            and review.status in ("lead_review", "calibration")
            and bool(review.id)
            and service.can_assess_complete(session, review, rubric),
            "lock": principal.can("dpi.admin") and review.status == "finalized",
            "decide": (assess or calibrate) and is_open,
        },
    }


@router.get("/people/{user_id}")
def scorecard(
    user_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    month: str | None = None,
) -> dict[str, Any]:
    """R2 + R6: one person's scorecard for the month, drill-down included."""
    user = _user(session, user_id)
    out = _view(session, settings, principal, user, parse_month(month))
    session.commit()
    return out


class SubmitIn(BaseModel):
    leave_days: float = Field(default=0, ge=0, le=23)


@router.post("/people/{user_id}/months/{month}/submit")
def submit(
    user_id: int,
    month: str,
    body: SubmitIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """§5 self_review -> lead_review: the person sent their part (the monthly evidence pack)."""
    if user_id != principal.user_id or not principal.can("dpi.self"):
        raise HTTPException(403, "chỉ người được đánh giá mới gửi phần tự khai")
    review = _acting_review(session, user_id, parse_month(month))
    if review.status not in ("draft", "self_review"):
        raise HTTPException(409, f"kỳ đánh giá đang ở bước {review.status}")
    review.leave_days = Decimal(str(body.leave_days))
    review.status = "lead_review"
    review.submitted_at = datetime.now(UTC)
    audit(session, principal.user_id, "dpi.submit", f"dpi_review:{review.id}")
    out = _view(session, settings, principal, _user(session, user_id), review.month)
    session.commit()
    return out


class AssessmentItem(BaseModel):
    metric: str
    score: float = Field(ge=0)
    comment: str = Field(min_length=5, max_length=2000)
    evidence: list[str] = Field(min_length=1, max_length=10)


class AssessmentIn(BaseModel):
    items: list[AssessmentItem] = Field(min_length=1, max_length=10)


@router.put("/people/{user_id}/months/{month}/assessment")
def assess(
    user_id: int,
    month: str,
    body: AssessmentIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """§3: the lead's 30 points; each with a comment and at least one piece of evidence."""
    if not _assesses(session, principal, user_id):
        raise HTTPException(403, "chỉ lead của người này (hoặc người có quyền chấm) được chấm")
    review = _acting_review(session, user_id, parse_month(month))
    if review.status not in service.OPEN:
        raise HTTPException(409, "kỳ đánh giá đã chốt")
    if review.status in ("draft", "self_review"):
        # The developer's part comes first; past its deadline the lead may go on without it.
        if datetime.now(UTC).date() <= service.deadlines(review.month)["self_review"]:
            raise HTTPException(
                409, "chờ người được đánh giá gửi phần tự khai (hoặc qua hạn tự khai)"
            )
        review.status = "lead_review"
    rubric = service.review_rubric(session, review).by_key()
    for item in body.items:
        metric = rubric.get(item.metric)
        if metric is None or metric.source != "manager":
            raise HTTPException(422, f"{item.metric} không phải chỉ số quản lý chấm")
        if item.score > metric.max:
            raise HTTPException(422, f"{item.metric}: tối đa {metric.max:g} điểm")
        if not all(e.strip() for e in item.evidence):
            raise HTTPException(422, f"{item.metric}: cần ít nhất một bằng chứng")
        row = session.scalars(
            select(DpiAssessment).where(
                DpiAssessment.review_id == review.id, DpiAssessment.metric == item.metric
            )
        ).first()
        if row is None:
            row = DpiAssessment(review_id=review.id, metric=item.metric, evidence=[])
            session.add(row)
        row.score = Decimal(str(round(item.score, 2)))
        row.comment = item.comment.strip()
        row.evidence = [e.strip() for e in item.evidence if e.strip()]
        row.by = principal.user_id
    session.flush()
    review.assessed_by = principal.user_id
    review.assessed_at = datetime.now(UTC)
    if review.status == "lead_review" and service.can_assess_complete(
        session, review, service.review_rubric(session, review)
    ):
        review.status = "calibration"
    audit(
        session,
        principal.user_id,
        "dpi.assess",
        f"dpi_review:{review.id}",
        metrics=[i.metric for i in body.items],
    )
    out = _view(session, settings, principal, _user(session, user_id), review.month)
    session.commit()
    return out


class CalibrateIn(BaseModel):
    adjustment: float = Field(ge=-service.MAX_ADJUSTMENT, le=service.MAX_ADJUSTMENT)
    reason: str = Field(min_length=10, max_length=1000)


@router.post("/people/{user_id}/months/{month}/calibrate")
def calibrate(
    user_id: int,
    month: str,
    body: CalibrateIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """§5 calibration: ±10 points on the total, the reason required."""
    if not _calibrates(session, principal, user_id):
        raise HTTPException(403, "cần quyền hiệu chỉnh DPI")
    review = _review(session, user_id, parse_month(month))
    if review.status not in service.OPEN:
        raise HTTPException(409, "kỳ đánh giá đã chốt")
    review.adjustment = Decimal(str(round(body.adjustment, 2)))
    review.adjustment_reason = body.reason.strip()
    review.adjusted_by = principal.user_id
    audit(
        session,
        principal.user_id,
        "dpi.calibrate",
        f"dpi_review:{review.id}",
        adjustment=body.adjustment,
        reason=body.reason,
    )
    out = _view(session, settings, principal, _user(session, user_id), review.month)
    session.commit()
    return out


class FinalizeIn(BaseModel):
    one_on_one_at: date
    action_plan: str = Field(min_length=10, max_length=4000)


@router.post("/people/{user_id}/months/{month}/finalize")
def finalize(
    user_id: int,
    month: str,
    body: FinalizeIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """§5 finalized: after the 1:1, with the action plan for next month; a final snapshot."""
    if not _assesses(session, principal, user_id):
        raise HTTPException(403, "chỉ lead của người này được chốt")
    review = _review(session, user_id, parse_month(month))
    if review.status not in ("lead_review", "calibration"):
        raise HTTPException(409, f"kỳ đánh giá đang ở bước {review.status}")
    if not service.can_assess_complete(session, review, service.review_rubric(session, review)):
        raise HTTPException(409, "chưa chấm đủ 30 điểm quản lý")
    pending = [d for d in service.open_disputes(session, review) if d.decision is None]
    if pending:
        raise HTTPException(409, "còn khiếu nại chỉ số chưa trả lời")
    review.one_on_one_at = body.one_on_one_at
    review.action_plan = body.action_plan.strip()
    review.status = "finalized"
    review.finalized_by = principal.user_id
    review.finalized_at = datetime.now(UTC)
    card = service.Calculator(session, review.month, settings).card(review)
    service.snapshot(session, review, card, kind="final")
    session.flush()
    audit(session, principal.user_id, "dpi.finalize", f"dpi_review:{review.id}")
    out = _view(session, settings, principal, _user(session, user_id), review.month)
    session.commit()
    return out


@router.post("/people/{user_id}/months/{month}/lock")
def lock(
    user_id: int,
    month: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    if not principal.can("dpi.admin"):
        raise HTTPException(403, "chỉ admin DPI được khoá")
    review = _review(session, user_id, parse_month(month))
    if review.status != "finalized":
        raise HTTPException(409, "chỉ khoá được kỳ đã chốt")
    review.status = "locked"
    review.locked_at = datetime.now(UTC)
    audit(session, principal.user_id, "dpi.lock", f"dpi_review:{review.id}")
    out = _view(session, settings, principal, _user(session, user_id), review.month)
    session.commit()
    return out


class DisputeIn(BaseModel):
    metric: str
    reason: str = Field(min_length=10, max_length=2000)


@router.post("/people/{user_id}/months/{month}/disputes")
def raise_dispute(
    user_id: int,
    month: str,
    body: DisputeIn,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> dict[str, Any]:
    if user_id != principal.user_id or not principal.can("dpi.dispute"):
        raise HTTPException(403, "chỉ khiếu nại được chỉ số của chính mình")
    review = _review(session, user_id, parse_month(month))
    if review.status not in service.OPEN:
        raise HTTPException(409, "kỳ đánh giá đã chốt")
    if body.metric not in service.review_rubric(session, review).by_key():
        raise HTTPException(422, "chỉ số không có trong rubric")
    waiting = session.scalars(
        select(DpiDispute.id).where(
            DpiDispute.review_id == review.id,
            DpiDispute.metric == body.metric,
            DpiDispute.decision.is_(None),
        )
    ).first()
    if waiting is not None:
        raise HTTPException(409, "chỉ số này đã có khiếu nại đang chờ trả lời")
    dispute = DpiDispute(
        review_id=review.id,
        metric=body.metric,
        reason=body.reason.strip(),
        raised_by=principal.user_id,
    )
    session.add(dispute)
    session.flush()
    audit(session, principal.user_id, "dpi.dispute", f"dpi_review:{review.id}", metric=body.metric)
    session.commit()
    return {"id": dispute.id}


class DecideIn(BaseModel):
    decision: Literal["accepted", "rejected"]
    answer: str = Field(min_length=5, max_length=2000)
    value: float | None = Field(default=None, ge=0, le=1)  # the metric's new v when accepted


@router.post("/disputes/{dispute_id}/decide")
def decide_dispute(
    dispute_id: int, body: DecideIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    dispute = session.get(DpiDispute, dispute_id)
    if dispute is None:
        raise HTTPException(404, "dispute not found")
    review = session.get(DpiReview, dispute.review_id)
    if review is None:
        raise HTTPException(404, "review not found")
    if not (
        _assesses(session, principal, review.user_id)
        or _calibrates(session, principal, review.user_id)
    ):
        raise HTTPException(403, "không có quyền trả lời khiếu nại này")
    if dispute.decision is not None:
        raise HTTPException(409, "khiếu nại đã được trả lời")
    if review.status not in service.OPEN:
        raise HTTPException(409, "kỳ đánh giá đã chốt")
    metric = service.review_rubric(session, review).by_key().get(dispute.metric)
    if body.decision == "accepted" and metric is not None and metric.source == "auto":
        if body.value is None:
            raise HTTPException(422, "chấp nhận khiếu nại chỉ số tự động cần giá trị mới (0-1)")
        override = session.scalars(
            select(DpiMetricOverride).where(
                DpiMetricOverride.review_id == review.id,
                DpiMetricOverride.metric == dispute.metric,
            )
        ).first()
        if override is None:
            override = DpiMetricOverride(review_id=review.id, metric=dispute.metric)
            session.add(override)
        override.value = Decimal(str(round(body.value, 3)))
        override.reason = body.answer.strip()
        override.by = principal.user_id
    dispute.decision = body.decision
    dispute.answer = body.answer.strip()
    dispute.decided_by = principal.user_id
    dispute.decided_at = datetime.now(UTC)
    audit(
        session,
        principal.user_id,
        "dpi.dispute.decide",
        f"dpi_review:{review.id}",
        metric=dispute.metric,
        decision=body.decision,
        value=body.value,
    )
    session.commit()
    return {"id": dispute.id, "decision": dispute.decision}


@router.post("/comments/{note_id}/confirm")
def confirm_comment(
    note_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    confirmed: bool = True,
    label: Literal["actionable", "design", "correctness", "security", "testability"] | None = None,
) -> dict[str, Any]:
    """§4.5: a lead ticks a review comment as one that mattered (D1): a comment by someone
    else than the MR's author; its label stays unless the lead names a meaningful one."""
    from app.models import MergeRequest

    note = session.get(MrNote, note_id)
    if note is None or note.is_system:
        raise HTTPException(404, "comment not found")
    mr = session.get(MergeRequest, note.mr_id) if note.mr_id else None
    if mr is None or mr.author_account_id == note.author_account_id:
        raise HTTPException(422, "chỉ xác nhận comment review trên MR của người khác")
    from app.models import Account

    account = session.get(Account, note.author_account_id) if note.author_account_id else None
    if (
        account is None
        or account.user_id is None
        or not _assesses(session, principal, account.user_id)
    ):
        raise HTTPException(403, "chỉ lead của người viết comment được xác nhận")
    row = session.get(ReviewComment, note_id)
    if row is None:
        from app.dpi.facts import classify_rule

        row = ReviewComment(note_id=note_id, label=classify_rule(note.body), by="rule")
        session.add(row)
    if label is not None:
        row.label = label
    if confirmed and row.label not in (
        "actionable",
        "design",
        "correctness",
        "security",
        "testability",
    ):
        raise HTTPException(422, "comment này không thuộc loại có giá trị; chọn nhãn khi xác nhận")
    row.confirmed_by = principal.user_id if confirmed else None
    audit(session, principal.user_id, "dpi.comment.confirm", f"note:{note_id}", confirmed=confirmed)
    session.commit()
    return {"note_id": note_id, "confirmed": confirmed}


class PeerIn(BaseModel):
    to_user_id: int
    month: str
    score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


def _in_month(month: date) -> Any:
    """Team membership that overlaps the month."""
    end = service.next_month(month)
    return (TeamMember.joined_at < end) & (
        TeamMember.left_at.is_(None) | (TeamMember.left_at > month)
    )


def _teammates(session: Session, user_id: int, month: date) -> set[int]:
    teams = select(TeamMember.team_id).where(TeamMember.user_id == user_id, _in_month(month))
    return {
        int(u)
        for u in session.scalars(
            select(TeamMember.user_id).where(TeamMember.team_id.in_(teams), _in_month(month))
        )
    } - {user_id}


@router.get("/peer-feedback")
def peer_targets(
    principal: CurrentPrincipal, session: SessionDep, month: str | None = None
) -> dict[str, Any]:
    """D3: teammates one may give feedback to this month, and what one already gave."""
    m = parse_month(month)
    mates = _teammates(session, principal.user_id, m)
    given = {
        r.to_user_id: {"score": r.score, "comment": r.comment}
        for r in session.scalars(
            select(DpiPeerFeedback).where(
                DpiPeerFeedback.from_user_id == principal.user_id, DpiPeerFeedback.month == m
            )
        )
    }
    users = session.scalars(select(User).where(User.id.in_(mates), User.active.is_not(False)))
    return {
        "month": m.isoformat(),
        "people": [
            {"id": u.id, "name": _name(u), "given": given.get(u.id)}
            for u in sorted(users, key=lambda u: (_name(u) or "").lower())
        ],
    }


@router.post("/peer-feedback")
def give_peer_feedback(
    body: PeerIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    if body.to_user_id == principal.user_id:
        raise HTTPException(422, "không tự đánh giá mình ở đây")
    m = parse_month(body.month)
    if not _finished(m) and m != service.month_start(datetime.now(UTC).date()):
        raise HTTPException(422, "chỉ phản hồi cho tháng này hoặc tháng đã qua")
    if body.to_user_id not in _teammates(session, principal.user_id, m):
        raise HTTPException(403, "chỉ đánh giá được người cùng team")
    closed = service.find_review(session, body.to_user_id, m)
    if closed is not None and closed.status in service.CLOSED:
        raise HTTPException(409, "kỳ đánh giá của người này đã chốt")
    row = session.scalars(
        select(DpiPeerFeedback).where(
            DpiPeerFeedback.month == m,
            DpiPeerFeedback.from_user_id == principal.user_id,
            DpiPeerFeedback.to_user_id == body.to_user_id,
        )
    ).first()
    if row is None:
        row = DpiPeerFeedback(month=m, from_user_id=principal.user_id, to_user_id=body.to_user_id)
        session.add(row)
    row.score = body.score
    row.comment = (body.comment or "").strip() or None
    session.commit()
    return {"ok": True}


# -- R1 / distribution / calibration -------------------------------------------------------------


def _latest_cards(session: Session, month: date, people: set[int] | None) -> dict[int, DpiSnapshot]:
    """Each person's latest snapshot of the month, the final one first (DISTINCT ON)."""
    query = (
        select(DpiSnapshot)
        .where(DpiSnapshot.month == month)
        .distinct(DpiSnapshot.user_id)
        .order_by(DpiSnapshot.user_id, (DpiSnapshot.kind == "final").desc(), DpiSnapshot.id.desc())
    )
    if people is not None:
        query = query.where(DpiSnapshot.user_id.in_(people or {0}))
    return {row.user_id: row for row in session.scalars(query)}


def _team_people(session: Session, team_id: int, month: date | None = None) -> set[int]:
    """The team's people in `month` (today when None)."""
    if month is None:
        today = datetime.now(UTC).date()
        when = (TeamMember.left_at.is_(None)) | (TeamMember.left_at > today)
    else:
        when = _in_month(month)
    return {
        int(u)
        for u in session.scalars(
            select(TeamMember.user_id).where(TeamMember.team_id == team_id, when)
        )
    }


def _metric(row: DpiSnapshot, key: str, field: str = "value") -> Any:
    return ((row.values.get("metrics") or {}).get(key) or {}).get(field)


def _aggregates(rows: list[DpiSnapshot]) -> dict[str, Any]:
    totals = [float(r.total) for r in rows if r.total is not None]
    rated = Counter(r.rating for r in rows if r.rating)
    c2 = [v for r in rows if (v := _metric(r, "C2")) is not None]
    gate = [
        d["pipeline_pass_rate"]
        for r in rows
        if (d := _metric(r, "B1", "detail")) and d.get("pipeline_pass_rate") is not None
    ]
    author_h = [
        d["author_hours_median"]
        for r in rows
        if (d := _metric(r, "C1", "detail")) and d.get("author_hours_median") is not None
    ]
    coverage = [float(r.values.get("coverage") or 0) for r in rows]
    return {
        "people": len(rows),
        "avg_total": round(mean(totals), 1) if totals else None,
        "ratings": {key: rated.get(key, 0) for _f, key, _l in RATINGS},
        "unrated": sum(1 for r in rows if not r.rating),
        "avg_delivery_predictability": round(100 * mean(c2), 1) if c2 else None,
        "avg_pipeline_pass_rate": round(mean(gate), 1) if gate else None,
        "median_author_hours": round(median(author_h), 1) if author_h else None,
        "avg_coverage": round(100 * mean(coverage), 1) if coverage else None,
    }


@router.get("/overview")
def overview(
    principal: CurrentPrincipal,
    session: SessionDep,
    month: str | None = None,
    team_id: int | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """R1: the month's DPI of the people one may see (latest snapshot each)."""
    m = parse_month(month)
    final_only = False
    people = person_scope(session, principal, "dpi.view")
    if people is not None and len(people) <= 1 and principal.can("dpi.final_view"):
        people, final_only = None, True
    if people is not None and not people:
        raise HTTPException(403, "không có quyền xem DPI")
    if team_id is not None:
        members = _team_people(session, team_id, m)
        people = members if people is None else people & members
    cards = _latest_cards(session, m, people)
    reviews = {r.user_id: r for r in session.scalars(select(DpiReview).where(DpiReview.month == m))}
    rows = []
    for user_id, snap in cards.items():
        review = reviews.get(user_id)
        if final_only and (review is None or review.status not in service.CLOSED):
            continue
        if profile and snap.profile != profile:
            continue
        rows.append((user_id, snap, review))
    users = {
        u.id: u
        for u in session.scalars(select(User).where(User.id.in_({r[0] for r in rows} or {0})))
    }
    return {
        "month": m.isoformat(),
        "shadow": service.shadow(session),
        "summary": _aggregates([s for _u, s, _r in rows]),
        "rows": sorted(
            (
                {
                    "user": {"id": uid, "name": _name(users.get(uid))},
                    "profile": snap.profile,
                    "total": float(snap.total) if snap.total is not None else None,
                    "rating": snap.rating,
                    "confidence": snap.confidence,
                    "provisional": bool(snap.values.get("provisional")),
                    "status": review.status if review else None,
                    "groups": {
                        g: (snap.values.get("groups") or {}).get(g, {}).get("ratio") for g in GROUPS
                    },
                    "split": snap.values.get("split"),
                    "flags": snap.values.get("flags"),
                }
                for uid, snap, review in rows
            ),
            key=lambda r: (r["user"]["name"] or "").lower(),
        ),
    }


@router.get("/distribution")
def distribution(
    principal: CurrentPrincipal, session: SessionDep, month: str | None = None
) -> dict[str, Any]:
    """§6.6: a manager sees how the month is spread per team, never who is where."""
    if not principal.can("dpi.distribution"):
        raise HTTPException(403, "cần quyền xem phân phối DPI")
    m = parse_month(month)
    teams = team_scope(principal, "dpi.distribution")
    query = select(Team)
    if teams is not None:
        query = query.where(Team.id.in_(teams or {0}))
    out = []
    listed = list(session.scalars(query.order_by(Team.name)))
    members: dict[int, set[int]] = defaultdict(set)
    for team_id, user_id in session.execute(
        select(TeamMember.team_id, TeamMember.user_id).where(
            TeamMember.team_id.in_([t.id for t in listed] or [0]), _in_month(m)
        )
    ):
        # The viewer is left out: knowing one's own score must not reveal the others'.
        if int(user_id) != principal.user_id:
            members[int(team_id)].add(int(user_id))
    everyone = set().union(*members.values()) if members else set()
    all_cards = _latest_cards(session, m, everyone)
    for team in listed:
        cards = [all_cards[u] for u in members.get(team.id, set()) if u in all_cards]
        agg = _aggregates(cards)
        hidden = agg["people"] < MIN_GROUP
        out.append(
            {
                "team": {"id": team.id, "name": team.name},
                "hidden": hidden,
                **({} if hidden else agg),
            }
        )
    return {"month": m.isoformat(), "shadow": service.shadow(session), "teams": out}


@router.get("/calibration")
def calibration(
    principal: CurrentPrincipal, session: SessionDep, month: str | None = None
) -> dict[str, Any]:
    """§5 calibration: the spread per team and per profile, teams far from the whole, and for
    each person the automatic part against the lead's part ("lệch so với lead")."""
    if not principal.can("dpi.calibrate"):
        raise HTTPException(403, "cần quyền hiệu chỉnh DPI")
    m = parse_month(month)
    people = person_scope(session, principal, "dpi.calibrate")
    cards = _latest_cards(session, m, people)
    totals = [float(s.total) for s in cards.values() if s.total is not None]
    overall = mean(totals) if totals else None
    spread = pstdev(totals) if len(totals) > 1 else 0.0
    membership: dict[int, list[int]] = defaultdict(list)
    today = datetime.now(UTC).date()
    for team_id, user_id in session.execute(
        select(TeamMember.team_id, TeamMember.user_id).where(
            TeamMember.user_id.in_(set(cards) or {0}),
            (TeamMember.left_at.is_(None)) | (TeamMember.left_at > today),
        )
    ):
        membership[int(team_id)].append(int(user_id))
    names = {
        t.id: t.name
        for t in session.scalars(select(Team).where(Team.id.in_(set(membership) or {0})))
    }
    teams = []
    for team_id, members in membership.items():
        values = [float(t) for u in members if (t := cards[u].total) is not None]
        avg = mean(values) if values else None
        off = (
            avg is not None
            and overall is not None
            and (abs(avg - overall) > 10 or (spread and abs(avg - overall) > spread))
        )
        teams.append(
            {
                "team": {"id": team_id, "name": names.get(team_id)},
                "people": len(members),
                "avg_total": round(avg, 1) if avg is not None else None,
                "off": bool(off),
            }
        )
    by_profile: dict[str, list[float]] = defaultdict(list)
    for s in cards.values():
        if s.total is not None:
            by_profile[s.profile].append(float(s.total))
    reviews = {r.user_id: r for r in session.scalars(select(DpiReview).where(DpiReview.month == m))}
    users = {u.id: u for u in session.scalars(select(User).where(User.id.in_(set(cards) or {0})))}
    rows = []
    for uid, snap in cards.items():
        split = snap.values.get("split") or {}
        auto_ratio = split.get("auto", 0) / split["auto_max"] if split.get("auto_max") else None
        manager_ratio = (
            split.get("manager", 0) / split["manager_max"]
            if split.get("manager_max") and not snap.values.get("provisional")
            else None
        )
        review = reviews.get(uid)
        rows.append(
            {
                "user": {"id": uid, "name": _name(users.get(uid))},
                "profile": snap.profile,
                "total": float(snap.total) if snap.total is not None else None,
                "rating": snap.rating,
                "confidence": snap.confidence,
                "status": review.status if review else None,
                "adjustment": float(review.adjustment or 0) if review else 0.0,
                "adjustment_reason": review.adjustment_reason if review else None,
                "gap_vs_lead": None
                if auto_ratio is None or manager_ratio is None
                else round(100 * (auto_ratio - manager_ratio), 1),
            }
        )
    return {
        "month": m.isoformat(),
        "shadow": service.shadow(session),
        "overall": {
            "people": len(cards),
            "avg_total": round(overall, 1) if overall is not None else None,
            "stdev": round(spread, 1),
        },
        "teams": sorted(teams, key=lambda t: t["team"]["name"] or ""),
        "profiles": {
            k: {"people": len(v), "avg_total": round(mean(v), 1)} for k, v in by_profile.items()
        },
        "rows": sorted(rows, key=lambda r: (r["user"]["name"] or "").lower()),
    }


@router.post("/run")
def run_now(
    principal: CurrentPrincipal,
    queue: Annotated[Enqueuer, Depends(get_queue)],
    month: str | None = None,
) -> dict[str, Any]:
    """Compute the month's scorecards now (the job of the 1st), in the background."""
    if not principal.can("dpi.admin"):
        raise HTTPException(403, "chỉ admin DPI")
    m = parse_month(month)
    queue.enqueue(
        "app.dpi.jobs.dpi_month_job",
        m.isoformat(),
        job_id=f"dpi-month-{m.isoformat()}-{int(datetime.now(UTC).timestamp())}",
        job_timeout=60 * 60,
    )
    return {"queued": True, "month": m.isoformat()}


class RebuildIn(BaseModel):
    months: int = Field(default=3, ge=1, le=6)
    resync: bool = True


def _rebuild_job(queue: Enqueuer) -> Any:
    fetch = getattr(queue, "fetch_job", None)
    if fetch is None:
        return None
    from app.dpi.jobs import REBUILD_JOB_ID

    try:
        return fetch(REBUILD_JOB_ID)
    except Exception:
        return None


@router.get("/rebuild")
def rebuild_status(
    principal: CurrentPrincipal, queue: Annotated[Enqueuer, Depends(get_queue)]
) -> dict[str, Any]:
    """The last "Chạy lại dữ liệu DPI": queued / started / finished / failed, and how far."""
    if not principal.can("dpi.admin"):
        raise HTTPException(403, "chỉ admin DPI")
    job = _rebuild_job(queue)
    if job is None:
        return {"status": "none"}
    state = job.get_status(refresh=True)
    meta = job.get_meta(refresh=True) if hasattr(job, "get_meta") else job.meta
    name = str(getattr(state, "value", state))
    return {
        "status": name,
        "progress": (meta or {}).get("progress"),
        "result": job.result if name == "finished" else None,
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
        "error": (str(job.exc_info or "").strip().splitlines() or [""])[-1][:300]
        if name == "failed"
        else None,
    }


@router.post("/rebuild", status_code=202)
def rebuild(
    body: RebuildIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, Any]:
    """Re-collect GitLab (optional) and compute the last months' scorecards, in the background."""
    if not principal.can("dpi.admin"):
        raise HTTPException(403, "chỉ admin DPI")
    from app.dpi.jobs import REBUILD_JOB_ID, REBUILD_TIMEOUT

    job = _rebuild_job(queue)
    if job is not None:
        state = str(getattr(job.get_status(refresh=True), "value", ""))
        if state in ("queued", "started", "deferred", "scheduled"):
            raise HTTPException(409, "đang chạy lại dữ liệu; chờ lượt này xong")
        try:
            job.delete()
        except Exception:  # the old run's record only
            log.warning("old dpi rebuild job not deleted")
    queue.enqueue(
        "app.dpi.jobs.dpi_rebuild_job",
        body.months,
        body.resync,
        job_id=REBUILD_JOB_ID,
        job_timeout=REBUILD_TIMEOUT,
        result_ttl=7 * 24 * 3600,
    )
    audit(session, principal.user_id, "dpi.rebuild", "dpi", months=body.months, resync=body.resync)
    session.commit()
    return {"queued": True, "months": body.months, "resync": body.resync}


# -- production defects (B2) --------------------------------------------------------------------


class RcaIn(BaseModel):
    status: Literal["confirmed", "process", "pending"]
    responsible: list[dict[str, float]] = Field(default_factory=list, max_length=10)
    process_gaps: list[str] = Field(default_factory=list, max_length=10)
    note: str | None = Field(default=None, max_length=2000)
    root_commit_sha: str | None = Field(default=None, max_length=64)
    root_mr_id: int | None = None


@router.get("/defects")
def defects(
    principal: CurrentPrincipal, session: SessionDep, status: str | None = None
) -> dict[str, Any]:
    if not principal.can("dpi.defects"):
        raise HTTPException(403, "cần quyền xác nhận RCA")
    from app.dpi.defects import Authors, suggest
    from app.models import Project

    query = (
        select(ProdDefect, Issue)
        .join(Issue, Issue.id == ProdDefect.issue_id)
        .join(Project, Project.id == Issue.project_id)
    )
    allowed = _defect_projects(principal)
    if allowed is not None:
        query = query.where(Project.business_project_id.in_(allowed or {0}))
    if status:
        query = query.where(ProdDefect.rca_status == status)
    rows = session.execute(query.order_by(Issue.created_at_src.desc()).limit(100)).all()
    in_scope = person_scope(session, principal, "dpi.defects")
    authors = Authors(session)  # one lookup per author across the page
    users = {
        u.id: _name(u)
        for u in session.scalars(
            select(User).where(
                User.id.in_(
                    {int(r["user_id"]) for d, _i in rows for r in d.responsible or []} | {0}
                )
            )
        )
    }
    return {
        "items": [
            {
                "id": d.id,
                "severity": d.severity,
                "issue": {
                    "id": i.id,
                    "iid": i.iid,
                    "title": i.title,
                    "url": i.web_url,
                    "created_at": i.created_at_src,
                    "state": i.state,
                },
                "rca_status": d.rca_status,
                "responsible": [
                    {
                        **r,
                        "name": users.get(int(r["user_id"]))
                        if in_scope is None or int(r["user_id"]) in in_scope
                        else None,
                    }
                    for r in d.responsible or []
                ],
                "process_gaps": d.process_gaps or [],
                "note": d.rca_note,
                "root_commit_sha": d.root_commit_sha,
                "root_mr_id": d.root_mr_id,
                "suggestions": [
                    s
                    if not s.get("author")
                    or in_scope is None
                    or int(s["author"]["user_id"]) in in_scope
                    else {**s, "author": None}
                    for s in suggest(session, d, i, authors)
                ],
            }
            for d, i in rows
        ]
    }


def _defect_projects(principal: Principal) -> set[int] | None:
    """Dự án whose production bugs one handles: the managed ones ("project"), every one
    granted ("team", "all"); None: all (admin). Repos outside every dự án are the admins'."""
    if principal.is_admin:
        return None
    held = principal.scope("dpi.defects")
    if held == "project":
        return principal.managed_projects() or set()
    return principal.visible_projects() or set()


@router.put("/defects/{defect_id}/rca")
def confirm_rca(
    defect_id: int, body: RcaIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    """§4.4: the lead confirms who carries a production bug (shares summing to 1), or that it
    was the process (nobody's points)."""
    from app.models import Project

    defect = session.get(ProdDefect, defect_id)
    if defect is None or not principal.can("dpi.defects"):
        raise HTTPException(404, "defect not found")
    issue = session.get(Issue, defect.issue_id)
    project = session.get(Project, issue.project_id) if issue else None
    allowed = _defect_projects(principal)
    if allowed is not None and (project is None or project.business_project_id not in allowed):
        raise HTTPException(404, "defect not found")
    responsible = [
        {"user_id": int(r.get("user_id", 0)), "share": float(r.get("share", 0))}
        for r in body.responsible
        if r.get("user_id")
    ]
    if len({r["user_id"] for r in responsible}) != len(responsible) or any(
        not 0 < r["share"] <= 1 for r in responsible
    ):
        raise HTTPException(422, "mỗi người một lần, tỉ lệ trong (0, 100%]")
    # Whoever is named now, and whoever carried it before, must be in one's scope.
    touched = {int(r["user_id"]) for r in responsible} | {
        int(r.get("user_id", 0)) for r in defect.responsible or [] if r.get("user_id")
    }
    for user_id in touched:
        if not covers_person(session, principal, "dpi.defects", user_id):
            raise HTTPException(403, "chỉ quy trách nhiệm cho người trong phạm vi quyền")
    if body.status == "confirmed":
        total = sum(r["share"] for r in responsible)
        if not responsible or abs(total - 1.0) > 0.01:
            raise HTTPException(422, "tỉ lệ trách nhiệm phải cộng lại bằng 100%")
    defect.rca_status = body.status
    defect.responsible = responsible if body.status == "confirmed" else []
    defect.process_gaps = [g.strip() for g in body.process_gaps if g.strip()]
    defect.rca_note = (body.note or "").strip() or None
    defect.root_commit_sha = body.root_commit_sha
    defect.root_mr_id = body.root_mr_id
    defect.rca_by = principal.user_id
    defect.rca_at = datetime.now(UTC)
    audit(
        session,
        principal.user_id,
        "dpi.defect.rca",
        f"prod_defect:{defect.id}",
        status=body.status,
        responsible=responsible,
    )
    session.commit()
    return {"id": defect.id, "rca_status": defect.rca_status}


# -- settings (dpi.admin) ------------------------------------------------------------------------


@router.get("/settings")
def get_settings_view(principal: CurrentPrincipal, session: SessionDep) -> dict[str, Any]:
    if not (principal.can("dpi.admin") or principal.can("dpi.view")):
        raise HTTPException(403, "không có quyền DPI")
    rubric = service.active_rubric(session)
    versions = session.scalars(select(DpiRubric).order_by(DpiRubric.created_at.desc())).all()
    out: dict[str, Any] = {
        "shadow": service.shadow(session),
        "rubric": {
            "version": rubric.version,
            "metrics": [
                {"key": m.key, "group": m.group, "max": m.max, "source": m.source, "label": m.label}
                for m in rubric.metrics
            ],
        },
        "versions": [{"version": v.version, "active": v.active} for v in versions],
        "profiles": [
            {"key": p.key, "label": p.label, "weights": p.weights, "a1_target": p.a1_target}
            for p in session.scalars(select(DpiProfile).order_by(DpiProfile.position))
        ],
        "deadlines_days": service.DEADLINES,
        "can_admin": principal.can("dpi.admin"),
    }
    if principal.can("dpi.admin"):
        rows = session.execute(
            select(DpiUserProfile, User)
            .join(User, User.id == DpiUserProfile.user_id)
            .order_by(User.full_name, DpiUserProfile.effective_from.desc())
        ).all()
        out["user_profiles"] = [
            {
                "user": {"id": u.id, "name": _name(u)},
                "profile": p.profile,
                "effective_from": p.effective_from,
            }
            for p, u in rows
        ]
    return out


class ShadowIn(BaseModel):
    shadow: bool


@router.put("/settings/shadow")
def put_shadow(body: ShadowIn, principal: CurrentPrincipal, session: SessionDep) -> dict[str, Any]:
    if not principal.can("dpi.admin"):
        raise HTTPException(403, "chỉ admin DPI")
    service.set_shadow(session, body.shadow, principal.user_id)
    audit(session, principal.user_id, "dpi.shadow", "dpi", shadow=body.shadow)
    session.commit()
    return {"shadow": body.shadow}


class RubricIn(BaseModel):
    version: str = Field(min_length=3, max_length=40, pattern=r"^[a-z0-9][a-z0-9.\-]*$")
    metrics: list[dict[str, Any]] = Field(min_length=5, max_length=40)


@router.post("/settings/rubric")
def new_rubric(body: RubricIn, principal: CurrentPrincipal, session: SessionDep) -> dict[str, Any]:
    """A new rubric version, made active; the old ones stay (snapshots name theirs)."""
    if not principal.can("dpi.admin"):
        raise HTTPException(403, "chỉ admin DPI")
    if session.get(DpiRubric, body.version) is not None:
        raise HTTPException(409, "phiên bản đã tồn tại")
    if problems := validate_metrics(body.metrics):
        raise HTTPException(422, "; ".join(problems))
    for row in session.scalars(select(DpiRubric).where(DpiRubric.active.is_(True))):
        row.active = False
    session.add(
        DpiRubric(
            version=body.version, metrics=body.metrics, active=True, created_by=principal.user_id
        )
    )
    audit(session, principal.user_id, "dpi.rubric", f"dpi_rubric:{body.version}")
    session.commit()
    return {"version": body.version}


class ProfileIn(BaseModel):
    label: str = Field(min_length=2, max_length=60)
    weights: dict[str, float]
    a1_target: float = Field(gt=0, le=500)


@router.put("/settings/profiles/{key}")
def put_profile(
    key: str, body: ProfileIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    if not principal.can("dpi.admin"):
        raise HTTPException(403, "chỉ admin DPI")
    if problems := validate_weights(body.weights):
        raise HTTPException(422, "; ".join(problems))
    row = session.get(DpiProfile, key)
    if row is None:
        position = session.scalar(select(func.count()).select_from(DpiProfile)) or 0
        row = DpiProfile(key=key, position=int(position))
        session.add(row)
    row.label = body.label
    row.weights = {g: float(body.weights.get(g, 0)) for g in GROUPS}
    row.a1_target = body.a1_target
    audit(session, principal.user_id, "dpi.profile", f"dpi_profile:{key}", weights=row.weights)
    session.commit()
    return {"key": key}


class UserProfileIn(BaseModel):
    user_id: int
    profile: str
    effective_from: date


@router.put("/user-profiles")
def put_user_profile(
    body: UserProfileIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    if not principal.can("dpi.admin"):
        raise HTTPException(403, "chỉ admin DPI")
    if session.get(DpiProfile, body.profile) is None:
        raise HTTPException(422, "profile không tồn tại")
    _user(session, body.user_id)
    start = body.effective_from.replace(day=1)
    row = session.scalars(
        select(DpiUserProfile).where(
            DpiUserProfile.user_id == body.user_id, DpiUserProfile.effective_from == start
        )
    ).first()
    if row is None:
        row = DpiUserProfile(user_id=body.user_id, effective_from=start, profile=body.profile)
        session.add(row)
    row.profile = body.profile
    row.set_by = principal.user_id
    # Open reviews from that month take the new profile.
    for review in session.scalars(
        select(DpiReview).where(
            DpiReview.user_id == body.user_id,
            DpiReview.month >= start,
            DpiReview.status.in_(service.OPEN),
        )
    ):
        review.profile = body.profile
    audit(
        session,
        principal.user_id,
        "dpi.user_profile",
        f"user:{body.user_id}",
        profile=body.profile,
        effective_from=start.isoformat(),
    )
    session.commit()
    return {"user_id": body.user_id, "profile": body.profile, "effective_from": start}


# -- P0: data readiness ----------------------------------------------------------------------


@router.get("/readiness")
def readiness(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    days: int = Query(30, ge=7, le=180),
) -> dict[str, Any]:
    if not (principal.can("dpi.admin") or principal.can("admin.projects")):
        raise HTTPException(403, "chỉ admin")
    from app.dpi.readiness import repo_readiness

    since = datetime.now(UTC) - timedelta(days=days)
    return {"days": days, **repo_readiness(session, since)}


@router.get("/readiness/{project_id}/gitlab")
def readiness_gitlab(
    project_id: int, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    """Protected default branch and the standard labels, asked of GitLab now."""
    if not (principal.can("dpi.admin") or principal.can("admin.projects")):
        raise HTTPException(403, "chỉ admin")
    from app.dpi.readiness import gitlab_checks

    return gitlab_checks(session, project_id)


@router.post("/readiness/{project_id}/labels")
def create_labels(
    project_id: int, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    """P0.4: create the standard labels missing on the repo (needs a token with `api`)."""
    if not principal.can("admin.projects"):
        raise HTTPException(403, "cần quyền quản trị dự án và repo")
    from app.dpi.readiness import ensure_labels

    result = ensure_labels(session, project_id)
    audit(session, principal.user_id, "dpi.labels", f"project:{project_id}", **result)
    session.commit()
    return result


__all__ = ["MANAGER_METRICS", "router"]
