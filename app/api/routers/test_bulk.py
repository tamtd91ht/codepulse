"""Bulk changes of cases, find and replace, undo (docs/specs/30 §15.10, §15.11, G10).

Who: `tests.edit` on every suite touched (a delete: `tests.delete`); undo: the one who made the
change or `tests.manage`, within 24 hours. The work is in app/testcases/bulk.py.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.routers.project_docs import _iso
from app.api.routers.testcases import CaseType, Priority
from app.api.security import CurrentPrincipal
from app.models import TestBulkOp, TestCase
from app.testcases import bulk as tb
from app.testcases import content
from app.testcases import service as tc

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
BulkStatus = Literal["draft", "review", "approved", "obsolete"]
ReplaceField = Literal["title", "preconditions", "steps", "expected", "notes", "test_data"]


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal[
        "status",
        "priority",
        "move",
        "delete",
        "section",
        "type",
        "milestone",
        "tags",
        "kind",
        "estimate",
        "jira",
        "custom",
        "copy",
    ]
    status: BulkStatus | None = None
    priority: Priority | None = None
    suite_id: int | None = None  # move, copy: the target suite
    section_id: int | None = None  # section: None = the suite's root; copy: the target folder
    type: CaseType | None = None
    milestone_id: int | None = None  # action "milestone": None = none
    tags_add: list[str] = Field(default_factory=list, max_length=tc.LIST_MAX)
    tags_remove: list[str] = Field(default_factory=list, max_length=tc.LIST_MAX)
    kind: Literal["manual", "automated"] | None = None
    estimate: str | None = Field(default=None, max_length=tc.ESTIMATE_MAX)  # "1h 30m"; empty: none
    jira_add: list[str] = Field(default_factory=list, max_length=tc.LIST_MAX)
    field_key: str | None = Field(default=None, max_length=100)
    value: Any = None  # action "custom": the field's value; None clears it


class SkipOut(BaseModel):
    case_id: int
    key: str
    reason: str


class BulkOut(BaseModel):
    changed: int
    batch_id: int | None = None  # None: nothing to undo (nothing changed, or a delete)
    skipped: list[SkipOut] = []


def _skips(items: list[tb.Skip]) -> list[SkipOut]:
    return [SkipOut(case_id=s.case_id, key=s.key, reason=s.reason) for s in items]


@router.post("/testcases/bulk", response_model=BulkOut)
def bulk(body: BulkIn, principal: CurrentPrincipal, session: SessionDep) -> BulkOut:
    params = body.model_dump(exclude={"ids", "action"})
    params["estimate"] = body.estimate or None
    got = tb.bulk(session, principal, body.ids, body.action, params)
    session.commit()
    return BulkOut(changed=got.changed, batch_id=got.batch_id, skipped=_skips(got.skipped))


# -- find and replace ------------------------------------------------------------------------------


class ReplaceIn(BaseModel):
    suite_id: int
    section_id: int | None = None  # only this folder and those below it
    find: str = Field(min_length=1, max_length=tb.REGEX_MAX)
    replace: str = Field(default="", max_length=tc.TEXT_MAX)
    fields: list[ReplaceField] = Field(min_length=1, max_length=len(tb.REPLACE_FIELDS))
    case_sensitive: bool = False
    regex: bool = False
    preview: bool = True


class ChangeOut(BaseModel):
    field: str
    before: str | None
    after: str | None


class MatchOut(BaseModel):
    case_id: int
    key: str
    title: str
    changes: list[ChangeOut]


class PreviewOut(BaseModel):
    total: int
    matches: list[MatchOut]
    # Something was left out: cases past 500, changes past 10 a case, or the size cap. The
    # before / after of each change are snippets around it; applying replaces everything.
    truncated: bool = False


class ReplacedOut(BaseModel):
    changed: int
    batch_id: int | None


@router.post("/testcases/replace", response_model=PreviewOut | ReplacedOut)
def replace(
    body: ReplaceIn, principal: CurrentPrincipal, session: SessionDep
) -> PreviewOut | ReplacedOut:
    """Find and replace in the text of a suite's cases (not obsolete ones): a preview of each
    case and change, or the change applied (one revision "bulk" a case, one undoable op)."""
    suite = tc.suite_for(session, principal, body.suite_id, edit=True)
    sub = tb.replacer(body.find, body.replace, regex=body.regex, case_sensitive=body.case_sensitive)
    query = select(TestCase).where(TestCase.suite_id == suite.id, TestCase.status != "obsolete")
    if body.section_id is not None:
        rows = content.section_rows(session, [suite.id])
        if body.section_id not in {s.id for s in rows}:
            raise HTTPException(422, "thư mục không thuộc bộ testcase này")
        query = query.where(TestCase.section_id.in_(content.descendants(rows, body.section_id)))
    query = query.order_by(TestCase.position, TestCase.id)
    fields = list(dict.fromkeys(body.fields))
    if body.preview:
        shown = tb.preview(tb.find_matches(session.scalars(query), fields, sub))
        return PreviewOut(
            total=shown.total,
            matches=[MatchOut.model_validate(m) for m in shown.matches],
            truncated=shown.truncated,
        )
    found = tb.find_matches(session.scalars(query.with_for_update()), fields, sub)
    if len(found) > tb.REPLACE_MAX:
        raise HTTPException(
            422, f"khớp {len(found)} testcase; tối đa {tb.REPLACE_MAX} một lần, hãy thu hẹp"
        )
    got = tb.replace_cases(session, principal, suite, found)
    session.commit()
    return ReplacedOut(changed=got.changed, batch_id=got.batch_id)


# -- the ops and undo ------------------------------------------------------------------------------


class OpOut(BaseModel):
    id: int
    kind: str
    summary: str
    count: int
    created_at: str | None
    undone_at: str | None
    can_undo: bool


class UndoOut(BaseModel):
    restored: int
    deleted: int
    skipped: list[SkipOut]


@router.get("/testcases/bulk-ops", response_model=list[OpOut])
def list_ops(
    business_project_id: int, principal: CurrentPrincipal, session: SessionDep
) -> list[OpOut]:
    """One's own last bulk changes in a dự án."""
    bp = tc.project_for(session, principal, business_project_id)
    now = datetime.now(UTC)
    rows = session.scalars(
        select(TestBulkOp)
        .where(TestBulkOp.business_project_id == bp.id, TestBulkOp.user_id == principal.user_id)
        .order_by(TestBulkOp.created_at.desc(), TestBulkOp.id.desc())
        .limit(tb.OPS_LISTED)
    )
    return [
        OpOut(
            id=op.id,
            kind=op.kind,
            summary=op.summary,
            count=len(op.before or {}) + len(op.created_ids or []),
            created_at=_iso(op.created_at),
            undone_at=_iso(op.undone_at),
            can_undo=tb.can_undo(op, now),
        )
        for op in rows
    ]


@router.post("/testcases/bulk-ops/{op_id}/undo", response_model=UndoOut)
@router.post("/testcases/bulk/{op_id}/undo", response_model=UndoOut, include_in_schema=False)
def undo(op_id: int, principal: CurrentPrincipal, session: SessionDep) -> UndoOut:
    got = tb.undo(session, principal, op_id)
    session.commit()
    return UndoOut(restored=got.restored, deleted=got.deleted, skipped=_skips(got.skipped))
