"""Case approval: submit, approve, reject, history (docs/specs/30 §15.9, §15.11, G7).

Who: `tests.edit` sends a draft for review; `tests.approve` approves or rejects. With the dự án's
`require_approval` off the same endpoints still work (a plain status change by those who may).
Every step is kept in `test_case_reviews` and the status change goes through the case's history.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.routers.project_docs import _iso, _people
from app.api.routers.testcases import CaseDetail, _detail
from app.api.security import CurrentPrincipal, Principal
from app.models import TestCase, TestCaseReview, TestSuite
from app.testcases import approval
from app.testcases import events as tc_events
from app.testcases import service as tc

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
COMMENT_MAX = 2000


class ApproveIn(BaseModel):
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)


class RejectIn(BaseModel):
    comment: str = Field(min_length=1, max_length=COMMENT_MAX)


class ReviewOut(BaseModel):
    id: int
    decision: str
    comment: str | None
    revision: int
    user_id: int | None
    user_name: str | None
    created_at: str | None


def _comment(text: str | None) -> str | None:
    return (text or "").strip() or None


def _locked(
    session: Session, principal: Principal, case_id: int, *, edit: bool = False
) -> tuple[TestCase, TestSuite]:
    case, suite = tc.case_for(session, principal, case_id, edit=edit)
    session.refresh(case, with_for_update=True)  # one status change at a time
    return case, suite


def _approver(session: Session, principal: Principal, suite: TestSuite) -> None:
    if not approval.may_approve_suite(session, principal, suite):
        raise HTTPException(403, "bạn không có quyền duyệt testcase (tests.approve)")


def _move(
    session: Session,
    principal: Principal,
    case: TestCase,
    suite: TestSuite,
    *,
    decision: str,
    comment: str | None,
    status: str,
) -> CaseDetail:
    before = case.status
    approval.review(
        session, case, decision=decision, comment=comment, user_id=principal.user_id, status=status
    )
    tc_events.case_status_changed(session, suite.business_project_id, case.id, before, case.status)
    session.commit()
    return _detail(session, principal, case, suite)


@router.post("/testcases/{case_id:int}/submit", response_model=CaseDetail)
def submit(case_id: int, principal: CurrentPrincipal, session: SessionDep) -> CaseDetail:
    case, suite = _locked(session, principal, case_id, edit=True)
    if case.status != "draft":
        raise HTTPException(409, "chỉ gửi duyệt được testcase đang Nháp")
    return _move(
        session, principal, case, suite, decision="submit", comment=None, status=approval.REVIEW
    )


@router.post("/testcases/{case_id:int}/approve", response_model=CaseDetail)
def approve(
    case_id: int, body: ApproveIn, principal: CurrentPrincipal, session: SessionDep
) -> CaseDetail:
    case, suite = _locked(session, principal, case_id)
    _approver(session, principal, suite)
    if case.status not in ("draft", approval.REVIEW):
        raise HTTPException(409, "chỉ duyệt được testcase đang Nháp hoặc Chờ duyệt")
    return _move(
        session,
        principal,
        case,
        suite,
        decision="approve",
        comment=_comment(body.comment),
        status=approval.APPROVED,
    )


@router.post("/testcases/{case_id:int}/reject", response_model=CaseDetail)
def reject(
    case_id: int, body: RejectIn, principal: CurrentPrincipal, session: SessionDep
) -> CaseDetail:
    comment = _comment(body.comment)
    if comment is None:
        raise HTTPException(422, "từ chối cần ghi lý do")
    case, suite = _locked(session, principal, case_id)
    _approver(session, principal, suite)
    if case.status != approval.REVIEW:
        raise HTTPException(409, "chỉ từ chối được testcase đang Chờ duyệt")
    return _move(
        session, principal, case, suite, decision="reject", comment=comment, status="draft"
    )


@router.get("/testcases/{case_id:int}/reviews", response_model=list[ReviewOut])
def reviews(case_id: int, principal: CurrentPrincipal, session: SessionDep) -> list[ReviewOut]:
    case, _ = tc.case_for(session, principal, case_id)
    rows = list(
        session.scalars(
            select(TestCaseReview)
            .where(TestCaseReview.case_id == case.id)
            .order_by(TestCaseReview.created_at.desc(), TestCaseReview.id.desc())
        )
    )
    people = _people(session, {r.user_id for r in rows})
    return [
        ReviewOut(
            id=r.id,
            decision=r.decision,
            comment=r.comment,
            revision=r.revision,
            user_id=r.user_id,
            user_name=(p.name if (p := people.get(r.user_id or 0)) else None),
            created_at=_iso(r.created_at),
        )
        for r in rows
    ]
