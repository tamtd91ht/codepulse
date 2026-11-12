"""Test runs the TestRail way (docs/specs/30 §13, API §14.2-14.4): milestones, configurations,
shared steps, runs with their tests and results, plans, a case's result history.

Who: `tests.view` on the dự án reads; `tests.manage` creates, edits, closes and deletes runs, plans,
milestones, configurations and shared steps; a result is recorded by whoever may edit, or by
the person the test or its run is assigned to (`content.may_record`). A closed run (or plan)
takes no more changes or results (409).
"""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.api.access import project_people
from app.api.audit import audit
from app.api.deps import get_session
from app.api.locks import busy_as_409, short_lock_wait
from app.api.routers.project_docs import Person, _iso, _people
from app.api.security import CurrentPrincipal, Principal
from app.assistant.knowledge import fold
from app.config import Settings, get_settings
from app.models import (
    TestCase,
    TestConfig,
    TestConfigGroup,
    TestMilestone,
    TestPlan,
    TestResult,
    TestRun,
    TestRunTest,
    TestSharedStep,
    User,
)
from app.testcases import bugs as tc_bugs
from app.testcases import content
from app.testcases import events as tc_events
from app.testcases import fields as tf
from app.testcases import runs as rs
from app.testcases import service as tc
from app.testcases import statuses as tst

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# §15 G6: a built-in result status or a custom one of the dự án (checked by `statuses.check`).
ResultStatus = Annotated[str, Field(min_length=1, max_length=40)]
StepStatus = Literal["untested", "passed", "failed", "blocked", "retest", "skipped"]
NAME_MAX = 200
DESC_MAX = 10_000
CLOSED_RUN = "test run đã đóng, không sửa hay ghi kết quả được nữa"
CLOSED_PLAN = "test plan đã đóng"
BUSY = "test run đang được người khác cập nhật (ghi kết quả, đóng hoặc sửa); thử lại sau vài giây"
NOT_IN_PROJECT = "người này không thuộc dự án"
POSITION_MAX = 1_000_000
# Module constants: a field named `content` would hide the module inside a class body.
STEP_TEXT_MAX = content.STEP_TEXT_MAX
STEP_MAX = content.STEP_MAX


# -- shapes ----------------------------------------------------------------------------------------


class Counts(BaseModel):
    """Tests by status: the built-in keys always, plus a key per custom status in use (G6)."""

    model_config = ConfigDict(extra="allow")

    untested: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    retest: int = 0
    skipped: int = 0
    total: int = 0


class MilestoneIn(BaseModel):
    business_project_id: int
    name: str = Field(min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    start_on: date | None = None
    due_on: date | None = None


class MilestonePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    start_on: date | None = None
    due_on: date | None = None
    completed: bool | None = None


class MilestoneOut(BaseModel):
    id: int
    key: str
    business_project_id: int
    name: str
    description: str | None
    start_on: date | None
    due_on: date | None
    completed_at: str | None
    runs: int
    counts: Counts
    can_edit: bool


class ConfigOut(BaseModel):
    id: int
    name: str


class ConfigGroupOut(BaseModel):
    id: int
    name: str
    configs: list[ConfigOut]


class ConfigGroupIn(BaseModel):
    business_project_id: int
    name: str = Field(min_length=1, max_length=NAME_MAX)


class NamePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    position: int | None = Field(default=None, ge=0, le=POSITION_MAX)


class ConfigIn(BaseModel):
    group_id: int
    name: str = Field(min_length=1, max_length=NAME_MAX)


class StepIn(BaseModel):
    content: str = Field(max_length=STEP_TEXT_MAX)
    expected: str = Field(default="", max_length=STEP_TEXT_MAX)


class SharedIn(BaseModel):
    business_project_id: int
    title: str = Field(min_length=1, max_length=NAME_MAX)
    steps: list[StepIn] = Field(min_length=1, max_length=STEP_MAX)


class SharedPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    steps: list[StepIn] | None = Field(default=None, min_length=1, max_length=STEP_MAX)


class SharedOut(BaseModel):
    id: int
    key: str
    business_project_id: int
    title: str
    steps: list[dict[str, str]]
    used_by: int
    updated_at: str | None
    can_edit: bool


class RegressionIn(BaseModel):
    days: int = Field(ge=1, le=180)
    target_branch: str | None = Field(default=None, max_length=255)


class SelectionIn(BaseModel):
    suite_id: int | None = None
    section_ids: list[int] = Field(default_factory=list, max_length=500)
    case_ids: list[int] = Field(default_factory=list, max_length=rs.RUN_TESTS_MAX)
    all: bool = False
    priority: list[str] = Field(default_factory=list, max_length=10)
    type: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)
    regression: RegressionIn | None = None

    def picked(self) -> rs.Selection:
        return rs.Selection(
            suite_id=self.suite_id,
            section_ids=self.section_ids,
            case_ids=self.case_ids,
            all=self.all,
            priority=self.priority,
            type=self.type,
            tags=[rs.line(t, 40) for t in self.tags if rs.line(t, 40)],
            regression_days=self.regression.days if self.regression else None,
            regression_branch=self.regression.target_branch if self.regression else None,
        )


class RunIn(BaseModel):
    business_project_id: int
    name: str | None = Field(default=None, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    milestone_id: int | None = None
    assigned_to: int | None = None
    refs: list[str] = Field(default_factory=list, max_length=rs.DEFECTS_MAX)
    selection: SelectionIn


class RunPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    milestone_id: int | None = None
    assigned_to: int | None = None
    refs: list[str] | None = Field(default=None, max_length=rs.DEFECTS_MAX)
    add: SelectionIn | None = None
    remove_test_ids: list[int] = Field(default_factory=list, max_length=rs.RUN_TESTS_MAX)
    # Removing tests that already have results deletes those results too: only on purpose.
    force: bool = False


class RunOut(BaseModel):
    id: int
    key: str
    business_project_id: int
    plan_id: int | None
    plan_name: str | None
    milestone_id: int | None
    milestone_name: str | None
    name: str
    description: str | None
    config: str | None
    config_ids: list[int]
    assigned_to: Person | None
    refs: list[str]
    source: str
    source_meta: dict[str, Any]
    completed_at: str | None
    created_at: str | None
    created_by: Person | None
    counts: Counts
    can_edit: bool
    # §15 G6: time spent (sum of the results' elapsed) and left (estimates of tests not done).
    elapsed_total_s: int = 0
    remaining_estimate_s: int = 0
    # §15 G6: "chỉ người được giao" is on; whether the viewer may record any test of the run.
    assigned_only: bool = False
    can_record_all: bool = False


class TestOut(BaseModel):
    __test__ = False

    id: int
    key: str
    run_id: int
    case_id: int | None
    case_key: str | None
    title: str
    section: str | None
    priority: str
    type: str
    status: str
    assigned_to: Person | None
    tested_at: str | None
    estimate_s: int | None
    defects: list[str]
    latest_comment: str | None
    flaky: bool = False  # §15 G1: the case's CI results disagree
    needs_retest: bool = False  # §15 G3: failed, and every bug of it closed (open runs)
    # §15 G6: of the latest result with a status.
    elapsed_s: int | None = None
    failed_step: int | None = None
    # §15.8 G5: the case's suite (result fields may apply to some suites only); None: deleted.
    suite_id: int | None = None


class TestsOut(BaseModel):
    __test__ = False

    items: list[TestOut]
    total: int


class ResultOut(BaseModel):
    id: int
    status: str | None
    comment: str | None
    version: str | None
    elapsed_s: int | None
    elapsed: str | None
    defects: list[str]
    step_results: list[dict[str, Any]]
    assigned_to: Person | None
    created_by: Person | None
    created_at: str | None
    # §15 G1: "ci" results carry the failure log, stack trace, attachments and CI facts.
    source: str = "manual"
    log: str | None = None
    stack: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    ci: dict[str, Any] = Field(default_factory=dict)
    failed_step: int | None = None  # §15 G6
    custom: dict[str, Any] = Field(default_factory=dict)  # §15.8 G5: result fields


class RunBrief(BaseModel):
    id: int
    key: str
    name: str
    completed: bool
    business_project_id: int | None = None  # §15 G6: where attachments are uploaded


class TestDetail(TestOut):
    content: dict[str, Any]
    results: list[ResultOut]
    run: RunBrief
    can_record: bool
    can_edit: bool


class StepResultIn(BaseModel):
    status: StepStatus | None = None
    actual: str | None = Field(default=None, max_length=STEP_TEXT_MAX)


class ResultIn(BaseModel):
    status: ResultStatus | None = None
    comment: str | None = Field(default=None, max_length=DESC_MAX)
    version: str | None = Field(default=None, max_length=NAME_MAX)
    elapsed: str | None = Field(default=None, max_length=40)
    defects: list[str] = Field(default_factory=list, max_length=rs.DEFECTS_MAX)
    step_results: list[StepResultIn] = Field(default_factory=list, max_length=STEP_MAX)
    assigned_to: int | None = None
    # §15 G6: files uploaded with POST /testcases/attachments.
    attachment_ids: list[int] = Field(default_factory=list, max_length=tst.ATTACH_PER_RESULT)
    # §15.8 G5: {key: value} of the dự án's result fields (required ones with a status).
    custom: dict[str, Any] | None = Field(default=None, max_length=tf.FIELDS_MAX)


class BulkResultIn(BaseModel):
    test_ids: list[int] = Field(min_length=1, max_length=rs.RUN_TESTS_MAX)
    status: ResultStatus | None = None
    comment: str | None = Field(default=None, max_length=DESC_MAX)
    version: str | None = Field(default=None, max_length=NAME_MAX)
    defects: list[str] = Field(default_factory=list, max_length=rs.DEFECTS_MAX)
    assigned_to: int | None = None
    custom: dict[str, Any] | None = Field(default=None, max_length=tf.FIELDS_MAX)


class Changed(BaseModel):
    changed: int


class CaseResult(BaseModel):
    result: ResultOut
    test_id: int
    test_key: str
    run_id: int
    run_key: str
    run_name: str
    config: str | None
    completed: bool


class PlanEntry(BaseModel):
    name: str | None = Field(default=None, max_length=NAME_MAX)
    selection: SelectionIn
    config_ids: list[int] = Field(default_factory=list, max_length=50)
    assigned_to: int | None = None


class PlanIn(BaseModel):
    business_project_id: int
    name: str = Field(min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    milestone_id: int | None = None
    entries: list[PlanEntry] = Field(default_factory=list, max_length=rs.PLAN_RUNS_MAX)


class PlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    milestone_id: int | None = None
    add_entries: list[PlanEntry] = Field(default_factory=list, max_length=rs.PLAN_RUNS_MAX)


class PlanOut(BaseModel):
    id: int
    key: str
    business_project_id: int
    name: str
    description: str | None
    milestone_id: int | None
    milestone_name: str | None
    completed_at: str | None
    created_at: str | None
    counts: Counts
    runs: int
    can_edit: bool


class PlanDetail(BaseModel):
    id: int
    key: str
    business_project_id: int
    name: str
    description: str | None
    milestone_id: int | None
    milestone_name: str | None
    completed_at: str | None
    created_at: str | None
    counts: Counts
    runs: list[RunOut]
    can_edit: bool


# -- helpers ---------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _today(settings: Settings) -> date:
    try:
        return datetime.now(ZoneInfo(settings.report_tz)).date()
    except Exception:  # an unknown zone name, or no tz database
        return _now().date()


def _name(value: str) -> str:
    out = rs.line(value)
    if not out:
        raise HTTPException(422, "tên không được trống")
    return out


def _assignable(session: Session, settings: Settings, bp_id: int) -> Any:
    """Who a test or a run of dự án `bp_id` can be assigned to: its people, the admins and the
    "Quản lý tổng" accounts, active ones only (the same list as GET /testcases/people)."""
    ids = project_people(session, [bp_id])
    admins = [e.lower() for e in settings.admin_emails]
    return select(User.id, User.full_name).where(
        or_(
            User.id.in_(ids or {0}),
            func.lower(User.primary_email).in_(admins or [""]),
            User.manage_all.is_(True),
        ),
        User.active.is_not(False),
    )


def _person(session: Session, settings: Settings, bp_id: int, user_id: int | None) -> int | None:
    if user_id is None:
        return None
    if session.get(User, user_id) is None:
        raise HTTPException(422, "người được giao không tồn tại")
    if session.execute(_assignable(session, settings, bp_id).where(User.id == user_id)).first():
        return user_id
    raise HTTPException(422, NOT_IN_PROJECT)


def _milestone(session: Session, bp_id: int, milestone_id: int | None) -> int | None:
    if milestone_id is None:
        return None
    m = session.get(TestMilestone, milestone_id)
    if m is None or m.business_project_id != bp_id:
        raise HTTPException(422, "milestone không tồn tại")
    return m.id


def _edit(principal: Principal, bp_id: int) -> None:
    if not tc.may_manage(principal, bp_id):
        raise HTTPException(403, "bạn không có quyền quản lý test run của dự án này")


def _design(principal: Principal, bp_id: int) -> None:
    """Shared steps are part of the cases' design (`tests.edit`)."""
    if not tc.may_edit(principal, bp_id):
        raise HTTPException(403, "bạn không có quyền sửa testcase của dự án này")


def _view(principal: Principal, bp_id: int, missing: str) -> None:
    if not tc.may_view(principal, bp_id):
        raise HTTPException(404, missing)


def _lock(session: Session, model: Any, ident: int, *, read: bool = False) -> Any:
    return session.scalar(
        select(model)
        .where(model.id == ident)
        .with_for_update(read=read)
        .execution_options(populate_existing=True)
    )


def _run_for(
    session: Session,
    principal: Principal,
    run_id: int,
    *,
    edit: bool = False,
    lock: bool = False,
    open_only: bool = False,
) -> TestRun:
    run = _lock(session, TestRun, run_id) if lock else session.get(TestRun, run_id)
    if run is None or not tc.may_view(principal, run.business_project_id):
        raise HTTPException(404, "test run không tồn tại")
    if edit:
        _edit(principal, run.business_project_id)
    if open_only and run.completed_at is not None:
        raise HTTPException(409, CLOSED_RUN)
    return run


def _counts(raw: dict[str, int] | None) -> Counts:
    return Counts(**(raw or rs.empty_counts()))


def _run_outs(session: Session, principal: Principal, runs: list[TestRun]) -> list[RunOut]:
    counts = rs.run_counts(session, [r.id for r in runs])
    plans = dict(
        session.execute(
            select(TestPlan.id, TestPlan.name).where(
                TestPlan.id.in_({r.plan_id for r in runs if r.plan_id} or {0})
            )
        ).all()
    )
    milestones = dict(
        session.execute(
            select(TestMilestone.id, TestMilestone.name).where(
                TestMilestone.id.in_({r.milestone_id for r in runs if r.milestone_id} or {0})
            )
        ).all()
    )
    people = _people(session, {r.assigned_to for r in runs} | {r.created_by for r in runs})
    bps = {r.business_project_id for r in runs}
    times = rs.run_times(session, runs, tst.not_done(session, bps))
    only = {bp: tst.assigned_only(session, bp) for bp in bps}
    return [
        RunOut(
            id=r.id,
            key=content.key("run", r.id) or "",
            business_project_id=r.business_project_id,
            plan_id=r.plan_id,
            plan_name=plans.get(r.plan_id) if r.plan_id else None,
            milestone_id=r.milestone_id,
            milestone_name=milestones.get(r.milestone_id) if r.milestone_id else None,
            name=r.name,
            description=r.description,
            config=r.config,
            config_ids=list(r.config_ids or []),
            assigned_to=people.get(r.assigned_to) if r.assigned_to else None,
            refs=list(r.refs or []),
            source=r.source,
            source_meta=dict(r.source_meta or {}),
            completed_at=_iso(r.completed_at),
            created_at=_iso(r.created_at),
            created_by=people.get(r.created_by) if r.created_by else None,
            counts=_counts(counts.get(r.id)),
            can_edit=tc.may_manage(principal, r.business_project_id),
            elapsed_total_s=times[r.id].elapsed_total_s,
            remaining_estimate_s=times[r.id].remaining_estimate_s,
            assigned_only=only[r.business_project_id],
            can_record_all=r.completed_at is None
            and tst.may_record_any(principal, r.business_project_id, only[r.business_project_id]),
        )
        for r in runs
    ]


def _test_outs(
    session: Session, run: TestRun, tests: list[TestRunTest]
) -> tuple[list[TestOut], dict[int, dict[str, Any]]]:
    shown = rs.views(session, run, tests)
    defects, comments = rs.result_extras(session, [t.id for t in tests])
    case_ids = {t.case_id for t in tests if t.case_id is not None}
    facts_of = (
        session.execute(
            select(TestCase.id, TestCase.suite_id, TestCase.flaky).where(TestCase.id.in_(case_ids))
        ).all()
        if case_ids
        else []
    )
    flaky = {cid for cid, _, is_flaky in facts_of if is_flaky}
    suite_of: dict[int, int] = {cid: sid for cid, sid, _ in facts_of}
    people = _people(session, {t.assigned_to for t in tests})
    retest = tc_bugs.retest_ids(session, run, tests, defects)
    facts = rs.latest_facts(session, [t.id for t in tests])
    out = [
        TestOut(
            id=t.id,
            key=content.key("test", t.id) or "",
            run_id=t.run_id,
            case_id=t.case_id,
            suite_id=suite_of.get(t.case_id) if t.case_id is not None else None,
            case_key=content.key("case", t.case_id),
            title=str(shown[t.id].get("title") or t.title),
            section=shown[t.id].get("section"),
            priority=str(shown[t.id].get("priority") or t.priority),
            type=str(shown[t.id].get("type") or t.type),
            status=t.status,
            assigned_to=people.get(t.assigned_to) if t.assigned_to else None,
            tested_at=_iso(t.tested_at),
            estimate_s=shown[t.id].get("estimate_s"),
            defects=defects.get(t.id, []),
            latest_comment=comments.get(t.id),
            flaky=t.case_id in flaky,
            needs_retest=t.id in retest,
            elapsed_s=facts.get(t.id, (None, None))[0],
            failed_step=facts.get(t.id, (None, None))[1],
        )
        for t in tests
    ]
    return out, shown


def _result_outs(session: Session, results: list[TestResult]) -> list[ResultOut]:
    people = _people(session, {r.assigned_to for r in results} | {r.created_by for r in results})
    return [
        ResultOut(
            id=r.id,
            status=r.status,
            comment=r.comment,
            version=r.version,
            elapsed_s=r.elapsed_s,
            elapsed=content.format_estimate(r.elapsed_s),
            defects=list(r.defects or []),
            step_results=list(r.step_results or []),
            assigned_to=people.get(r.assigned_to) if r.assigned_to else None,
            created_by=people.get(r.created_by) if r.created_by else None,
            created_at=_iso(r.created_at),
            source=r.source,
            log=r.log,
            stack=r.stack,
            attachments=list(r.attachments or []),
            ci=dict(r.ci or {}),
            failed_step=r.failed_step,
            custom=dict(r.custom or {}),
        )
        for r in results
    ]


def _can_record(session: Session, principal: Principal, run: TestRun, test: TestRunTest) -> bool:
    return run.completed_at is None and tst.may_record(
        session, principal, run.business_project_id, [test.assigned_to, run.assigned_to]
    )


def _detail(session: Session, principal: Principal, test: TestRunTest, run: TestRun) -> TestDetail:
    [base], shown = _test_outs(session, run, [test])
    results = list(
        session.scalars(
            select(TestResult)
            .where(TestResult.test_id == test.id)
            .order_by(TestResult.created_at.desc(), TestResult.id.desc())
        )
    )
    return TestDetail(
        **base.model_dump(),
        content=shown[test.id],
        results=_result_outs(session, results),
        run=RunBrief(
            id=run.id,
            key=content.key("run", run.id) or "",
            name=run.name,
            completed=run.completed_at is not None,
            business_project_id=run.business_project_id,
        ),
        can_record=_can_record(session, principal, run, test),
        can_edit=run.completed_at is None and tc.may_manage(principal, run.business_project_id),
    )


def _test_for(
    session: Session, principal: Principal, test_id: int, *, lock: bool = False
) -> tuple[TestRunTest, TestRun]:
    """With `lock`: the run in share mode first, then the test for update. Closing a run, removing
    its tests and closing a plan lock the run before its tests; taking them in the same order
    means a result and a close wait for each other instead of deadlocking."""
    if not lock:
        test = session.get(TestRunTest, test_id)
        run = session.get(TestRun, test.run_id) if test is not None else None
    else:
        run_id = session.scalar(select(TestRunTest.run_id).where(TestRunTest.id == test_id))
        run = _lock(session, TestRun, run_id, read=True) if run_id is not None else None
        test = _lock(session, TestRunTest, test_id) if run is not None else None
        if test is not None and test.run_id != run_id:  # a test never changes run; be safe
            test = None
    if test is None or run is None or not tc.may_view(principal, run.business_project_id):
        raise HTTPException(404, "test không tồn tại")
    return test, run


def _download(data: bytes, name: str) -> Response:
    safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_").strip() or "test-run"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="test-run.xlsx"; '
            f"filename*=UTF-8''{quote(safe)}.xlsx",
            "X-Content-Type-Options": "nosniff",
        },
    )


# -- people ----------------------------------------------------------------------------------------


@router.get("/testcases/people", response_model=list[Person])
def people(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    business_project_id: int,
) -> list[Person]:
    """Who a test or a run can be assigned to: the people of the dự án and the active admins."""
    bp = tc.project_for(session, principal, business_project_id)
    rows = session.execute(_assignable(session, settings, bp.id)).all()
    out = [Person(id=uid, name=name or f"#{uid}") for uid, name in rows]
    return sorted(out, key=lambda p: (fold(p.name), p.id))


# -- milestones ------------------------------------------------------------------------------------


def _milestone_outs(
    session: Session, principal: Principal, items: list[TestMilestone]
) -> list[MilestoneOut]:
    counts, runs = rs.counts_by(session, TestRun.milestone_id, [m.id for m in items])
    return [
        MilestoneOut(
            id=m.id,
            key=content.key("milestone", m.id) or "",
            business_project_id=m.business_project_id,
            name=m.name,
            description=m.description,
            start_on=m.start_on,
            due_on=m.due_on,
            completed_at=_iso(m.completed_at),
            runs=runs.get(m.id, 0),
            counts=_counts(counts.get(m.id)),
            can_edit=tc.may_manage(principal, m.business_project_id),
        )
        for m in items
    ]


def _milestone_for(
    session: Session, principal: Principal, milestone_id: int, *, edit: bool = False
) -> TestMilestone:
    m = session.get(TestMilestone, milestone_id)
    if m is None or not tc.may_view(principal, m.business_project_id):
        raise HTTPException(404, "milestone không tồn tại")
    if edit:
        _edit(principal, m.business_project_id)
    return m


@router.get("/testcases/milestones", response_model=list[MilestoneOut])
def list_milestones(
    principal: CurrentPrincipal, session: SessionDep, business_project_id: int
) -> list[MilestoneOut]:
    bp = tc.project_for(session, principal, business_project_id)
    items = list(
        session.scalars(
            select(TestMilestone)
            .where(TestMilestone.business_project_id == bp.id)
            .order_by(
                TestMilestone.completed_at.is_not(None),
                TestMilestone.due_on.asc().nulls_last(),
                TestMilestone.id.desc(),
            )
        )
    )
    return _milestone_outs(session, principal, items)


@router.post("/testcases/milestones", response_model=MilestoneOut, status_code=201)
def create_milestone(
    body: MilestoneIn, principal: CurrentPrincipal, session: SessionDep
) -> MilestoneOut:
    bp = tc.project_for(session, principal, body.business_project_id, manage=True)
    m = TestMilestone(
        business_project_id=bp.id,
        name=_name(body.name),
        description=rs.text(body.description),
        start_on=body.start_on,
        due_on=body.due_on,
        created_by=principal.user_id,
    )
    session.add(m)
    session.flush()
    audit(session, principal.user_id, "testmilestone.create", f"test_milestone:{m.id}")
    session.commit()
    return _milestone_outs(session, principal, [m])[0]


@router.patch("/testcases/milestones/{milestone_id:int}", response_model=MilestoneOut)
def update_milestone(
    milestone_id: int, body: MilestonePatch, principal: CurrentPrincipal, session: SessionDep
) -> MilestoneOut:
    m = _milestone_for(session, principal, milestone_id, edit=True)
    given = body.model_fields_set
    if body.name is not None:
        m.name = _name(body.name)
    if "description" in given:
        m.description = rs.text(body.description)
    if "start_on" in given:
        m.start_on = body.start_on
    if "due_on" in given:
        m.due_on = body.due_on
    if body.completed is not None:
        if body.completed and m.completed_at is None:
            m.completed_at = _now()
        elif not body.completed:
            m.completed_at = None
    session.commit()
    return _milestone_outs(session, principal, [m])[0]


@router.delete("/testcases/milestones/{milestone_id:int}", status_code=204)
def delete_milestone(milestone_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    m = _milestone_for(session, principal, milestone_id, edit=True)
    audit(session, principal.user_id, "testmilestone.delete", f"test_milestone:{m.id}", name=m.name)
    session.delete(m)
    session.commit()


# -- configurations --------------------------------------------------------------------------------


def _group_for(
    session: Session, principal: Principal, group_id: int, *, edit: bool = False
) -> TestConfigGroup:
    g = session.get(TestConfigGroup, group_id)
    if g is None or not tc.may_view(principal, g.business_project_id):
        raise HTTPException(404, "nhóm cấu hình không tồn tại")
    if edit:
        _edit(principal, g.business_project_id)
    return g


def _group_out(session: Session, group: TestConfigGroup) -> ConfigGroupOut:
    configs = session.scalars(
        select(TestConfig)
        .where(TestConfig.group_id == group.id)
        .order_by(TestConfig.position, TestConfig.id)
    )
    return ConfigGroupOut(
        id=group.id, name=group.name, configs=[ConfigOut(id=c.id, name=c.name) for c in configs]
    )


@router.get("/testcases/configs", response_model=list[ConfigGroupOut])
def list_configs(
    principal: CurrentPrincipal, session: SessionDep, business_project_id: int
) -> list[ConfigGroupOut]:
    bp = tc.project_for(session, principal, business_project_id)
    groups = list(
        session.scalars(
            select(TestConfigGroup)
            .where(TestConfigGroup.business_project_id == bp.id)
            .order_by(TestConfigGroup.position, TestConfigGroup.id)
        )
    )
    by_group: dict[int, list[ConfigOut]] = {g.id: [] for g in groups}
    for c in session.scalars(
        select(TestConfig)
        .where(TestConfig.group_id.in_(list(by_group) or [0]))
        .order_by(TestConfig.position, TestConfig.id)
    ):
        by_group[c.group_id].append(ConfigOut(id=c.id, name=c.name))
    return [ConfigGroupOut(id=g.id, name=g.name, configs=by_group[g.id]) for g in groups]


@router.post("/testcases/config-groups", response_model=ConfigGroupOut, status_code=201)
def create_config_group(
    body: ConfigGroupIn, principal: CurrentPrincipal, session: SessionDep
) -> ConfigGroupOut:
    bp = tc.project_for(session, principal, body.business_project_id, manage=True)
    top = session.scalar(
        select(func.max(TestConfigGroup.position)).where(
            TestConfigGroup.business_project_id == bp.id
        )
    )
    group = TestConfigGroup(
        business_project_id=bp.id, name=_name(body.name), position=int(top or 0) + 1
    )
    session.add(group)
    session.commit()
    return _group_out(session, group)


@router.patch("/testcases/config-groups/{group_id:int}", response_model=ConfigGroupOut)
def update_config_group(
    group_id: int, body: NamePatch, principal: CurrentPrincipal, session: SessionDep
) -> ConfigGroupOut:
    group = _group_for(session, principal, group_id, edit=True)
    if body.name is not None:
        group.name = _name(body.name)
    if body.position is not None:
        group.position = body.position
    session.commit()
    return _group_out(session, group)


@router.delete("/testcases/config-groups/{group_id:int}", status_code=204)
def delete_config_group(group_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    group = _group_for(session, principal, group_id, edit=True)
    session.delete(group)
    session.commit()


def _config_for(
    session: Session, principal: Principal, config_id: int
) -> tuple[TestConfig, TestConfigGroup]:
    config = session.get(TestConfig, config_id)
    if config is None:
        raise HTTPException(404, "cấu hình không tồn tại")
    try:
        group = _group_for(session, principal, config.group_id, edit=True)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, "cấu hình không tồn tại") from None
        raise
    return config, group


@router.post("/testcases/configs", response_model=ConfigOut, status_code=201)
def create_config(body: ConfigIn, principal: CurrentPrincipal, session: SessionDep) -> ConfigOut:
    group = _group_for(session, principal, body.group_id, edit=True)
    top = session.scalar(
        select(func.max(TestConfig.position)).where(TestConfig.group_id == group.id)
    )
    config = TestConfig(group_id=group.id, name=_name(body.name), position=int(top or 0) + 1)
    session.add(config)
    session.commit()
    return ConfigOut(id=config.id, name=config.name)


@router.patch("/testcases/configs/{config_id:int}", response_model=ConfigOut)
def update_config(
    config_id: int, body: NamePatch, principal: CurrentPrincipal, session: SessionDep
) -> ConfigOut:
    config, _ = _config_for(session, principal, config_id)
    if body.name is not None:
        config.name = _name(body.name)
    if body.position is not None:
        config.position = body.position
    session.commit()
    return ConfigOut(id=config.id, name=config.name)


@router.delete("/testcases/configs/{config_id:int}", status_code=204)
def delete_config(config_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    config, _ = _config_for(session, principal, config_id)
    session.delete(config)
    session.commit()


# -- shared steps ----------------------------------------------------------------------------------

_USED_BY = text(
    "SELECT (e->>'shared')::int AS sid, count(DISTINCT c.id) "
    "FROM test_cases c CROSS JOIN LATERAL jsonb_array_elements(c.steps_list) e "
    "WHERE jsonb_typeof(c.steps_list) = 'array' AND jsonb_typeof(e) = 'object' "
    "AND jsonb_typeof(e->'shared') = 'number' AND (e->>'shared')::int = ANY(:ids) "
    "GROUP BY 1"
)


def _used_by(session: Session, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    return {int(sid): int(n) for sid, n in session.execute(_USED_BY, {"ids": ids})}


def _steps(items: list[StepIn]) -> list[dict[str, str]]:
    out = [
        {
            "content": rs.text(s.content, STEP_TEXT_MAX) or "",
            "expected": rs.text(s.expected, STEP_TEXT_MAX) or "",
        }
        for s in items
    ]
    out = [s for s in out if s["content"] or s["expected"]]
    if not out:
        raise HTTPException(422, "bước dùng chung cần ít nhất một bước")
    return out


def _shared_outs(
    session: Session, principal: Principal, items: list[TestSharedStep]
) -> list[SharedOut]:
    used = _used_by(session, [s.id for s in items])
    return [
        SharedOut(
            id=s.id,
            key=content.key("shared", s.id) or "",
            business_project_id=s.business_project_id,
            title=s.title,
            steps=[
                {"content": str(x.get("content") or ""), "expected": str(x.get("expected") or "")}
                for x in s.steps or []
            ],
            used_by=used.get(s.id, 0),
            updated_at=_iso(s.updated_at),
            can_edit=tc.may_edit(principal, s.business_project_id),
        )
        for s in items
    ]


def _shared_for(
    session: Session, principal: Principal, shared_id: int, *, edit: bool = False
) -> TestSharedStep:
    s = session.get(TestSharedStep, shared_id)
    if s is None or not tc.may_view(principal, s.business_project_id):
        raise HTTPException(404, "bước dùng chung không tồn tại")
    if edit:
        _design(principal, s.business_project_id)
    return s


@router.get("/testcases/shared-steps", response_model=list[SharedOut])
def list_shared_steps(
    principal: CurrentPrincipal, session: SessionDep, business_project_id: int
) -> list[SharedOut]:
    bp = tc.project_for(session, principal, business_project_id)
    items = list(
        session.scalars(
            select(TestSharedStep)
            .where(TestSharedStep.business_project_id == bp.id)
            .order_by(TestSharedStep.title, TestSharedStep.id)
        )
    )
    return _shared_outs(session, principal, items)


@router.post("/testcases/shared-steps", response_model=SharedOut, status_code=201)
def create_shared_step(
    body: SharedIn, principal: CurrentPrincipal, session: SessionDep
) -> SharedOut:
    bp = tc.project_for(session, principal, body.business_project_id, edit=True)
    s = TestSharedStep(
        business_project_id=bp.id,
        title=_name(body.title),
        steps=_steps(body.steps),
        created_by=principal.user_id,
        updated_by=principal.user_id,
    )
    session.add(s)
    session.flush()
    audit(session, principal.user_id, "testshared.create", f"test_shared_step:{s.id}")
    session.commit()
    return _shared_outs(session, principal, [s])[0]


@router.patch("/testcases/shared-steps/{shared_id:int}", response_model=SharedOut)
def update_shared_step(
    shared_id: int, body: SharedPatch, principal: CurrentPrincipal, session: SessionDep
) -> SharedOut:
    s = _shared_for(session, principal, shared_id, edit=True)
    if body.title is not None:
        s.title = _name(body.title)
    if body.steps is not None:
        s.steps = _steps(body.steps)
    s.updated_by = principal.user_id
    session.commit()
    return _shared_outs(session, principal, [s])[0]


@router.delete("/testcases/shared-steps/{shared_id:int}", status_code=204)
def delete_shared_step(shared_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    s = _shared_for(session, principal, shared_id, edit=True)
    used = session.scalar(
        select(func.count())
        .select_from(TestCase)
        .where(TestCase.steps_list.contains([{"shared": s.id}]))
    )
    if used:
        raise HTTPException(409, f"bước dùng chung đang được {used} testcase dùng, chưa xoá được")
    audit(
        session, principal.user_id, "testshared.delete", f"test_shared_step:{s.id}", title=s.title
    )
    session.delete(s)
    session.commit()


# -- runs ------------------------------------------------------------------------------------------


def _new_run(
    session: Session,
    principal: Principal,
    bp_id: int,
    selection: SelectionIn,
    *,
    name: str | None,
    today: date,
    description: str | None = None,
    milestone_id: int | None = None,
    assigned_to: int | None = None,
    refs: list[str] | None = None,
    plan: TestPlan | None = None,
    configs: tuple[list[int], str | None] = ([], None),
    chosen: rs.Chosen | None = None,
    shown: dict[int, dict[str, Any]] | None = None,
) -> TestRun:
    sel = selection.picked()
    picked = chosen or rs.resolve(session, bp_id, sel, _now())
    run = TestRun(
        business_project_id=bp_id,
        plan_id=plan.id if plan else None,
        milestone_id=milestone_id,
        name=rs.line(name) or rs.default_name(sel, today),
        description=rs.text(description),
        config_ids=configs[0],
        config=configs[1],
        assigned_to=assigned_to,
        refs=rs.jira_keys(refs or []),
        source="plan" if plan else picked.source,
        source_meta=picked.meta,
        created_by=principal.user_id,
    )
    session.add(run)
    session.flush()
    rs.add_tests(session, run, picked.cases, shown)
    return run


@router.get("/testcases/runs", response_model=list[RunOut])
def list_runs(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    state: Literal["open", "completed", "all"] = "all",
    milestone_id: int | None = None,
    plan_id: int | None = None,
) -> list[RunOut]:
    bp = tc.project_for(session, principal, business_project_id)
    query = select(TestRun).where(TestRun.business_project_id == bp.id)
    if state == "open":
        query = query.where(TestRun.completed_at.is_(None))
    elif state == "completed":
        query = query.where(TestRun.completed_at.is_not(None))
    if milestone_id is not None:
        query = query.where(TestRun.milestone_id == milestone_id)
    if plan_id is not None:
        query = query.where(TestRun.plan_id == plan_id)
    runs = list(session.scalars(query.order_by(TestRun.created_at.desc(), TestRun.id.desc())))
    return _run_outs(session, principal, runs)


@router.post("/testcases/runs", response_model=RunOut, status_code=201)
def create_run(
    body: RunIn, principal: CurrentPrincipal, session: SessionDep, settings: SettingsDep
) -> RunOut:
    bp = tc.project_for(session, principal, body.business_project_id, manage=True)
    run = _new_run(
        session,
        principal,
        bp.id,
        body.selection,
        name=body.name,
        today=_today(settings),
        description=body.description,
        milestone_id=_milestone(session, bp.id, body.milestone_id),
        assigned_to=_person(session, settings, bp.id, body.assigned_to),
        refs=body.refs,
    )
    audit(
        session,
        principal.user_id,
        "testrun.create",
        f"test_run:{run.id}",
        tests=rs.run_size(session, run.id),
        source=run.source,
    )
    session.commit()
    return _run_outs(session, principal, [run])[0]


@router.get("/testcases/runs/{run_id:int}", response_model=RunOut)
def get_run(run_id: int, principal: CurrentPrincipal, session: SessionDep) -> RunOut:
    return _run_outs(session, principal, [_run_for(session, principal, run_id)])[0]


@router.patch("/testcases/runs/{run_id:int}", response_model=RunOut)
@busy_as_409(BUSY)
def update_run(
    run_id: int,
    body: RunPatch,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> RunOut:
    short_lock_wait(session)  # a result or a close may hold the run (deadlock-free order)
    run = _run_for(session, principal, run_id, edit=True, lock=True, open_only=True)
    given = body.model_fields_set
    if body.name is not None:
        run.name = _name(body.name)
    if "description" in given:
        run.description = rs.text(body.description)
    if "milestone_id" in given:
        run.milestone_id = _milestone(session, run.business_project_id, body.milestone_id)
    if "assigned_to" in given:
        run.assigned_to = _person(session, settings, run.business_project_id, body.assigned_to)
    if body.refs is not None:
        run.refs = rs.jira_keys(body.refs)
    if body.remove_test_ids and not body.force:
        tested = rs.tests_with_results(session, run, body.remove_test_ids)
        if tested:
            raise HTTPException(
                409,
                f"{tested} test đã có kết quả; xoá sẽ mất cả kết quả của chúng. "
                "Gửi lại với force: true nếu chắc chắn.",
            )
    removed = rs.remove_tests(session, run, body.remove_test_ids)
    added = 0
    if body.add is not None:
        picked = rs.resolve(session, run.business_project_id, body.add.picked(), _now())
        added = rs.add_tests(session, run, picked.cases)
    if added or removed:
        audit(
            session,
            principal.user_id,
            "testrun.tests",
            f"test_run:{run.id}",
            added=added or None,
            removed=len(removed) or None,
            removed_ids=removed or None,
            forced=body.force or None,
        )
    session.commit()
    return _run_outs(session, principal, [run])[0]


@router.post("/testcases/runs/{run_id:int}/close", response_model=RunOut)
@busy_as_409(BUSY)
def close_run(run_id: int, principal: CurrentPrincipal, session: SessionDep) -> RunOut:
    short_lock_wait(session)
    run = _run_for(session, principal, run_id, edit=True, lock=True, open_only=True)
    rs.close_run(session, run, _now())
    audit(session, principal.user_id, "testrun.close", f"test_run:{run.id}")
    tc_events.run_closed(session, run)  # docs/specs/30 §15: webhook run.closed
    session.commit()
    return _run_outs(session, principal, [run])[0]


@router.delete("/testcases/runs/{run_id:int}", status_code=204)
@busy_as_409(BUSY)
def delete_run(run_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    short_lock_wait(session)
    run = _run_for(session, principal, run_id, edit=True, lock=True)
    audit(
        session,
        principal.user_id,
        "testrun.delete",
        f"test_run:{run.id}",
        name=run.name,
        tests=rs.run_size(session, run.id),
    )
    session.delete(run)
    session.commit()


@router.get("/testcases/runs/{run_id:int}/tests", response_model=TestsOut)
def list_tests(
    run_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    status: Annotated[str | None, Query(max_length=100)] = None,
    assigned_to: int | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> TestsOut:
    """`status`: one or several, comma-separated; `assigned_to=0`: nobody."""
    run = _run_for(session, principal, run_id)
    query = select(TestRunTest).where(TestRunTest.run_id == run.id)
    # §15 G3: `needs_retest` among the statuses keeps only the tests to run again.
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    retest_only = "needs_retest" in statuses
    status = ",".join(s for s in statuses if s != "needs_retest") or None
    if status:
        query = query.where(TestRunTest.status.in_([s.strip() for s in status.split(",")]))
    if assigned_to == 0:
        query = query.where(TestRunTest.assigned_to.is_(None))
    elif assigned_to is not None:
        query = query.where(TestRunTest.assigned_to == assigned_to)
    tests = list(session.scalars(query.order_by(TestRunTest.position, TestRunTest.id)))
    items, _ = _test_outs(session, run, tests)
    if retest_only:
        items = [t for t in items if t.needs_retest]
    if q and q.strip():
        needle = fold(q.strip())
        items = [
            t
            for t in items
            if needle in fold(f"{t.key} {t.case_key or ''} {t.title} {t.section or ''}")
        ]
    return TestsOut(items=items, total=len(items))


@router.get("/testcases/tests/{test_id:int}", response_model=TestDetail)
def get_test(test_id: int, principal: CurrentPrincipal, session: SessionDep) -> TestDetail:
    test, run = _test_for(session, principal, test_id)
    return _detail(session, principal, test, run)


@router.post("/testcases/tests/{test_id:int}/results", response_model=TestDetail, status_code=201)
@busy_as_409(BUSY)
def add_result(
    test_id: int,
    body: ResultIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> TestDetail:
    short_lock_wait(session)  # closing the run or removing tests may hold it
    test, run = _test_for(session, principal, test_id, lock=True)
    if run.completed_at is not None:
        raise HTTPException(409, CLOSED_RUN)
    if not tst.may_record(
        session, principal, run.business_project_id, [test.assigned_to, run.assigned_to]
    ):
        raise HTTPException(
            403, "chỉ người được giao test hoặc người sửa được testcase mới ghi kết quả"
        )
    status = tst.check(session, run.business_project_id, body.status)
    files = tst.attachment_refs(session, run.business_project_id, body.attachment_ids)
    reassign = "assigned_to" in body.model_fields_set
    elapsed = content.parse_estimate(body.elapsed) if body.elapsed else None
    if body.elapsed and body.elapsed.strip() and elapsed is None:
        raise HTTPException(422, "thời gian chạy không đọc được (ví dụ: 30s, 5m, 1h 30m)")
    steps: list[dict[str, Any]] = []
    shown = rs.views(session, run, [test])[test.id]
    if body.step_results:
        count = len(shown.get("steps_list") or []) if shown.get("template") == "steps" else 0
        steps = rs.step_results([s.model_dump() for s in body.step_results], count)
    # §15 G6: a failed (blocked) step fails (blocks) the result; all passed: passed.
    status, failed_step = tst.from_steps(status, steps)
    custom = rs.result_custom(
        session,
        run.business_project_id,
        test,
        body.custom,
        status=status,
        template=shown.get("template"),
    )
    defects = rs.jira_keys(body.defects)
    if not (
        status
        or rs.text(body.comment)
        or rs.line(body.version)
        or elapsed
        or defects
        or steps
        or files
        or reassign
        or custom
    ):
        raise HTTPException(422, "kết quả trống: chọn trạng thái hoặc nhập bình luận")
    rs.add_result(
        session,
        test,
        user_id=principal.user_id,
        now=_now(),
        status=status,
        failed_step=failed_step,
        attachments=files,
        custom=custom,
        comment=body.comment,
        version=body.version,
        elapsed_s=elapsed,
        defects=defects,
        steps=steps,
        reassign=reassign,
        assigned_to=(
            _person(session, settings, run.business_project_id, body.assigned_to)
            if reassign
            else None
        ),
    )
    tc_events.results_failed(
        session, run, [test], status=status, comment=body.comment, defects=defects
    )
    session.commit()
    return _detail(session, principal, test, run)


@router.post("/testcases/runs/{run_id:int}/results", response_model=Changed)
@busy_as_409(BUSY)
def bulk_results(
    run_id: int,
    body: BulkResultIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> Changed:
    short_lock_wait(session)
    run = _run_for(session, principal, run_id)
    run = _lock(session, TestRun, run.id, read=True)
    if run.completed_at is not None:
        raise HTTPException(409, CLOSED_RUN)
    reassign = "assigned_to" in body.model_fields_set
    defects = rs.jira_keys(body.defects)
    status = tst.check(session, run.business_project_id, body.status)
    if not (
        status
        or rs.text(body.comment)
        or rs.line(body.version)
        or defects
        or reassign
        or body.custom
    ):
        raise HTTPException(422, "kết quả trống: chọn trạng thái hoặc nhập bình luận")
    assignee = (
        _person(session, settings, run.business_project_id, body.assigned_to) if reassign else None
    )
    tests = list(
        session.scalars(
            select(TestRunTest)
            .where(TestRunTest.run_id == run.id, TestRunTest.id.in_(sorted(set(body.test_ids))))
            .order_by(TestRunTest.id)
            .with_for_update()
        )
    )
    if len(tests) != len(set(body.test_ids)):
        raise HTTPException(422, "có test không thuộc test run này")
    only = tst.assigned_only(session, run.business_project_id)
    for t in tests:
        if not tst.may_record(
            session,
            principal,
            run.business_project_id,
            [t.assigned_to, run.assigned_to],
            only_assigned=only,
        ):
            raise HTTPException(403, f"bạn không được ghi kết quả cho {content.key('test', t.id)}")
    now = _now()
    # §15.8: result fields per test (they may apply to some suites / templates only).
    # No live result field: nothing is kept (unknown keys are dropped), so no per-test work.
    result_fields = tf.rows(session, run.business_project_id, "result")
    customs: dict[int, dict[str, Any]] = {t.id: {} for t in tests}
    if result_fields:
        shown = rs.views(session, run, tests)
        suite_of = rs.suites_of(session, tests)
        customs = {
            t.id: rs.result_custom(
                session,
                run.business_project_id,
                t,
                body.custom,
                status=status,
                template=shown.get(t.id, {}).get("template"),
                fields=result_fields,
                suite_of=suite_of,
            )
            for t in tests
        }
    for t in tests:
        rs.add_result(
            session,
            t,
            user_id=principal.user_id,
            now=now,
            status=status,
            comment=body.comment,
            version=body.version,
            defects=defects,
            reassign=reassign,
            assigned_to=assignee,
            custom=customs[t.id],
        )
    audit(
        session,
        principal.user_id,
        "testresult.bulk",
        f"test_run:{run.id}",
        tests=len(tests),
        status=status,
    )
    tc_events.results_failed(
        session, run, tests, status=status, comment=body.comment, defects=defects
    )
    session.commit()
    return Changed(changed=len(tests))


@router.get("/testcases/runs/{run_id:int}/export")
def export_run(run_id: int, principal: CurrentPrincipal, session: SessionDep) -> Response:
    run = _run_for(session, principal, run_id)
    tests = list(
        session.scalars(
            select(TestRunTest)
            .where(TestRunTest.run_id == run.id)
            .order_by(TestRunTest.position, TestRunTest.id)
        )
    )
    items, _ = _test_outs(session, run, tests)
    # The latest result with a status of each test: who ran it, how long, which build.
    latest: dict[int, TestResult] = {
        r.test_id: r
        for r in session.scalars(
            select(TestResult)
            .where(
                TestResult.test_id.in_([t.id for t in tests] or [0]),
                TestResult.status.is_not(None),
            )
            .ext(distinct_on(TestResult.test_id))
            .order_by(TestResult.test_id, TestResult.created_at.desc(), TestResult.id.desc())
        )
    }
    testers = _people(session, {r.created_by for r in latest.values()})
    [out] = _run_outs(session, principal, [run])
    names = tst.labels(session, run.business_project_id)
    rows = []
    for t in items:
        r = latest.get(t.id)
        tester = testers.get(r.created_by) if r and r.created_by else None
        rows.append(
            [
                t.key,
                t.case_key or "",
                t.section or "",
                t.title,
                t.priority,
                content.TYPE_LABEL.get(t.type, t.type),
                names.get(t.status, t.status),
                t.assigned_to.name if t.assigned_to else "",
                (t.tested_at or "")[:16].replace("T", " "),
                tester.name if tester else "",
                content.format_estimate(r.elapsed_s) or "" if r else "",
                (r.version or "") if r else "",
                t.latest_comment or "",
                ", ".join(t.defects),
            ]
        )
    c = out.counts
    header = [
        ("Mã", out.key),
        ("Milestone", out.milestone_name or ""),
        ("Cấu hình", out.config or ""),
        ("Trạng thái", "Đã đóng" if run.completed_at else "Đang mở"),
        (
            "Tiến độ",
            f"{c.total} test: "
            + ", ".join(
                f"{n} {names.get(k, k)}"
                for k, n in c.model_dump().items()
                if k != "total" and (n or k in ("passed", "failed", "untested"))
            ),
        ),
    ]
    return _download(rs.write_xlsx(run.name, header, rows), f"{out.key} {run.name}")


@router.get("/testcases/{case_id:int}/results", response_model=list[CaseResult])
def case_results(
    case_id: int, principal: CurrentPrincipal, session: SessionDep
) -> list[CaseResult]:
    """The 50 latest results of a case, over the runs of the dự án the viewer sees."""
    case = session.get(TestCase, case_id)
    if case is None:
        raise HTTPException(404, "testcase không tồn tại")
    try:
        tc.suite_for(session, principal, case.suite_id)
    except HTTPException:
        raise HTTPException(404, "testcase không tồn tại") from None
    query = (
        select(TestResult, TestRunTest, TestRun)
        .join(TestRunTest, TestRunTest.id == TestResult.test_id)
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(TestRunTest.case_id == case.id)
    )
    seen = principal.visible_projects()
    if not principal.can("tests.view"):
        return []
    if seen is not None:
        query = query.where(TestRun.business_project_id.in_(seen or {0}))
    rows = session.execute(
        query.order_by(TestResult.created_at.desc(), TestResult.id.desc()).limit(50)
    ).all()
    outs = _result_outs(session, [r for r, _t, _run in rows])
    return [
        CaseResult(
            result=o,
            test_id=t.id,
            test_key=content.key("test", t.id) or "",
            run_id=run.id,
            run_key=content.key("run", run.id) or "",
            run_name=run.name,
            config=run.config,
            completed=run.completed_at is not None,
        )
        for o, (_r, t, run) in zip(outs, rows, strict=True)
    ]


# -- plans -----------------------------------------------------------------------------------------


def _plan_for(
    session: Session,
    principal: Principal,
    plan_id: int,
    *,
    edit: bool = False,
    lock: bool = False,
) -> TestPlan:
    plan = _lock(session, TestPlan, plan_id) if lock else session.get(TestPlan, plan_id)
    if plan is None or not tc.may_view(principal, plan.business_project_id):
        raise HTTPException(404, "test plan không tồn tại")
    if edit:
        _edit(principal, plan.business_project_id)
    return plan


def _plan_outs(session: Session, principal: Principal, plans: list[TestPlan]) -> list[PlanOut]:
    counts, runs = rs.counts_by(session, TestRun.plan_id, [p.id for p in plans])
    milestones = dict(
        session.execute(
            select(TestMilestone.id, TestMilestone.name).where(
                TestMilestone.id.in_({p.milestone_id for p in plans if p.milestone_id} or {0})
            )
        ).all()
    )
    return [
        PlanOut(
            id=p.id,
            key=content.key("plan", p.id) or "",
            business_project_id=p.business_project_id,
            name=p.name,
            description=p.description,
            milestone_id=p.milestone_id,
            milestone_name=milestones.get(p.milestone_id) if p.milestone_id else None,
            completed_at=_iso(p.completed_at),
            created_at=_iso(p.created_at),
            counts=_counts(counts.get(p.id)),
            runs=runs.get(p.id, 0),
            can_edit=tc.may_manage(principal, p.business_project_id),
        )
        for p in plans
    ]


def _plan_detail(session: Session, principal: Principal, plan: TestPlan) -> PlanDetail:
    [base] = _plan_outs(session, principal, [plan])
    runs = list(
        session.scalars(select(TestRun).where(TestRun.plan_id == plan.id).order_by(TestRun.id))
    )
    return PlanDetail(**base.model_dump(exclude={"runs"}), runs=_run_outs(session, principal, runs))


def _add_entries(
    session: Session,
    principal: Principal,
    plan: TestPlan,
    entries: list[PlanEntry],
    today: date,
    settings: Settings,
) -> int:
    """One run per entry and combination of its configurations. Everything is checked (run
    count, test count, assignees) before any run is made; each entry's cases and their content
    are read once and shared by all its runs."""
    bp_id = plan.business_project_id
    existing = int(
        session.scalar(select(func.count(TestRun.id)).where(TestRun.plan_id == plan.id)) or 0
    )
    planned = [(entry, rs.combinations(session, bp_id, entry.config_ids)) for entry in entries]
    total = existing + sum(len(combos) for _e, combos in planned)
    if total > rs.PLAN_RUNS_MAX:
        raise HTTPException(422, f"một test plan có tối đa {rs.PLAN_RUNS_MAX} test run")
    now = _now()
    resolved = [
        (entry, combos, rs.resolve(session, bp_id, entry.selection.picked(), now))
        for entry, combos in planned
    ]
    tests = sum(len(combos) * len(chosen.cases) for _e, combos, chosen in resolved)
    if tests > rs.PLAN_TESTS_MAX:
        raise HTTPException(
            422,
            f"lựa chọn này tạo {tests} test (số testcase x số tổ hợp cấu hình), tối đa "
            f"{rs.PLAN_TESTS_MAX} test mỗi lần: bớt cấu hình hoặc chia nhỏ test plan",
        )
    assignees = [_person(session, settings, bp_id, e.assigned_to) for e, _c, _ch in resolved]
    made = 0
    for (entry, combos, chosen), assignee in zip(resolved, assignees, strict=True):
        shown = rs.live_contents(session, chosen.cases)
        for combo in combos:
            _new_run(
                session,
                principal,
                plan.business_project_id,
                entry.selection,
                name=entry.name,
                today=today,
                milestone_id=plan.milestone_id,
                assigned_to=assignee,
                plan=plan,
                configs=combo,
                chosen=chosen,
                shown=shown,
            )
            made += 1
    return made


@router.get("/testcases/plans", response_model=list[PlanOut])
def list_plans(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    state: Literal["open", "completed", "all"] = "all",
) -> list[PlanOut]:
    bp = tc.project_for(session, principal, business_project_id)
    query = select(TestPlan).where(TestPlan.business_project_id == bp.id)
    if state == "open":
        query = query.where(TestPlan.completed_at.is_(None))
    elif state == "completed":
        query = query.where(TestPlan.completed_at.is_not(None))
    plans = list(session.scalars(query.order_by(TestPlan.created_at.desc(), TestPlan.id.desc())))
    return _plan_outs(session, principal, plans)


@router.post("/testcases/plans", response_model=PlanDetail, status_code=201)
def create_plan(
    body: PlanIn, principal: CurrentPrincipal, session: SessionDep, settings: SettingsDep
) -> PlanDetail:
    bp = tc.project_for(session, principal, body.business_project_id, manage=True)
    plan = TestPlan(
        business_project_id=bp.id,
        name=_name(body.name),
        description=rs.text(body.description),
        milestone_id=_milestone(session, bp.id, body.milestone_id),
        created_by=principal.user_id,
    )
    session.add(plan)
    session.flush()
    made = _add_entries(session, principal, plan, body.entries, _today(settings), settings)
    audit(session, principal.user_id, "testplan.create", f"test_plan:{plan.id}", runs=made)
    session.commit()
    return _plan_detail(session, principal, plan)


@router.get("/testcases/plans/{plan_id:int}", response_model=PlanDetail)
def get_plan(plan_id: int, principal: CurrentPrincipal, session: SessionDep) -> PlanDetail:
    return _plan_detail(session, principal, _plan_for(session, principal, plan_id))


@router.patch("/testcases/plans/{plan_id:int}", response_model=PlanDetail)
@busy_as_409(BUSY)
def update_plan(
    plan_id: int,
    body: PlanPatch,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> PlanDetail:
    short_lock_wait(session)
    plan = _plan_for(session, principal, plan_id, edit=True, lock=True)
    if plan.completed_at is not None:
        raise HTTPException(409, CLOSED_PLAN)
    if body.name is not None:
        plan.name = _name(body.name)
    if "description" in body.model_fields_set:
        plan.description = rs.text(body.description)
    if "milestone_id" in body.model_fields_set:
        plan.milestone_id = _milestone(session, plan.business_project_id, body.milestone_id)
        # Closed runs keep the milestone they were closed under.
        for run in session.scalars(
            select(TestRun).where(TestRun.plan_id == plan.id, TestRun.completed_at.is_(None))
        ):
            run.milestone_id = plan.milestone_id
    if body.add_entries:
        made = _add_entries(session, principal, plan, body.add_entries, _today(settings), settings)
        audit(session, principal.user_id, "testplan.add_runs", f"test_plan:{plan.id}", runs=made)
    session.commit()
    return _plan_detail(session, principal, plan)


@router.post("/testcases/plans/{plan_id:int}/close", response_model=PlanDetail)
@busy_as_409(BUSY)
def close_plan(plan_id: int, principal: CurrentPrincipal, session: SessionDep) -> PlanDetail:
    short_lock_wait(session)
    plan = _plan_for(session, principal, plan_id, edit=True, lock=True)
    if plan.completed_at is not None:
        raise HTTPException(409, CLOSED_PLAN)
    now = _now()
    for run in session.scalars(
        select(TestRun)
        .where(TestRun.plan_id == plan.id, TestRun.completed_at.is_(None))
        .with_for_update()
    ):
        rs.close_run(session, run, now)
    plan.completed_at = now
    audit(session, principal.user_id, "testplan.close", f"test_plan:{plan.id}")
    tc_events.plan_closed(session, plan)  # docs/specs/30 §15: webhook plan.closed
    session.commit()
    return _plan_detail(session, principal, plan)


@router.delete("/testcases/plans/{plan_id:int}", status_code=204)
@busy_as_409(BUSY)
def delete_plan(plan_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    short_lock_wait(session)
    plan = _plan_for(session, principal, plan_id, edit=True, lock=True)
    audit(session, principal.user_id, "testplan.delete", f"test_plan:{plan.id}", name=plan.name)
    session.delete(plan)
    session.commit()
