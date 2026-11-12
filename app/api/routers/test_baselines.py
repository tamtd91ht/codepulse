"""Baselines: suites frozen at a release, compare, runs from a baseline (docs/specs/30 §15.13,
§15.16, G9).

Who: `tests.view` on the baseline's dự án reads and compares; `tests.manage` on it creates and
deletes. A run is made from a baseline with POST /testcases/runs `baseline_id` (test_runs.py).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.routers.project_docs import _iso, _people
from app.api.security import CurrentPrincipal, Principal
from app.models import TestBaseline, TestMilestone, TestSuite
from app.testcases import baselines as bl
from app.testcases import runs as rs
from app.testcases import service as tc

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
NAME_MAX = 200
DESC_MAX = 10_000
NOT_FOUND = "baseline không tồn tại"


class BaselineIn(BaseModel):
    suite_id: int
    name: str = Field(min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    milestone_id: int | None = None
    section_ids: list[int] = Field(default_factory=list, max_length=500)


class BaselineOut(BaseModel):
    id: int
    business_project_id: int
    suite_id: int | None
    suite_name: str
    name: str
    description: str | None
    milestone_id: int | None
    milestone_name: str | None
    case_count: int
    created_by: int | None
    created_by_name: str | None
    created_at: str | None
    can_edit: bool = False


class BaselineCaseOut(BaseModel):
    id: int
    case_id: int | None
    key: str
    title: str
    section: str | None
    priority: str
    type: str


class BaselineDetail(BaselineOut):
    cases: list[BaselineCaseOut]


class CaseBrief(BaseModel):
    case_id: int | None
    key: str
    title: str | None
    section: str | None


class FieldChange(BaseModel):
    field: str
    before: Any = None
    after: Any = None


class CaseChange(CaseBrief):
    fields: list[FieldChange]


class CompareOut(BaseModel):
    a: BaselineOut
    b: BaselineOut | None  # None: the suite as it is now
    added: list[CaseBrief]
    removed: list[CaseBrief]
    changed: list[CaseChange]
    same: int
    # §15.16: `added` / `removed` / `changed` are one page [offset, offset + limit) of each list.
    total_added: int
    total_removed: int
    total_changed: int
    offset: int
    limit: int


def _outs(session: Session, principal: Principal, items: list[TestBaseline]) -> list[BaselineOut]:
    milestones = dict(
        session.execute(
            select(TestMilestone.id, TestMilestone.name).where(
                TestMilestone.id.in_({b.milestone_id for b in items if b.milestone_id} or {0})
            )
        ).all()
    )
    people = _people(session, {b.created_by for b in items})
    return [
        BaselineOut(
            id=b.id,
            business_project_id=b.business_project_id,
            suite_id=b.suite_id,
            suite_name=b.suite_name,
            name=b.name,
            description=b.description,
            milestone_id=b.milestone_id,
            milestone_name=milestones.get(b.milestone_id) if b.milestone_id else None,
            case_count=b.case_count,
            created_by=b.created_by,
            created_by_name=people[b.created_by].name if b.created_by in people else None,
            created_at=_iso(b.created_at),
            can_edit=tc.may_manage(principal, b.business_project_id),
        )
        for b in items
    ]


def _baseline_for(
    session: Session, principal: Principal, baseline_id: int, *, manage: bool = False
) -> TestBaseline:
    baseline = session.get(TestBaseline, baseline_id)
    if baseline is None or not tc.may_view(principal, baseline.business_project_id):
        raise HTTPException(404, NOT_FOUND)
    if manage and not tc.may_manage(principal, baseline.business_project_id):
        raise HTTPException(403, "bạn không có quyền quản lý baseline của dự án này")
    return baseline


@router.get("/testcases/baselines", response_model=list[BaselineOut])
def list_baselines(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    suite_id: int | None = None,
) -> list[BaselineOut]:
    bp = tc.project_for(session, principal, business_project_id)
    query = select(TestBaseline).where(TestBaseline.business_project_id == bp.id)
    if suite_id is not None:
        query = query.where(TestBaseline.suite_id == suite_id)
    items = list(
        session.scalars(query.order_by(TestBaseline.created_at.desc(), TestBaseline.id.desc()))
    )
    return _outs(session, principal, items)


@router.post("/testcases/baselines", response_model=BaselineDetail, status_code=201)
def create_baseline(
    body: BaselineIn, principal: CurrentPrincipal, session: SessionDep
) -> BaselineDetail:
    suite = tc.suite_for(session, principal, body.suite_id)
    if not tc.may_manage(principal, suite.business_project_id):
        raise HTTPException(403, "bạn không có quyền quản lý baseline của dự án này")
    name = rs.line(body.name)
    if not name:
        raise HTTPException(422, "tên không được trống")
    milestone_id = body.milestone_id
    if milestone_id is not None:
        m = session.get(TestMilestone, milestone_id)
        if m is None or m.business_project_id != suite.business_project_id:
            raise HTTPException(422, "milestone không tồn tại")
    baseline = bl.create(
        session,
        suite,
        name=name,
        description=rs.text(body.description),
        milestone_id=milestone_id,
        section_ids=body.section_ids or None,
        user_id=principal.user_id,
    )
    audit(
        session,
        principal.user_id,
        "baseline.create",
        f"baseline:{baseline.id}",
        business_project_id=baseline.business_project_id,
        suite_id=suite.id,
        name=baseline.name,
        cases=baseline.case_count,
    )
    session.commit()
    return _detail(session, principal, baseline)


def _detail(session: Session, principal: Principal, baseline: TestBaseline) -> BaselineDetail:
    [base] = _outs(session, principal, [baseline])
    return BaselineDetail(
        **base.model_dump(),
        cases=[
            BaselineCaseOut(
                id=c.id,
                case_id=c.case_id,
                key=c.key,
                title=c.title,
                section=c.section,
                priority=c.priority,
                type=c.type,
            )
            for c in bl.cases_of(session, baseline.id)
        ],
    )


@router.get("/testcases/baselines/compare", response_model=CompareOut)
def compare_baselines(
    principal: CurrentPrincipal,
    session: SessionDep,
    a: int,
    b: Annotated[str, Query(max_length=20, description="a baseline id, or `current`")],
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=bl.COMPARE_LIMIT_MAX)] = bl.COMPARE_LIMIT,
) -> CompareOut:
    first = _baseline_for(session, principal, a)
    if b.strip().lower() == "current":
        second = None
        if first.suite_id is None or session.get(TestSuite, first.suite_id) is None:
            raise HTTPException(422, "bộ testcase của baseline đã bị xoá")
        right = bl.side_of_current(session, first)
    else:
        try:
            other = int(b)
        except ValueError:
            raise HTTPException(422, "b: id baseline hoặc `current`") from None
        second = _baseline_for(session, principal, other)
        right = bl.side_of_baseline(session, second)
    result = bl.compare(bl.side_of_baseline(session, first), right, offset=offset, limit=limit)
    outs = _outs(session, principal, [first] + ([second] if second else []))
    return CompareOut(a=outs[0], b=outs[1] if second else None, **result)


@router.get("/testcases/baselines/{baseline_id:int}", response_model=BaselineDetail)
def get_baseline(
    baseline_id: int, principal: CurrentPrincipal, session: SessionDep
) -> BaselineDetail:
    return _detail(session, principal, _baseline_for(session, principal, baseline_id))


@router.delete("/testcases/baselines/{baseline_id:int}", status_code=204)
def delete_baseline(baseline_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    """Runs made from it keep their content (their tests hold their own snapshot)."""
    baseline = _baseline_for(session, principal, baseline_id, manage=True)
    audit(
        session,
        principal.user_id,
        "baseline.delete",
        f"baseline:{baseline.id}",
        business_project_id=baseline.business_project_id,
        name=baseline.name,
        cases=baseline.case_count,
    )
    session.delete(baseline)
    session.commit()
