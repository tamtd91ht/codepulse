"""Testcase (docs/specs/30): suites and cases of a dự án, importing the team's Google Sheet,
exporting for a test run, coverage, and the cases merge requests call for.

Who: `docs.view` reads, `docs.edit` changes (app/testcases/service.py). A suite shared with
several dự án is read and edited from any of them; only its home deletes it.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.audit import audit
from app.api.deps import get_session
from app.api.routers.project_docs import Person, _iso, _people
from app.api.security import CurrentPrincipal, Principal
from app.assistant import importers
from app.assistant.knowledge import fold
from app.config import Settings, get_settings
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
    TestMilestone,
    TestSection,
    TestSharedStep,
    TestSuite,
    TestSuiteLink,
)
from app.models.testcases import CASE_STATUSES, CASE_TYPES, PRIORITIES
from app.testcases import approval, content, sheets
from app.testcases import ci as tc_ci
from app.testcases import events as tc_events
from app.testcases import fields as tf
from app.testcases import impact as imp
from app.testcases import service as tc
from app.testcases.runs import refresh_open_snapshots

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
Priority = Literal["critical", "high", "medium", "low"]
Kind = Literal["manual", "automated"]
Status = Literal["draft", "review", "approved", "obsolete"]
Template = Literal["text", "steps"]
CaseType = Literal[
    "acceptance",
    "accessibility",
    "automated",
    "compatibility",
    "destructive",
    "functional",
    "other",
    "performance",
    "regression",
    "security",
    "smoke",
    "usability",
]
if set(get_args(CaseType)) != set(CASE_TYPES):  # a type added in one place only
    raise RuntimeError("CaseType must name every CASE_TYPES entry")
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
    can_edit: bool  # tests.edit: design the cases
    can_run: bool  # tests.run: record results of any test
    can_manage: bool  # tests.manage: runs, plans, milestones, configurations
    can_delete: bool = False  # §15.9 tests.delete: cases, suites, sections
    can_approve: bool = False  # §15.9 tests.approve


class SuiteIn(BaseModel):
    business_project_id: int
    name: str = Field(min_length=1, max_length=tc.NAME_MAX)
    description: str | None = Field(default=None, max_length=2000)


class SuitePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=tc.NAME_MAX)
    description: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None
    position: int | None = Field(default=None, ge=0, le=1_000_000)
    # §15.2 G2: "{repo}:{glob}" or a bare glob, inherited by every case of the suite.
    code_paths: list[str] | None = Field(default=None, max_length=tc.LIST_MAX)


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
    review: int = 0  # §15.9 G7: waiting for approval
    can_edit: bool
    can_delete: bool
    updated_at: str | None
    code_paths: list[str] = []


class ProjectsIn(BaseModel):
    project_ids: list[int] = Field(max_length=50)


class CaseFields(BaseModel):
    """What the editor sends (docs/specs/30 §14.1): never a code (keys are C<id>; `code` is the
    sheet's own, set by imports only); an unknown field such as `code` is ignored."""

    section_id: int | None = None
    title: str | None = Field(default=None, max_length=tc.TITLE_MAX)
    template: Template | None = None
    preconditions: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    steps: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    test_data: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    expected: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    steps_list: list[dict[str, Any]] | None = Field(default=None, max_length=content.STEP_MAX)
    notes: str | None = Field(default=None, max_length=tc.TEXT_MAX)
    priority: Priority | None = None
    type: CaseType | None = None
    kind: Kind | None = None
    automation: str | None = Field(default=None, max_length=tc.AUTOMATION_MAX)
    # §15 G1: "classname.name" of the automated test in CI reports, unique in the suite.
    automation_id: str | None = Field(default=None, max_length=tc.AUTOMATION_MAX)
    tags: list[str] | None = Field(default=None, max_length=tc.LIST_MAX)
    jira_keys: list[str] | None = Field(default=None, max_length=tc.LIST_MAX)
    code_paths: list[str] | None = Field(default=None, max_length=tc.LIST_MAX)
    status: Status | None = None
    milestone_id: int | None = None
    estimate: str | None = Field(default=None, max_length=tc.ESTIMATE_MAX)
    # §15.8 G5: {key: value} of the dự án's case fields; on PATCH the keys given (null clears).
    custom: dict[str, Any] | None = Field(default=None, max_length=tf.FIELDS_MAX)


class CaseIn(CaseFields):
    suite_id: int
    title: str = Field(min_length=1, max_length=tc.TITLE_MAX)


class CasePatch(CaseFields):
    revision: int  # the revision edited: another save since then is a conflict


class CaseOut(BaseModel):
    id: int
    key: str  # C<id>
    suite_id: int
    code: str | None  # the sheet's code it was imported with ("Mã gốc")
    section_id: int | None
    section: str | None  # the section's path "A > B"
    title: str
    template: str
    preconditions: str | None
    steps: str | None
    test_data: str | None
    expected: str | None
    steps_list: list[dict[str, Any]]
    steps_view: list[dict[str, Any]]  # shared steps expanded
    notes: str | None
    priority: str
    type: str
    kind: str
    automation: str | None
    automation_id: str | None = None
    flaky: bool = False
    tags: list[str]
    jira_keys: list[str]
    code_paths: list[str]
    status: str
    source: str
    revision: int
    problems: list[str]
    estimate_s: int | None
    estimate: str | None
    milestone_id: int | None
    updated_at: str | None
    custom: dict[str, Any] = Field(default_factory=dict)  # §15.8 G5


class RevisionOut(BaseModel):
    number: int
    via: str
    changed_by: Person | None
    changed_at: str | None
    snapshot: dict[str, Any]


class CaseDetail(CaseOut):
    can_edit: bool
    # §15.9 G7: tests.delete, tests.approve, and whether the dự án requires approval.
    can_delete: bool = False
    can_approve: bool = False
    approval_on: bool = False
    created_by: Person | None
    updated_by: Person | None
    created_at: str | None
    revisions: list[RevisionOut]


class CaseList(BaseModel):
    items: list[CaseOut]
    total: int
    tags: list[str]
    can_edit: bool


class SectionOut(BaseModel):
    id: int
    suite_id: int
    parent_id: int | None
    name: str
    description: str | None
    position: int
    depth: int
    path: str
    cases: int  # directly inside, not obsolete
    total: int  # with every section below
    code_paths: list[str] = []  # §15.2 G2: inherited by its cases and sub-folders


class SectionIn(BaseModel):
    suite_id: int
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=tc.SECTION_MAX)
    description: str | None = Field(default=None, max_length=tc.SECTION_DESCRIPTION_MAX)


class SectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=tc.SECTION_MAX)
    description: str | None = Field(default=None, max_length=tc.SECTION_DESCRIPTION_MAX)
    parent_id: int | None = None  # given (even null): moved there
    position: int | None = Field(default=None, ge=0, le=1_000_000)
    code_paths: list[str] | None = Field(default=None, max_length=tc.LIST_MAX)


class SectionDeleted(BaseModel):
    sections: int
    cases: int


class Uncovered(BaseModel):
    key: str
    title: str | None
    url: str | None


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    automated: int
    # §15 G1 "% tự động": automated cases with a CI result in the last 30 days.
    automated_ci: int = 0
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


def _case_out(case: TestCase, paths: dict[int, str], shared: dict[int, TestSharedStep]) -> CaseOut:
    view = content.expand_steps(case.steps_list, shared) if case.template == "steps" else []
    return CaseOut(
        id=case.id,
        key=content.key("case", case.id) or "",
        suite_id=case.suite_id,
        code=case.code,
        section_id=case.section_id,
        section=paths.get(case.section_id) if case.section_id else None,
        title=case.title,
        template=case.template,
        preconditions=case.preconditions,
        steps=case.steps,
        test_data=case.test_data,
        expected=case.expected,
        steps_list=list(case.steps_list or []),
        steps_view=view,
        notes=case.notes,
        priority=case.priority,
        type=case.type,
        kind=case.kind,
        automation=case.automation,
        automation_id=case.automation_id,
        flaky=bool(case.flaky),
        tags=list(case.tags or []),
        jira_keys=list(case.jira_keys or []),
        code_paths=list(case.code_paths or []),
        status=case.status,
        source=case.source,
        revision=case.revision,
        problems=tc.problems(case, shared),
        estimate_s=case.estimate_s,
        estimate=content.format_estimate(case.estimate_s),
        milestone_id=case.milestone_id,
        updated_at=_iso(case.updated_at),
        custom=dict(case.custom or {}),
    )


def case_outs(session: Session, cases: list[TestCase]) -> list[CaseOut]:
    """Cases as shown, with their section paths and shared steps read once for all."""
    paths = content.section_paths(session, {c.suite_id for c in cases})
    shared = content.shared_map(
        session, [i for c in cases for i in content.shared_ids(c.steps_list)]
    )
    return [_case_out(c, paths, shared) for c in cases]


def _check(
    session: Session, suite: TestSuite, values: dict[str, Any], current: TestCase | None = None
) -> dict[str, Any]:
    try:
        return tc.check_refs(session, suite, values, current=current)
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None


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
        review=counts.review,
        can_edit=any(tc.may_edit(principal, p) for p in projects),
        # §15.9: deleting a suite needs tests.delete in its home dự án only (as DELETE does).
        can_delete=approval.may_delete(principal, suite.business_project_id),
        updated_at=_iso(suite.updated_at),
        code_paths=list(suite.code_paths or []),
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


def _custom(
    session: Session,
    suite: TestSuite,
    values: dict[str, Any],
    current: TestCase | None = None,
) -> None:
    """§15.8: `values["custom"]` checked against the home dự án's case fields for the editor: a
    new case takes the defaults, an edit merges the keys given into what the case has (null
    clears one), and a required field applicable to the suite and template must have a value
    (422 naming it), also on an edit that does not send `custom`."""
    template = values.get("template") or (current.template if current else None) or "text"
    try:
        values["custom"] = tf.clean_custom(
            session,
            suite.business_project_id,
            "case",
            values.get("custom"),
            suite_id=suite.id,
            template=template,
            partial=current is not None,
            current=dict(current.custom or {}) if current is not None else None,
            defaults=current is None,
            enforce_required=True,
        )
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None


def _content_changed(case: TestCase, values: dict[str, Any]) -> bool:
    return any(
        k not in tc.NOT_CONTENT and getattr(case, k) != v
        for k, v in values.items()
        if k in tc.FIELDS
    )


def _guard(
    session: Session,
    principal: Principal,
    suite: TestSuite,
    values: dict[str, Any],
    current: TestCase | None = None,
    *,
    rule: bool | None = None,
    approver: bool | None = None,
) -> None:
    """§15.9 G7: with the dự án's approval rule on, "approved" needs tests.approve (403) and a
    content change of an approved case sends it back to "review". `rule` / `approver`: see
    `approval.guard_status` (an import reads them once)."""
    before = current.status if current is not None else None
    wanted = values.get("status")
    changed = _content_changed(current, values) if current is not None else False
    status = approval.guard_status(
        session,
        principal,
        suite.business_project_id,
        before=before,
        wanted=wanted,
        content_changed=changed,
        rule=rule,
        approver=approver,
    )
    if status is not None:
        values["status"] = status


def _may_delete_suite(session: Session, principal: Principal, suite: TestSuite) -> bool:
    return any(approval.may_delete(principal, bp) for bp in tc.projects_of(session, suite))


def _check_delete(session: Session, principal: Principal, suite: TestSuite) -> None:
    """tests.delete in one of the dự án of the suite (cases, sections)."""
    if not _may_delete_suite(session, principal, suite):
        approval.check_delete(principal, suite.business_project_id)


def _project_suites(session: Session, bp_id: int, *, archived: bool = False) -> list[TestSuite]:
    query = select(TestSuite).where(tc.suite_in([bp_id]))
    query = query.where(
        TestSuite.archived_at.is_not(None) if archived else TestSuite.archived_at.is_(None)
    )
    return list(session.scalars(query.order_by(TestSuite.position, TestSuite.name)))


# -- dự án and suites ------------------------------------------------------------------------------


@router.get("/testcases/projects", response_model=list[ProjectRow])
def projects(principal: CurrentPrincipal, session: SessionDep) -> list[ProjectRow]:
    seen = tc.viewable_projects(session, principal)
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
            can_run=tc.may_run(principal, bp.id),
            can_manage=tc.may_manage(principal, bp.id),
            can_delete=approval.may_delete(principal, bp.id),
            can_approve=approval.may_approve(principal, bp.id),
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
    if body.code_paths is not None:
        try:
            suite.code_paths = tc.area_paths(session, suite, body.code_paths, suite.code_paths)
        except tc.FieldError as exc:
            raise HTTPException(422, str(exc)) from None
    suite.updated_by = principal.user_id
    session.commit()
    counts = tc.counts(session, [suite.id])[suite.id]
    return _suite_out(
        session, principal, suite, counts, _names(session, {suite.business_project_id})
    )


@router.delete("/testcases/suites/{suite_id}", status_code=204)
def delete_suite(suite_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    """The suite and its cases, from its home dự án only (shared ones just stop the share)."""
    suite = tc.suite_for(session, principal, suite_id)
    if not approval.may_delete(principal, suite.business_project_id):
        if _may_delete_suite(session, principal, suite):
            raise HTTPException(403, "chỉ dự án gốc của bộ testcase mới xoá được bộ này")
        approval.check_delete(principal, suite.business_project_id)  # §15.9: tests.delete
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
    request: Request,
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    suite_id: int | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    priority: Priority | None = None,
    status: Status | None = None,
    kind: Kind | None = None,
    section_id: int | None = None,
    type: CaseType | None = None,
    milestone_id: int | None = None,
    tag: Annotated[str | None, Query(max_length=tc.TAG_MAX)] = None,
    problem: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> CaseList:
    """`section_id`: the cases of that section and every one below it. `q` also finds a case by
    its key ("C12"). `f.<key>=<value>` (§15.8): a custom field of the dự án (of the suite's home
    dự án with `suite_id`) equals (dropdown, multi-select, checkbox, user, number, date) or
    contains (text, textarea, url) the value."""
    bp = tc.project_for(session, principal, business_project_id)
    fields_bp = bp.id
    if suite_id is not None:
        suite = tc.suite_for(session, principal, suite_id)
        if bp.id not in tc.projects_of(session, suite):
            raise HTTPException(404, "bộ testcase không thuộc dự án này")
        scope = [suite.id]
        fields_bp = suite.business_project_id
    else:
        scope = [s.id for s in _project_suites(session, bp.id)]
    wanted_fields = [(k[2:], v) for k, v in request.query_params.multi_items() if k[:2] == "f."]
    base = select(TestCase).where(TestCase.suite_id.in_(scope or [0]))
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
    case_key = content.case_id_of(q or "")
    if case_key is not None:
        query = query.where(TestCase.id == case_key)
    else:
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
    if type:
        query = query.where(TestCase.type == type)
    if milestone_id is not None:
        query = query.where(TestCase.milestone_id == milestone_id)
    if section_id is not None:
        section = session.get(TestSection, section_id)
        if section is None or section.suite_id not in scope:
            raise HTTPException(404, "thư mục không thuộc bộ testcase này")
        query = query.where(TestCase.section_id.in_(tc.section_scope(session, section)))
    if tag:
        query = query.where(TestCase.tags.contains([tag]))
    if problem:
        query = query.where(tc.problem_clause())
    if wanted_fields:
        live = {f.key: f for f in tf.rows(session, fields_bp, "case")}
        for key, value in wanted_fields[:20]:
            field = live.get(key)
            if field is None:
                raise HTTPException(422, f"không có trường “{key[:40]}”")
            if not value.strip():
                continue
            try:
                query = query.where(tf.filter_clause(field, value[:200]))
            except tc.FieldError as exc:
                raise HTTPException(422, str(exc)) from None
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        session.scalars(
            query.order_by(TestCase.suite_id, TestCase.position, TestCase.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return CaseList(
        items=case_outs(session, rows),
        total=int(total),
        tags=tags,
        can_edit=tc.may_edit(principal, bp.id),
    )


@router.post("/testcases", response_model=CaseOut, status_code=201)
def create_case(body: CaseIn, principal: CurrentPrincipal, session: SessionDep) -> CaseOut:
    suite = tc.suite_for(session, principal, body.suite_id, edit=True)
    values = _clean(body.model_dump(exclude={"suite_id"}, exclude_none=True), partial=False)
    values.setdefault("status", "draft")
    _check(session, suite, values)
    _custom(session, suite, values)
    _guard(session, principal, suite, values)
    case = tc.create(session, suite, values, user_id=principal.user_id, source="manual")
    session.commit()
    return case_outs(session, [case])[0]


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
        **case_outs(session, [case])[0].model_dump(),
        can_edit=tc.may_edit_suite(session, principal, suite),
        can_delete=_may_delete_suite(session, principal, suite),
        can_approve=approval.may_approve_suite(session, principal, suite),
        approval_on=approval.approval_on(session, suite.business_project_id),
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
    _check(session, suite, values, current=case)
    _custom(session, suite, values, current=case)
    _guard(session, principal, suite, values, current=case)
    before = case.status
    try:
        tc.change(session, case, values, user_id=principal.user_id, via="editor")
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None
    tc_events.case_status_changed(session, suite.business_project_id, case.id, before, case.status)
    session.commit()
    return _detail(session, principal, case, suite)


@router.delete("/testcases/{case_id:int}", status_code=204)
def delete_case(case_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    case, suite = tc.case_for(session, principal, case_id)
    _check_delete(session, principal, suite)  # §15.9: tests.delete
    audit(
        session,
        principal.user_id,
        "testcase.delete",
        f"test_case:{case.id}",
        key=content.key("case", case.id),
        title=case.title[:200],
    )
    refresh_open_snapshots(session, [case.id])  # open runs keep the case as it was
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
    try:
        values = tc.clean({k: v for k, v in rev.snapshot.items() if k in tc.RESTORED}, partial=True)
    except tc.FieldError as exc:
        raise HTTPException(409, str(exc)) from None
    # A section, milestone or shared step gone since: dropped rather than refused.
    tc.check_refs(session, suite, values, lenient=True)
    _guard(session, principal, suite, values, current=case)  # §15.9 G7
    before = case.status
    try:
        tc.change(session, case, values, user_id=principal.user_id, via="restore")
    except tc.FieldError as exc:
        raise HTTPException(409, str(exc)) from None
    tc_events.case_status_changed(session, suite.business_project_id, case.id, before, case.status)
    session.commit()
    return _detail(session, principal, case, suite)


# -- sections --------------------------------------------------------------------------------------


def _section_for(
    session: Session, principal: Principal, section_id: int, *, edit: bool = False
) -> tuple[TestSection, TestSuite]:
    section = session.get(TestSection, section_id)
    if section is None:
        raise HTTPException(404, "thư mục không tồn tại")
    try:
        suite = tc.suite_for(session, principal, section.suite_id, edit=edit)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, "thư mục không tồn tại") from None
        raise
    return section, suite


def _sections_out(session: Session, suite_id: int) -> list[SectionOut]:
    return [
        SectionOut(
            id=r.section.id,
            suite_id=r.section.suite_id,
            parent_id=r.section.parent_id,
            name=r.section.name,
            description=r.section.description,
            position=r.section.position,
            depth=r.depth,
            path=r.path,
            cases=r.cases,
            total=r.total,
            code_paths=list(r.section.code_paths or []),
        )
        for r in tc.section_tree(session, suite_id)
    ]


def _one_section(session: Session, section: TestSection) -> SectionOut:
    return next(s for s in _sections_out(session, section.suite_id) if s.id == section.id)


@router.get("/testcases/sections", response_model=list[SectionOut])
def list_sections(
    principal: CurrentPrincipal, session: SessionDep, suite_id: int
) -> list[SectionOut]:
    """The suite's section tree in reading order (each parent, then its sections)."""
    suite = tc.suite_for(session, principal, suite_id)
    return _sections_out(session, suite.id)


@router.post("/testcases/sections", response_model=SectionOut, status_code=201)
def create_section(body: SectionIn, principal: CurrentPrincipal, session: SessionDep) -> SectionOut:
    suite = tc.suite_for(session, principal, body.suite_id, edit=True)
    session.refresh(suite, with_for_update=True)  # one tree change at a time
    try:
        section = tc.create_section(
            session, suite, parent_id=body.parent_id, name=body.name, description=body.description
        )
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None
    session.commit()
    return _one_section(session, section)


@router.patch("/testcases/sections/{section_id:int}", response_model=SectionOut)
def update_section(
    section_id: int, body: SectionPatch, principal: CurrentPrincipal, session: SessionDep
) -> SectionOut:
    """Rename, describe, move (`parent_id` given, null: the suite's root) or reorder."""
    section, suite = _section_for(session, principal, section_id, edit=True)
    session.refresh(suite, with_for_update=True)
    try:
        if body.name is not None:
            section.name = tc.section_name(body.name)
        if "description" in body.model_fields_set:
            about = (body.description or "").strip()
            section.description = about or None
        if "parent_id" in body.model_fields_set:
            tc.move_section(session, section, body.parent_id)
        if body.code_paths is not None:
            section.code_paths = tc.area_paths(session, suite, body.code_paths, section.code_paths)
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.position is not None:
        section.position = body.position
    session.commit()
    return _one_section(session, section)


@router.delete("/testcases/sections/{section_id:int}", response_model=SectionDeleted)
def delete_section(
    section_id: int, principal: CurrentPrincipal, session: SessionDep
) -> SectionDeleted:
    """The section, every section below it and their cases (TestRail's way)."""
    section, suite = _section_for(session, principal, section_id)
    _check_delete(session, principal, suite)  # §15.9: tests.delete
    session.refresh(suite, with_for_update=True)
    name = section.name
    sections, cases = tc.delete_section(session, section)
    audit(
        session,
        principal.user_id,
        "testcase.section.delete",
        f"test_section:{section_id}",
        name=name,
        sections=sections,
        cases=cases,
    )
    session.commit()
    return SectionDeleted(sections=sections, cases=cases)


# -- coverage --------------------------------------------------------------------------------------


@router.get("/testcases/stats", response_model=StatsOut)
def stats(principal: CurrentPrincipal, session: SessionDep, business_project_id: int) -> StatsOut:
    bp = tc.project_for(session, principal, business_project_id)
    scope = [s.id for s in _project_suites(session, bp.id)] or [0]
    by_status = {s: 0 for s in CASE_STATUSES}
    by_priority = {p: 0 for p in PRIORITIES}
    automated = problems = 0
    covered_keys: set[str] = set()
    missing = tc.problem_clause()
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
        automated_ci=tc_ci.automated_with_ci(session, scope),
        problems=problems,
        jira_issues=len(issues),
        jira_covered=len(issues) - len(uncovered),
        uncovered=[Uncovered(key=i.key, title=i.title, url=i.web_url) for i in uncovered[:20]],
    )


# -- import / export -------------------------------------------------------------------------------


def _custom_columns(session: Session, bp_id: int) -> dict[str, str]:
    """§15.8: "custom.<key>" -> name of the dự án's live case fields (columns an import maps)."""
    return {f"{sheets.CUSTOM_PREFIX}{f.key}": f.name for f in tf.rows(session, bp_id, "case")}


def _read_out(
    title: str, url: str | None, found: list[sheets.Sheet], custom: dict[str, str] | None = None
) -> ReadOut:
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
            for s in (sheets.guess(s, custom) for s in found)
        ],
        fields={**{f: sheets.FIELD_LABEL[f] for f in sheets.MAP_FIELDS}, **(custom or {})},
    )


@router.post("/testcases/import/read", response_model=ReadOut)
def read_url(
    body: UrlIn, principal: CurrentPrincipal, session: SessionDep, settings: SettingsDep
) -> ReadOut:
    """The tabs of a Google Sheet shared publicly, with the guessed columns. Nothing kept."""
    bp = tc.project_for(session, principal, body.business_project_id, edit=True)
    custom = _custom_columns(session, bp.id)
    session.commit()  # nothing held while the sheet downloads
    try:
        title, found = sheets.fetch_google(
            body.url.strip(), importers.hosts_of(settings.assistant_url_hosts)
        )
    except sheets.SheetError as exc:
        raise HTTPException(422, str(exc)) from None
    return _read_out(title, body.url.strip(), found, custom)


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
    custom = await run_in_threadpool(_custom_columns, session, business_project_id)
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
        return _read_out(name.rsplit(".", 1)[0], None, found, custom)

    return await run_in_threadpool(convert)


def _key(path: str, title: str) -> str:
    parts = [p.strip() for p in path.split(">") if p.strip()]
    return fold(f"{content.PATH_SEP.join(' '.join(p.split()) for p in parts)}|{title}")


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
        if any(
            f not in sheets.MAP_FIELDS and not f.startswith(sheets.CUSTOM_PREFIX)
            for f in sheet.mapping
        ):
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
        paths = content.section_paths(session, [suite.id])
        folders: dict[tuple[int | None, str], int] = {}
        milestones = {
            fold(name): ms_id
            for ms_id, name in session.execute(
                select(TestMilestone.id, TestMilestone.name)
                .where(TestMilestone.business_project_id.in_(tc.projects_of(session, suite)))
                .order_by(TestMilestone.id)
            )
        }
        by_id = {c.id: c for c in existing}
        by_code = {c.code: c for c in existing if c.code}
        # Rows without a code: the cases of the same section and title, in order.
        by_text: dict[str, list[TestCase]] = {}
        for c in existing:
            by_text.setdefault(_key(paths.get(c.section_id or 0, ""), c.title), []).append(c)
        seen: set[int] = set()
        created = updated = unchanged = 0
        position = tc.next_position(session, suite.id)
        # §15.8: the home dự án's case fields; a value that does not fit is left out (an import
        # is never blocked by a custom field, required or not).
        case_fields = tf.rows(session, suite.business_project_id, "case")
        field_by_key = {f.key: f for f in case_fields}
        approval_rule = approval.approval_on(session, suite.business_project_id)
        approver = approval.may_approve(principal, suite.business_project_id)
        for values in mapped.cases:
            case_id = values.pop("case_id", None)
            path = values.pop("section", None)
            milestone = values.pop("milestone", None)
            code = values.pop("code", None) or ""
            raw_custom: dict[str, str] = values.pop("custom_raw", None) or {}
            imported = {
                k: tf.coerce(session, field_by_key[k], v)
                for k, v in raw_custom.items()
                if k in field_by_key
            }
            if path is not None:
                values["section_id"] = content.section_for_path(session, suite.id, path, folders)
            if milestone is not None:
                found_ms = milestones.get(fold(milestone)) if milestone else None
                if found_ms is not None or not milestone:
                    values["milestone_id"] = found_ms
            case: TestCase | None
            if case_id is not None and case_id in by_id:
                case = by_id[case_id]
            else:
                case = by_code.get(code) if code else None
                if case is None:
                    # No code, or one matching nothing: an old export's "TC-nnnn" whose code was
                    # dropped by the move to C<id> keys still finds its case by section and title
                    # (only among cases without a code of their own).
                    same = [
                        c
                        for c in by_text.get(_key(path or "", values["title"]), [])
                        if id(c) not in seen and not (code and c.code)
                    ]
                    case = same[0] if same else None
            if case is not None and id(case) not in seen:
                seen.add(id(case))
                if code and by_code.get(code, case) is case:
                    values["code"] = code
                    by_code[code] = case
                if imported:
                    merged = dict(case.custom or {})
                    for k, v in imported.items():
                        if v is None:
                            merged.pop(k, None)
                        else:
                            merged[k] = v
                    values["custom"] = merged
                if approval_rule:
                    _guard(  # §15.9 G7
                        session,
                        principal,
                        suite,
                        values,
                        current=case,
                        rule=approval_rule,
                        approver=approver,
                    )
                if tc.change(session, case, values, user_id=principal.user_id, via="import"):
                    updated += 1
                else:
                    unchanged += 1
                continue
            if code and code not in by_code:
                values["code"] = code
            values.setdefault("status", "draft")
            values["custom"] = {k: v for k, v in imported.items() if v is not None}
            if approval_rule:
                _guard(session, principal, suite, values, rule=approval_rule, approver=approver)
            new = tc.create(
                session,
                suite,
                values,
                user_id=principal.user_id,
                source="import",
                position=position,
                flush=False,
                fields=case_fields,
            )
            position += 1
            if new.code:
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
    session: Session,
    groups: list[tuple[str, list[Any]]],
    name: str,
    fmt: str,
    extra: dict[int, str] | None = None,
) -> Response:
    ctx = sheets.context_for(session, [c for _, cases in groups for c in cases])
    return _file(
        sheets.write_testrail(groups, ctx)
        if fmt == "testrail"
        else sheets.write_csv(groups, extra, ctx)
        if fmt == "csv"
        else sheets.write_xlsx(groups, extra, ctx),
        name,
        fmt,
    )


def _file(data: bytes, name: str, fmt: str) -> Response:
    if fmt in ("csv", "testrail"):
        mime, ext = "text/csv; charset=utf-8", "csv"
    else:
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
    format: Literal["xlsx", "csv", "testrail"] = "xlsx",
    include_obsolete: bool = False,
) -> Response:
    """Each suite on a tab (.xlsx), or a .csv; `testrail`: TestRail's CSV, one row per step."""
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
    return _download(session, groups, name, format)


@router.get("/testcases/import/template")
def import_template(
    principal: CurrentPrincipal, format: Literal["xlsx", "testrail"] = "xlsx"
) -> Response:
    """A sample file to fill in and import: our columns (.xlsx) or TestRail's CSV."""
    return _file(sheets.template_file(format), "Testcase mau", format)


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
    ids = [i for i in dict.fromkeys([*item.matched, *item.ai_cases]) if i in cases]
    shown = dict(zip(ids, case_outs(session, [cases[i] for i in ids]), strict=True))
    out = []
    for case_id in ids:
        case = cases[case_id]
        ai = item.ai_cases.get(case_id) or {}
        out.append(
            ImpactCase(
                case=shown[case_id],
                suite=suites.get(case.suite_id, ""),
                reasons=item.matched.get(case_id, []),
                ai_reason=ai.get("reason"),
                confidence=ai.get("confidence"),
            )
        )
    out.sort(key=lambda c: (tc.PRIORITY_RANK.get(c.case.priority, 9), c.case.id))
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
    # §15 G1 AC4: the cases CI reported results for on this MR.
    for case_id, reason in tc_ci.mr_ci_reasons(
        session, bp_id, mr.iid, repo.path_with_namespace
    ).items():
        item.matched.setdefault(case_id, []).append(reason)
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
        # Analysing the MR with the AI is a test lead's (tests.manage).
        can_edit=tc.may_manage(principal, bp_id),
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
    found = [i for i in reasons if i in cases]
    shown = dict(zip(found, case_outs(session, [cases[i] for i in found]), strict=True))
    for case_id in found:
        why = reasons[case_id]
        case = cases[case_id]
        listed.append(
            ImpactCase(
                case=shown[case_id],
                suite=suites.get(case.suite_id, ""),
                reasons=why,
                ai_reason=None,
                confidence=None,
            )
        )
    listed.sort(key=lambda c: (tc.PRIORITY_RANK.get(c.case.priority, 9), c.suite, c.case.id))
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
    return _download(session, list(groups.items()), f"Can test lai {days} ngay", format, why)
