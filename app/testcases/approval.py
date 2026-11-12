"""Deleting and approving test cases (docs/specs/30 §15.9, 30a G7).

`tests.delete` guards deleting cases, suites and sections; `tests.approve` guards the "approved"
status while the dự án's `require_approval` is on. Every write path calls `check_delete` before a
delete and `guard_status` before a case's status or content changes.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.models import TestCase, TestCaseReview, TestProjectSettings, TestSuite
from app.testcases import service as tc

APPROVED = "approved"
REVIEW = "review"


def may_delete(principal: Principal, bp_id: int | None) -> bool:
    return tc.may_view(principal, bp_id) and tc._scoped(principal, "tests.delete", bp_id)


def may_approve(principal: Principal, bp_id: int | None) -> bool:
    return tc.may_view(principal, bp_id) and tc._scoped(principal, "tests.approve", bp_id)


def check_delete(principal: Principal, bp_id: int | None) -> None:
    """403 unless `principal` may delete cases, suites, sections of dự án `bp_id`."""
    if not may_delete(principal, bp_id):
        raise HTTPException(403, "bạn không có quyền xoá testcase (tests.delete)")


def approval_on(session: Session, bp_id: int | None) -> bool:
    if bp_id is None:
        return False
    return bool(
        session.scalar(
            select(TestProjectSettings.require_approval).where(
                TestProjectSettings.business_project_id == bp_id
            )
        )
    )


def guard_status(
    session: Session,
    principal: Principal,
    bp_id: int | None,
    *,
    before: str | None,
    wanted: str | None,
    content_changed: bool,
    rule: bool | None = None,
    approver: bool | None = None,
) -> str | None:
    """The status a case ends with after a change, under the dự án's approval rule.

    `before`: its status now (None for a new case); `wanted`: the status asked (None: unchanged);
    `content_changed`: anything but the status changes. With approval off: `wanted` as asked.
    With it on: asking for "approved" needs tests.approve (403), and an approved case whose content
    changes goes back to "review" unless the one changing it may approve and asked to keep it.
    Returns the status to store (None: leave it). `rule` (`approval_on`) and `approver`
    (`may_approve`): already known by a caller checking many cases (an import); None: read here."""
    if not (approval_on(session, bp_id) if rule is None else rule):
        return wanted
    if approver is None:
        approver = may_approve(principal, bp_id)
    if wanted == APPROVED and before != APPROVED and not approver:
        raise HTTPException(403, "testcase cần người duyệt (tests.approve) mới đặt được Đã duyệt")
    if content_changed and before == APPROVED and wanted in (None, APPROVED):
        if wanted == APPROVED and approver:
            return APPROVED
        return REVIEW
    return wanted


def may_approve_suite(session: Session, principal: Principal, suite: TestSuite) -> bool:
    """`tests.approve` in one of the dự án the suite belongs to (home or shared with)."""
    return any(may_approve(principal, bp) for bp in tc.projects_of(session, suite))


def review(
    session: Session,
    case: TestCase,
    *,
    decision: str,
    comment: str | None,
    user_id: int | None,
    status: str,
) -> None:
    """Record one approval step about the case's current revision, then move it to `status`
    through the normal revision path (its history shows the change)."""
    session.add(
        TestCaseReview(
            case_id=case.id,
            decision=decision,
            comment=comment,
            revision=case.revision,
            user_id=user_id,
        )
    )
    tc.change(session, case, {"status": status}, user_id=user_id, via="editor")
