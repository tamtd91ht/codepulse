"""Testcase (docs/specs/30): suites and cases of a dự án, importing the team's Google Sheet,
exporting for a test run, coverage, and the cases merge requests call for.

Who: `docs.view` reads, `docs.edit` changes (app/testcases/service.py). A suite shared with
several dự án is read and edited from any of them; only its home deletes it.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.audit import audit
from app.api.deps import get_session
from app.api.routers.project_docs import Person, _iso, _people
from app.api.security import CurrentPrincipal, Principal
from app.assistant import importers
from app.assistant.knowledge import fold
from app.config import Settings, get_settings
from app.docs import service as docs
from app.jira.scopes import jira_issue_in
from app.models import (
    BusinessProject,
    Issue,
    MergeRequest,
    Project,
    Review,
    TestAiRun,
    TestCase,
    TestCaseRevision,
    TestSuite,
    TestSuiteLink,
)
from app.models.testcases import CASE_STATUSES, PRIORITIES
from app.testcases import impact as imp
from app.testcases import service as tc
from app.testcases import sheets

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
Priority = Literal["critical", "high", "medium", "low"]
Kind = Literal["manual", "automated"]
Status = Literal["draft", "approved", "obsolete"]
JIRA_DAYS = 90
REGRESSION_DAYS = 14
MAX_IMPORT_CELL = 20_000
MAX_IMPORT_CELLS = 300_000  # of one import, every tab together
MAX_IMPORT_CHARS = 20_000_000


# -- shapes ----------------------------------------------------------------------------------------


class ProjectRow(BaseModel):
    id: int
    name: str
    suites: int
    cases: int
    can_edit: bool


class SuiteIn(BaseModel):
    business_project_id: int
    name: str = Field(min_length=1, max_length=tc.NAME_MAX)
    description: str | None = Field(default=None, max_length=2000)


class SuitePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=tc.NAME_MAX)
    description: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None
    position: int | None = None


class SuiteOut(BaseModel):
    id: int
    business_project_id: int
    home_name: str
    name: str
    description: str | None
    source_url: str | None
    source_sheet: str | None
    projects: list[int]
    shared: bool
    archived: bool
    total: int
    approved: int
    draft: int
    obsolete: int
    can_edit: bool
    can_delete: bool
    updated_at: str | None


class ProjectsIn(BaseModel):
    project_ids: list[int] = Field(max_length=50)


class CaseFields(BaseModel):
    code: str | None = Field(default=None, max_length=tc.CODE_MAX)
    section: str | None = Field(default=None, max_length=tc.SECTION_MAX)
    title: str | None = Field(default=None, max_length=tc.TITLE_MAX)
    preconditions: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    steps: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    test_data: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    expected: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    notes: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    priority: Priority | None = None
    kind: Kind | None = None
    automation: str | None = Field(default=None, max_length=tc.AUTOMATION_MAX)
    tags: list[str] | None = Field(default=None, max_length=tc.LIST_MAX)
    jira_keys: list[str] | None = Field(default=None, max_length=tc.LIST_MAX)
    code_paths: list[str] | None = Field(default=None, max_length=tc.LIST_MAX)
    status: Status | None = None


class CaseIn(CaseFields):
    suite_id: int
    title: str = Field(min_length=1, max_length=tc.TITLE_MAX)


class CasePatch(CaseFields):
    revision: int  # the revision edited: another save since then is a conflict


class CaseOut(BaseModel):
    id: int
    suite_id: int
    code: str
    section: str | None
    title: str
    preconditions: str | None
    steps: str | None
    test_data: str | None
    expected: str | None
    notes: str | None
    priority: str
    kind: str
    automation: str | None
    tags: list[str]
    jira_keys: list[str]
    code_paths: list[str]
    status: str
    source: str
    revision: int
    problems: list[str]
    updated_at: str | None


class RevisionOut(BaseModel):
    number: int
    via: str
    changed_by: Person | None
    changed_at: str | None
    snapshot: dict[str, Any]


class CaseDetail(CaseOut):
    can_edit: bool
    created_by: Person | None
    updated_by: Person | None
    created_at: str | None
    revisions: list[RevisionOut]


class CaseList(BaseModel):
    items: list[CaseOut]
    total: int
    sections: list[str]
    tags: list[str]
    can_edit: bool


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["status", "priority", "move", "delete"]
    status: Status | None = None
    priority: Priority | None = None
    suite_id: int | None = None


class BulkOut(BaseModel):
    changed: int


class Uncovered(BaseModel):
    key: str
    title: str | None
    url: str | None


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    automated: int
    problems: int
    jira_issues: int
    jira_covered: int
    uncovered: list[Uncovered]


class UrlIn(BaseModel):
    business_project_id: int
    url: str = Field(min_length=10, max_length=2000)


class SheetOut(BaseModel):
    name: str
    header_row: int
    mapping: dict[str, int]
    rows: list[list[str]]
    total: int


class ReadOut(BaseModel):
    title: str
    source_url: str | None
    sheets: list[SheetOut]
    fields: dict[str, str]


class ImportSheet(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    suite_id: int | None = None
    suite_name: str | None = Field(default=None, max_length=tc.NAME_MAX)
    header_row: int = Field(ge=-1, le=sheets.HEADER_SCAN)
    mapping: dict[str, int]
    rows: list[list[str]] = Field(max_length=sheets.MAX_ROWS + sheets.HEADER_SCAN)


class ImportIn(BaseModel):
    business_project_id: int
    source_url: str | None = Field(default=None, max_length=2000)
    sheets: list[ImportSheet] = Field(min_length=1, max_length=sheets.MAX_SHEETS)


class ImportResult(BaseModel):
    suite_id: int
    name: str
    created: int
    updated: int
    unchanged: int
    skipped: int
    missing: int  # cases of the suite the sheet no longer has (kept)


class ImportOut(BaseModel):
    suites: list[ImportResult]


class ImpactCase(BaseModel):
    case: CaseOut
    suite: str
    reasons: list[str]
    ai_reason: str | None
    confidence: str | None


class AiBrief(BaseModel):
    id: int
    status: str
    error: str | None
    summary: str | None
    missing: int
    created_at: str | None
    finished_at: str | None


class MrImpactOut(BaseModel):
    mr_id: int
    iid: int
    title: str | None
    repo: str
    business_project_id: int | None
    merged_at: str | None
    target_branch: str | None
    files: int
    review_id: int | None
    cases: list[ImpactCase]
    ai: AiBrief | None
    can_edit: bool
    ai_allowed: bool


class RegressionMr(BaseModel):
    mr_id: int
    iid: int
    title: str | None
    repo: str
    merged_at: str | None
    target_branch: str | None
    files: int
    matched: int
    review_id: int | None
    ai: AiBrief | None


class RegressionOut(BaseModel):
    mrs: list[RegressionMr]
    cases: list[ImpactCase]
    branches: list[str]
    since: str
    until: str


# -- helpers ---------------------------------------------------------------------------------------


def _case_out(case: TestCase) -> CaseOut:
    return CaseOut(
        id=case.id,
        suite_id=case.suite_id,
        code=case.code,
        section=case.section,
        title=case.title,
        preconditions=case.preconditions,
        steps=case.steps,
        test_data=case.test_data,
        expected=case.expected,
        notes=case.notes,
        priority=case.priority,
        kind=case.kind,
        automation=case.automation,
        tags=list(case.tags or []),
        jira_keys=list(case.jira_keys or []),
        code_paths=list(case.code_paths or []),
        status=case.status,
        source=case.source,
        revision=case.revision,
        problems=tc.problems(case),
        updated_at=_iso(case.updated_at),
    )


def _suite_out(
    session: Session,
    principal: Principal,
    suite: TestSuite,
    counts: tc.SuiteCounts,
    names: dict[int, str],
    links: list[int] | None = None,
) -> SuiteOut:
    projects = (
        [suite.business_project_id, *links] if links is not None else tc.projects_of(session, suite)
    )
    return SuiteOut(
        id=suite.id,
        business_project_id=suite.business_project_id,
        home_name=names.get(suite.business_project_id, ""),
        name=suite.name,
        description=suite.description,
        source_url=suite.source_url,
        source_sheet=suite.source_sheet,
        projects=projects,
        shared=len(projects) > 1,
        archived=suite.archived_at is not None,
        total=counts.total,
        approved=counts.approved,
        draft=counts.draft,
        obsolete=counts.obsolete,
        can_edit=any(tc.may_edit(principal, p) for p in projects),
        can_delete=tc.may_edit(principal, suite.business_project_id),
        updated_at=_iso(suite.updated_at),
    )


def _names(session: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(
        session.execute(
            select(BusinessProject.id, BusinessProject.name).where(BusinessProject.id.in_(ids))
        ).all()
    )


def _clean(values: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    try:
        return tc.clean(values, partial=partial)
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None


def _project_suites(session: Session, bp_id: int, *, archived: bool = False) -> list[TestSuite]:
    query = select(TestSuite).where(tc.suite_in([bp_id]))
    query = query.where(
        TestSuite.archived_at.is_not(None) if archived else TestSuite.archived_at.is_(None)
    )
    return list(session.scalars(query.order_by(TestSuite.position, TestSuite.name)))


# -- dự án and suites ------------------------------------------------------------------------------


@router.get("/testcases/projects", response_model=list[ProjectRow])
def projects(principal: CurrentPrincipal, session: SessionDep) -> list[ProjectRow]:
    seen = docs.viewable_projects(session, principal)
    wanted = [bp.id for bp in seen] or [0]
    suites_of: dict[int, set[int]] = {}
    homes = session.execute(
        select(TestSuite.business_project_id, TestSuite.id).where(
            TestSuite.business_project_id.in_(wanted), TestSuite.archived_at.is_(None)
        )
    ).all()
    shared = session.execute(
        select(TestSuiteLink.business_project_id, TestSuiteLink.suite_id)
        .join(TestSuite, TestSuite.id == TestSuiteLink.suite_id)
        .where(TestSuiteLink.business_project_id.in_(wanted), TestSuite.archived_at.is_(None))
    ).all()
    for bp_id, suite_id in [*homes, *shared]:
        suites_of.setdefault(bp_id, set()).add(suite_id)
    all_suites = {s for ids in suites_of.values() for s in ids}
    per_suite = dict(
        session.execute(
            select(TestCase.suite_id, func.count())
            .where(TestCase.suite_id.in_(all_suites or {0}))
            .group_by(TestCase.suite_id)
        ).all()
    )
    return [
        ProjectRow(
            id=bp.id,
            name=bp.name,
            suites=len(suites_of.get(bp.id, ())),
            cases=sum(int(per_suite.get(s, 0)) for s in suites_of.get(bp.id, ())),
            can_edit=tc.may_edit(principal, bp.id),
        )
        for bp in seen
    ]


@router.get("/testcases/suites", response_model=list[SuiteOut])
def list_suites(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    archived: bool = False,
) -> list[SuiteOut]:
    bp = tc.project_for(session, principal, business_project_id)
    suites = _project_suites(session, bp.id, archived=archived)
    counts = tc.counts(session, [s.id for s in suites])
    links = tc.links_of(session, [s.id for s in suites])
    names = _names(session, {s.business_project_id for s in suites})
    return [_suite_out(session, principal, s, counts[s.id], names, links[s.id]) for s in suites]


@router.post("/testcases/suites", response_model=SuiteOut, status_code=201)
def create_suite(body: SuiteIn, principal: CurrentPrincipal, session: SessionDep) -> SuiteOut:
    bp = tc.project_for(session, principal, body.business_project_id, edit=True)
    top = session.scalar(
        select(func.max(TestSuite.position)).where(TestSuite.business_project_id == bp.id)
    )
    suite = TestSuite(
        business_project_id=bp.id,
        name=" ".join(body.name.split()),
        description=(body.description or "").strip() or None,
        position=int(top or 0) + 1,
        created_by=principal.user_id,
        updated_by=principal.user_id,
    )
    session.add(suite)
    session.flush()
    audit(session, principal.user_id, "testcase.suite.create", f"test_suite:{suite.id}")
    session.commit()
    return _suite_out(session, principal, suite, tc.SuiteCounts(), {bp.id: bp.name})


@router.patch("/testcases/suites/{suite_id}", response_model=SuiteOut)
def update_suite(
    suite_id: int, body: SuitePatch, principal: CurrentPrincipal, session: SessionDep
) -> SuiteOut:
    suite = tc.suite_for(session, principal, suite_id, edit=True)
    if body.name is not None:
        suite.name = " ".join(body.name.split())
    if body.description is not None:
        suite.description = body.description.strip() or None
    if body.position is not None:
        suite.position = body.position
    if body.archived is not None:
        suite.archived_at = datetime.now(UTC) if body.archived else None
    suite.updated_by = principal.user_id
    session.commit()
    counts = tc.counts(session, [suite.id])[suite.id]
    return _suite_out(
        session, principal, suite, counts, _names(session, {suite.business_project_id})
    )


@router.delete("/testcases/suites/{suite_id}", status_code=204)
def delete_suite(suite_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    """The suite and its cases, from its home dự án only (shared ones just stop the share)."""
    suite = tc.suite_for(session, principal, suite_id, edit=True)
    if not tc.may_edit(principal, suite.business_project_id):
        raise HTTPException(403, "chỉ dự án gốc của bộ testcase mới xoá được bộ này")
    total = tc.counts(session, [suite.id])[suite.id].total
    audit(
        session,
        principal.user_id,
        "testcase.suite.delete",
        f"test_suite:{suite.id}",
        name=suite.name,
        cases=total,
    )
    session.delete(suite)
    session.commit()


@router.put("/testcases/suites/{suite_id}/projects", response_model=SuiteOut)
def share_suite(
    suite_id: int, body: ProjectsIn, principal: CurrentPrincipal, session: SessionDep
) -> SuiteOut:
    """Share a suite with other dự án, or stop (the same rules as a document, docs/specs/29 §17)."""
    suite = tc.suite_for(session, principal, suite_id, edit=True)
    session.refresh(suite, with_for_update=True)
    home = suite.business_project_id
    current = set(tc.linked_ids(session, suite.id))
    wanted = {p for p in body.project_ids if p != home}
    if any(p not in current and not tc.may_view(principal, p) for p in wanted):
        raise HTTPException(403, "bạn không có quyền sửa testcase của một dự án được chọn")
    hidden = {p for p in current if not tc.may_view(principal, p)}
    wanted = {p for p in wanted if tc.may_view(principal, p)} | hidden
    added, dropped = wanted - current, current - wanted
    for bp_id in added:
        if session.get(BusinessProject, bp_id) is None:
            raise HTTPException(422, "dự án không tồn tại")
        if not tc.may_edit(principal, bp_id):
            raise HTTPException(403, "bạn không có quyền sửa testcase của một dự án được chọn")
    for bp_id in dropped:
        if not (tc.may_edit(principal, bp_id) or tc.may_edit(principal, home)):
            raise HTTPException(403, "bạn không có quyền bỏ liên kết với một dự án được chọn")
    for bp_id in added:
        session.add(
            TestSuiteLink(suite_id=suite.id, business_project_id=bp_id, linked_by=principal.user_id)
        )
    if dropped:
        session.execute(
            delete(TestSuiteLink).where(
                TestSuiteLink.suite_id == suite.id,
                TestSuiteLink.business_project_id.in_(dropped),
            )
        )
    if added or dropped:
        audit(
            session,
            principal.user_id,
            "testcase.suite.share",
            f"test_suite:{suite.id}",
            added=sorted(added),
            dropped=sorted(dropped),
        )
    session.commit()
    counts = tc.counts(session, [suite.id])[suite.id]
    projects_ = tc.projects_of(session, suite)
    return _suite_out(session, principal, suite, counts, _names(session, set(projects_)))


# -- cases -----------------------------------------------------------------------------------------


@router.get("/testcases", response_model=CaseList)
def list_cases(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    suite_id: int | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    priority: Priority | None = None,
    status: Status | None = None,
    kind: Kind | None = None,
    section: Annotated[str | None, Query(max_length=tc.SECTION_MAX)] = None,
    tag: Annotated[str | None, Query(max_length=tc.TAG_MAX)] = None,
    problem: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> CaseList:
    bp = tc.project_for(session, principal, business_project_id)
    if suite_id is not None:
        suite = tc.suite_for(session, principal, suite_id)
        if bp.id not in tc.projects_of(session, suite):
            raise HTTPException(404, "bộ testcase không thuộc dự án này")
        scope = [suite.id]
    else:
        scope = [s.id for s in _project_suites(session, bp.id)]
    base = select(TestCase).where(TestCase.suite_id.in_(scope or [0]))
    sections = sorted(
        {
            s
            for s in session.scalars(
                select(TestCase.section).where(TestCase.suite_id.in_(scope or [0])).distinct()
            )
            if s
        }
    )[:300]
    tags = sorted(
        set(
            session.scalars(
                select(func.jsonb_array_elements_text(TestCase.tags))
                .where(TestCase.suite_id.in_(scope or [0]))
                .distinct()
            )
        )
    )[:200]
    query = base
    for word in fold(q or "").split()[:8]:
        query = query.where(TestCase.search.contains(word, autoescape=True))
    if priority:
        query = query.where(TestCase.priority == priority)
    if status:
        query = query.where(TestCase.status == status)
    elif not q:
        query = query.where(TestCase.status != "obsolete")
    if kind:
        query = query.where(TestCase.kind == kind)
    if section:
        query = query.where(TestCase.section == section)
    if tag:
        query = query.where(TestCase.tags.contains([tag]))
    if problem:
        query = query.where(
            or_(
                func.coalesce(func.trim(TestCase.steps), "") == "",
                func.coalesce(func.trim(TestCase.expected), "") == "",
            )
        )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(TestCase.suite_id, TestCase.position, TestCase.id)
        .offset(offset)
        .limit(limit)
    )
    return CaseList(
        items=[_case_out(c) for c in rows],
        total=int(total),
        sections=sections,
        tags=tags,
        can_edit=tc.may_edit(principal, bp.id),
    )


@router.post("/testcases", response_model=CaseOut, status_code=201)
def create_case(body: CaseIn, principal: CurrentPrincipal, session: SessionDep) -> CaseOut:
    suite = tc.suite_for(session, principal, body.suite_id, edit=True)
    values = _clean(body.model_dump(exclude={"suite_id"}, exclude_none=True), partial=False)
    values.setdefault("status", "draft")
    session.refresh(suite, with_for_update=True)  # one code chooser at a time
    try:
        case = tc.create(session, suite, values, user_id=principal.user_id, source="manual")
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None
    session.commit()
    return _case_out(case)


def _detail(session: Session, principal: Principal, case: TestCase, suite: TestSuite) -> CaseDetail:
    revisions = list(
        session.scalars(
            select(TestCaseRevision)
            .where(TestCaseRevision.case_id == case.id)
            .order_by(TestCaseRevision.number.desc())
            .limit(50)
        )
    )
    people = _people(
        session, {case.created_by, case.updated_by, *(r.changed_by for r in revisions)}
    )
    return CaseDetail(
        **_case_out(case).model_dump(),
        can_edit=tc.may_edit_suite(session, principal, suite),
        created_by=people.get(case.created_by or 0),
        updated_by=people.get(case.updated_by or 0),
        created_at=_iso(case.created_at),
        revisions=[
            RevisionOut(
                number=r.number,
                via=r.via,
                changed_by=people.get(r.changed_by or 0),
                changed_at=_iso(r.changed_at),
                snapshot=r.snapshot,
            )
            for r in revisions
        ],
    )


@router.get("/testcases/{case_id:int}", response_model=CaseDetail)
def get_case(case_id: int, principal: CurrentPrincipal, session: SessionDep) -> CaseDetail:
    case, suite = tc.case_for(session, principal, case_id)
    return _detail(session, principal, case, suite)


@router.patch("/testcases/{case_id:int}", response_model=CaseDetail)
def update_case(
    case_id: int, body: CasePatch, principal: CurrentPrincipal, session: SessionDep
) -> CaseDetail:
    case, suite = tc.case_for(session, principal, case_id, edit=True)
    session.refresh(case, with_for_update=True)
    if body.revision != case.revision:
        raise HTTPException(
            409, "testcase vừa được người khác sửa; tải lại để xem bản mới rồi sửa tiếp"
        )
    values = _clean(body.model_dump(exclude={"revision"}, exclude_unset=True), partial=True)
    try:
        tc.change(session, case, values, user_id=principal.user_id, via="editor")
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None
    session.commit()
    return _detail(session, principal, case, suite)


@router.delete("/testcases/{case_id:int}", status_code=204)
def delete_case(case_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    case, _ = tc.case_for(session, principal, case_id, edit=True)
    audit(
        session,
        principal.user_id,
        "testcase.delete",
        f"test_case:{case.id}",
        code=case.code,
        title=case.title[:200],
    )
    session.delete(case)
    session.commit()


@router.post("/testcases/{case_id:int}/restore/{number}", response_model=CaseDetail)
def restore_case(
    case_id: int, number: int, principal: CurrentPrincipal, session: SessionDep
) -> CaseDetail:
    case, suite = tc.case_for(session, principal, case_id, edit=True)
    session.refresh(case, with_for_update=True)
    rev = session.scalar(
        select(TestCaseRevision).where(
            TestCaseRevision.case_id == case.id, TestCaseRevision.number == number
        )
    )
    if rev is None:
        raise HTTPException(404, "không có bản này trong lịch sử")
    values = _clean({k: v for k, v in rev.snapshot.items() if k in tc.FIELDS}, partial=True)
    try:
        tc.change(session, case, values, user_id=principal.user_id, via="restore")
    except tc.FieldError as exc:
        raise HTTPException(409, str(exc)) from None
    session.commit()
    return _detail(session, principal, case, suite)


@router.post("/testcases/bulk", response_model=BulkOut)
def bulk(body: BulkIn, principal: CurrentPrincipal, session: SessionDep) -> BulkOut:
    found = dict(
        session.execute(
            select(TestCase.id, TestCase.suite_id).where(TestCase.id.in_(body.ids))
        ).all()
    )
    if len(found) != len(set(body.ids)):
        raise HTTPException(404, "có testcase không còn")
    for suite_id in set(found.values()):
        suite = session.get(TestSuite, suite_id)
        if suite is None or not tc.may_view_suite(session, principal, suite):
            raise HTTPException(404, "có testcase không còn")
        if not tc.may_edit_suite(session, principal, suite):
            raise HTTPException(403, "có testcase bạn không có quyền sửa")
    cases = list(
        session.scalars(
            select(TestCase)
            .where(TestCase.id.in_(body.ids), TestCase.suite_id.in_(set(found.values())))
            .order_by(TestCase.id)
            .with_for_update()
        )
    )
    if len(cases) != len(found):
        raise HTTPException(409, "testcase vừa bị chuyển hoặc xoá; tải lại rồi thử lại")
    changed = 0
    if body.action == "delete":
        for case in cases:
            session.delete(case)
        changed = len(cases)
        audit(session, principal.user_id, "testcase.bulk_delete", None, ids=body.ids[:500])
    elif body.action == "move":
        if body.suite_id is None:
            raise HTTPException(422, "chọn bộ testcase đích")
        target = tc.suite_for(session, principal, body.suite_id, edit=True)
        session.refresh(target, with_for_update=True)
        codes = tc.Codes(session, target.id)
        position = tc.next_position(session, target.id)
        for case in cases:
            if case.suite_id == target.id:
                continue
            tc.record(session, case, user_id=principal.user_id, via="bulk")
            case.suite_id = target.id
            case.code = codes.free(case.code)
            case.position = position
            position += 1
            changed += 1
            session.flush()
    else:
        field_ = "status" if body.action == "status" else "priority"
        value = body.status if body.action == "status" else body.priority
        if value is None:
            raise HTTPException(422, "chọn giá trị")
        for case in cases:
            if tc.change(session, case, {field_: value}, user_id=principal.user_id, via="bulk"):
                changed += 1
    session.commit()
    return BulkOut(changed=changed)


# -- coverage --------------------------------------------------------------------------------------


@router.get("/testcases/stats", response_model=StatsOut)
def stats(principal: CurrentPrincipal, session: SessionDep, business_project_id: int) -> StatsOut:
    bp = tc.project_for(session, principal, business_project_id)
    scope = [s.id for s in _project_suites(session, bp.id)] or [0]
    by_status = {s: 0 for s in CASE_STATUSES}
    by_priority = {p: 0 for p in PRIORITIES}
    automated = problems = 0
    covered_keys: set[str] = set()
    missing = or_(
        func.coalesce(func.trim(TestCase.steps), "") == "",
        func.coalesce(func.trim(TestCase.expected), "") == "",
    )
    for status_, priority_, kind_, missed, n in session.execute(
        select(TestCase.status, TestCase.priority, TestCase.kind, missing, func.count())
        .where(TestCase.suite_id.in_(scope))
        .group_by(TestCase.status, TestCase.priority, TestCase.kind, missing)
    ):
        by_status[status_] += n
        if status_ == "obsolete":
            continue
        by_priority[priority_] += n
        automated += n if kind_ == "automated" else 0
        problems += n if missed else 0
    covered_keys.update(
        session.scalars(
            select(func.jsonb_array_elements_text(TestCase.jira_keys))
            .where(TestCase.suite_id.in_(scope), TestCase.status != "obsolete")
            .distinct()
        )
    )
    since = datetime.now(UTC) - timedelta(days=JIRA_DAYS)
    issues = session.execute(
        select(Issue.key, Issue.title, Issue.web_url)
        .where(
            Issue.source == "jira",
            Issue.parent_key.is_(None),
            Issue.created_at_src >= since,
            jira_issue_in([bp.id]),
        )
        .order_by(Issue.created_at_src.desc())
        .limit(2000)
    ).all()
    uncovered = [i for i in issues if i.key and i.key not in covered_keys]
    total_live = sum(v for k, v in by_status.items() if k != "obsolete")
    return StatsOut(
        total=total_live,
        by_status=by_status,
        by_priority=by_priority,
        automated=automated,
        problems=problems,
        jira_issues=len(issues),
        jira_covered=len(issues) - len(uncovered),
        uncovered=[Uncovered(key=i.key, title=i.title, url=i.web_url) for i in uncovered[:20]],
    )


# -- import / export -------------------------------------------------------------------------------


def _read_out(title: str, url: str | None, found: list[sheets.Sheet]) -> ReadOut:
    if not found:
        raise HTTPException(422, "không có dữ liệu trong bảng tính")
    return ReadOut(
        title=title,
        source_url=url,
        sheets=[
            SheetOut(
                name=s.name,
                header_row=s.header_row,
                mapping=s.mapping,
                rows=s.rows,
                total=max(0, len(s.rows) - s.header_row - 1),
            )
            for s in (sheets.guess(s) for s in found)
        ],
        fields={f: sheets.FIELD_LABEL[f] for f in sheets.MAP_FIELDS},
    )


@router.post("/testcases/import/read", response_model=ReadOut)
def read_url(
    body: UrlIn, principal: CurrentPrincipal, session: SessionDep, settings: SettingsDep
) -> ReadOut:
    """The tabs of a Google Sheet shared publicly, with the guessed columns. Nothing kept."""
    tc.project_for(session, principal, body.business_project_id, edit=True)
    session.commit()  # nothing held while the sheet downloads
    try:
        title, found = sheets.fetch_google(
            body.url.strip(), importers.hosts_of(settings.assistant_url_hosts)
        )
    except sheets.SheetError as exc:
        raise HTTPException(422, str(exc)) from None
    return _read_out(title, body.url.strip(), found)


@router.post("/testcases/import/read-file", response_model=ReadOut)
async def read_file(
    request: Request,
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> ReadOut:
    """The tabs of an uploaded .xlsx / .csv (raw bytes as the body). Nothing kept."""
    await run_in_threadpool(tc.project_for, session, principal, business_project_id, True)
    await run_in_threadpool(session.commit)
    limit = sheets.MAX_FILE_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"file lớn hơn {limit // (1024 * 1024)} MB")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, f"file lớn hơn {limit // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, "file rỗng")
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]

    def convert() -> ReadOut:
        try:
            found = sheets.read_file(name, bytes(data))
        except sheets.SheetError as exc:
            raise HTTPException(422, str(exc)) from None
        return _read_out(name.rsplit(".", 1)[0], None, found)

    return await run_in_threadpool(convert)


def _key(values: dict[str, Any]) -> str:
    return fold(f"{values.get('section') or ''}|{values.get('title') or ''}")


@router.post("/testcases/import", response_model=ImportOut)
def import_sheets(body: ImportIn, principal: CurrentPrincipal, session: SessionDep) -> ImportOut:
    """Each chosen tab into a suite (new, or one of the dự án): a case with the same code (or,
    without a code, the same section and title) is updated, a new one added; none is removed."""
    bp = tc.project_for(session, principal, body.business_project_id, edit=True)
    cells = sum(len(r) for s in body.sheets for r in s.rows)
    chars = sum(len(c) for s in body.sheets for r in s.rows for c in r)
    if cells > MAX_IMPORT_CELLS or chars > MAX_IMPORT_CHARS:
        raise HTTPException(413, "bảng tính quá lớn: chia thành nhiều lần nhập")
    for sheet in body.sheets:
        if any(f not in sheets.MAP_FIELDS for f in sheet.mapping):
            raise HTTPException(422, "cột ghép không hợp lệ")
        if any(len(r) > sheets.MAX_COLS for r in sheet.rows) or any(
            len(c) > MAX_IMPORT_CELL for r in sheet.rows for c in r
        ):
            raise HTTPException(422, "bảng tính quá lớn")
    results = []
    for sheet in body.sheets:
        try:
            mapped = sheets.to_cases(sheet.rows, sheet.header_row, sheet.mapping)
        except sheets.SheetError as exc:
            raise HTTPException(422, f"{sheet.name}: {exc}") from None
        if sheet.suite_id is not None:
            suite = tc.suite_for(session, principal, sheet.suite_id, edit=True)
            if bp.id not in tc.projects_of(session, suite):
                raise HTTPException(422, "bộ testcase không thuộc dự án này")
            session.refresh(suite, with_for_update=True)
        else:
            top = session.scalar(
                select(func.max(TestSuite.position)).where(TestSuite.business_project_id == bp.id)
            )
            suite = TestSuite(
                business_project_id=bp.id,
                name=" ".join((sheet.suite_name or sheet.name).split())[: tc.NAME_MAX]
                or "Testcase",
                position=int(top or 0) + 1,
                created_by=principal.user_id,
            )
            session.add(suite)
            session.flush()
        if body.source_url:
            suite.source_url = body.source_url.strip()
            suite.source_sheet = sheet.name
        suite.updated_by = principal.user_id
        existing = list(
            session.scalars(
                select(TestCase)
                .where(TestCase.suite_id == suite.id)
                .order_by(TestCase.position, TestCase.id)
            )
        )
        by_code = {c.code: c for c in existing}
        # Rows without a code: the cases of the same section and title, in order.
        by_text: dict[str, list[TestCase]] = {}
        for c in existing:
            by_text.setdefault(_key(tc.snapshot(c)), []).append(c)
        codes = tc.Codes(session, suite.id)
        seen: set[int] = set()
        created = updated = unchanged = 0
        position = tc.next_position(session, suite.id)
        for values in mapped.cases:
            code = values.pop("code", "") or ""
            if code:
                case = by_code.get(code)
            else:
                same = [c for c in by_text.get(_key(values), []) if id(c) not in seen]
                case = same[0] if same else None
            if case is not None and id(case) not in seen:
                seen.add(id(case))
                if code:
                    values["code"] = code
                if tc.change(session, case, values, user_id=principal.user_id, via="import"):
                    updated += 1
                else:
                    unchanged += 1
                continue
            values["code"] = code
            values.setdefault("status", "draft")
            new = tc.create(
                session,
                suite,
                values,
                user_id=principal.user_id,
                source="import",
                position=position,
                codes=codes,
                flush=False,
            )
            position += 1
            by_code[new.code] = new
            seen.add(id(new))
            created += 1
        session.flush()
        results.append(
            ImportResult(
                suite_id=suite.id,
                name=suite.name,
                created=created,
                updated=updated,
                unchanged=unchanged,
                skipped=mapped.skipped,
                missing=len([c for c in existing if id(c) not in seen and c.status != "obsolete"]),
            )
        )
        audit(
            session,
            principal.user_id,
            "testcase.import",
            f"test_suite:{suite.id}",
            sheet=sheet.name,
            created=created,
            updated=updated,
            source=bool(body.source_url),
        )
    session.commit()
    return ImportOut(suites=results)


def _download(
    groups: list[tuple[str, list[Any]]], name: str, fmt: str, extra: dict[int, str] | None = None
) -> Response:
    if fmt == "csv":
        data = sheets.write_csv(groups, extra)
        mime, ext = "text/csv; charset=utf-8", "csv"
    else:
        data = sheets.write_xlsx(groups, extra)
        mime, ext = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    from urllib.parse import quote

    safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_") or "testcase"
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="testcase.{ext}"; '
            f"filename*=UTF-8''{quote(safe.strip())}.{ext}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/testcases/export")
def export(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    suite_id: int | None = None,
    format: Literal["xlsx", "csv"] = "xlsx",
    include_obsolete: bool = False,
) -> Response:
    bp = tc.project_for(session, principal, business_project_id)
    if suite_id is not None:
        suite = tc.suite_for(session, principal, suite_id)
        if bp.id not in tc.projects_of(session, suite):
            raise HTTPException(404, "bộ testcase không thuộc dự án này")
        suites = [suite]
    else:
        suites = _project_suites(session, bp.id)
    groups = []
    for s in suites:
        query = select(TestCase).where(TestCase.suite_id == s.id)
        if not include_obsolete:
            query = query.where(TestCase.status != "obsolete")
        groups.append(
            (s.name, list(session.scalars(query.order_by(TestCase.position, TestCase.id))))
        )
    name = (
        f"Testcase {suites[0].name}" if suite_id is not None and suites else f"Testcase {bp.name}"
    )
    return _download(groups, name, format)


# -- merge requests --------------------------------------------------------------------------------


def _ai_brief(run: TestAiRun | None) -> AiBrief | None:
    if run is None:
        return None
    result = run.result or {}
    return AiBrief(
        id=run.id,
        status=run.status,
        error=run.error,
        summary=result.get("summary"),
        missing=len(result.get("missing") or []),
        created_at=_iso(run.created_at),
        finished_at=_iso(run.finished_at),
    )


def _review_ids(
    session: Session, principal: Principal, mr_ids: list[int], bp_id: int
) -> dict[int, int]:
    """The newest review of each MR, for those whose author's work one may see (review results
    stay per author, docs/specs/09; the MR itself is listed for the testers)."""
    if not mr_ids:
        return {}
    out: dict[int, int] = {}
    for mr_id, review_id, author in session.execute(
        select(Review.mr_id, Review.id, Review.author_user_id)
        .where(Review.mr_id.in_(mr_ids))
        .order_by(Review.id.desc())
    ):
        if mr_id is not None and mr_id not in out:
            out[mr_id] = review_id if principal.can_see_work(author, bp_id) else 0
    return {k: v for k, v in out.items() if v}


def _impact_cases(
    session: Session, item: imp.MrImpact, cases: dict[int, TestCase], suites: dict[int, str]
) -> list[ImpactCase]:
    ids = list(dict.fromkeys([*item.matched, *item.ai_cases]))
    out = []
    for case_id in ids:
        case = cases.get(case_id)
        if case is None:
            continue
        ai = item.ai_cases.get(case_id) or {}
        out.append(
            ImpactCase(
                case=_case_out(case),
                suite=suites.get(case.suite_id, ""),
                reasons=item.matched.get(case_id, []),
                ai_reason=ai.get("reason"),
                confidence=ai.get("confidence"),
            )
        )
    out.sort(key=lambda c: (tc.PRIORITY_RANK.get(c.case.priority, 9), c.case.code))
    return out


def _lookup(session: Session, ids: set[int]) -> tuple[dict[int, TestCase], dict[int, str]]:
    cases = {c.id: c for c in session.scalars(select(TestCase).where(TestCase.id.in_(ids or {0})))}
    suites = dict(
        session.execute(
            select(TestSuite.id, TestSuite.name).where(
                TestSuite.id.in_({c.suite_id for c in cases.values()} or {0})
            )
        ).all()
    )
    return cases, suites


@router.get("/testcases/impact/mr/{mr_id}", response_model=MrImpactOut)
def mr_impact(mr_id: int, principal: CurrentPrincipal, session: SessionDep) -> MrImpactOut:
    mr = session.get(MergeRequest, mr_id)
    repo = session.get(Project, mr.project_id) if mr and mr.project_id else None
    bp_id = repo.business_project_id if repo else None
    if mr is None or repo is None or bp_id is None or not tc.may_view(principal, bp_id):
        raise HTTPException(404, "merge request không thuộc dự án nào bạn xem được")
    [item] = imp.for_mrs(session, [mr], [bp_id])
    latest = session.scalar(
        select(TestAiRun)
        .where(TestAiRun.kind == "impact", TestAiRun.merge_request_id == mr.id)
        .order_by(TestAiRun.created_at.desc())
        .limit(1)
    )
    cases, suites = _lookup(session, set(item.matched) | set(item.ai_cases))
    return MrImpactOut(
        mr_id=mr.id,
        iid=mr.iid,
        title=mr.title,
        repo=item.repo,
        business_project_id=bp_id,
        merged_at=_iso(mr.merged_at),
        target_branch=mr.target_branch,
        files=len(item.paths),
        review_id=_review_ids(session, principal, [mr.id], bp_id).get(mr.id),
        cases=_impact_cases(session, item, cases, suites),
        ai=_ai_brief(latest),
        can_edit=tc.may_edit(principal, bp_id),
        ai_allowed=repo.allow_external_llm is not False,
    )


def _regression(
    session: Session,
    principal: Principal,
    bp_id: int,
    days: int,
    target_branch: str | None,
) -> tuple[RegressionOut, dict[int, str]]:
    bp = tc.project_for(session, principal, bp_id)
    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    mrs = imp.merged_mrs(session, bp.id, since, until, target_branch)
    impacts = imp.for_mrs(session, mrs, [bp.id])
    reasons = imp.regression(impacts)
    cases, suites = _lookup(session, set(reasons))
    reviews = _review_ids(session, principal, [m.id for m in mrs], bp.id)
    listed = []
    for case_id, why in reasons.items():
        case = cases.get(case_id)
        if case is None:
            continue
        listed.append(
            ImpactCase(
                case=_case_out(case),
                suite=suites.get(case.suite_id, ""),
                reasons=why,
                ai_reason=None,
                confidence=None,
            )
        )
    listed.sort(key=lambda c: (tc.PRIORITY_RANK.get(c.case.priority, 9), c.suite, c.case.code))
    branches = sorted(
        b
        for b in session.scalars(
            select(MergeRequest.target_branch)
            .join(Project, Project.id == MergeRequest.project_id)
            .where(Project.business_project_id == bp.id, MergeRequest.merged_at >= since)
            .distinct()
        )
        if b
    )
    out = RegressionOut(
        mrs=[
            RegressionMr(
                mr_id=i.mr.id,
                iid=i.mr.iid,
                title=i.mr.title,
                repo=i.repo,
                merged_at=_iso(i.mr.merged_at),
                target_branch=i.mr.target_branch,
                files=len(i.paths),
                matched=len(set(i.matched) | set(i.ai_cases)),
                review_id=reviews.get(i.mr.id),
                ai=_ai_brief(i.ai),
            )
            for i in impacts
        ],
        cases=listed,
        branches=branches,
        since=since.isoformat(),
        until=until.isoformat(),
    )
    return out, {c.case.id: " | ".join(c.reasons) for c in listed}


@router.get("/testcases/regression", response_model=RegressionOut)
def regression(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    days: Annotated[int, Query(ge=1, le=180)] = REGRESSION_DAYS,
    target_branch: Annotated[str | None, Query(max_length=255)] = None,
) -> RegressionOut:
    return _regression(session, principal, business_project_id, days, target_branch or None)[0]


@router.get("/testcases/regression/export")
def regression_export(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    days: Annotated[int, Query(ge=1, le=180)] = REGRESSION_DAYS,
    target_branch: Annotated[str | None, Query(max_length=255)] = None,
    format: Literal["xlsx", "csv"] = "xlsx",
) -> Response:
    out, why = _regression(session, principal, business_project_id, days, target_branch or None)
    cases, _ = _lookup(session, set(why))
    groups: dict[str, list[TestCase]] = {}
    for item in out.cases:
        case = cases.get(item.case.id)
        if case is not None:
            groups.setdefault(item.suite or "Testcase", []).append(case)
    return _download(list(groups.items()), f"Can test lai {days} ngay", format, why)
