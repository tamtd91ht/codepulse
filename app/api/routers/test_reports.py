"""Testcase reports and their schedules (docs/specs/30 §15, G4).

Reports (`GET /testcases/reports/{kind}`, kind = summary | compare | coverage | defects |
productivity | automation | readiness) and their Excel export (`.../{kind}/export`), the filter
options (`/testcases/reports/options`), the report schedules (`/testcases/report-schedules`).

Who: `tests.view` on the dự án reads every report. Productivity per person (and the per-person
rows of the summary) is a coaching signal: whoever has `tests.manage` on the dự án sees every
row, anyone else sees their own row and the totals. Schedules: `tests.manage`.
PDF: the dashboard's print view ("In / Lưu PDF", the browser's print to PDF); the shareable
link is the page URL with the filters in its query string.
"""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.config import get_settings
from app.models import (
    TestConfig,
    TestConfigGroup,
    TestMilestone,
    TestPlan,
    TestReportSchedule,
    TestSuite,
)
from app.testcases import reports as rp
from app.testcases import service as tc

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
Kind = Literal[
    "summary", "compare", "coverage", "defects", "productivity", "automation", "readiness"
]
ScheduleKind = Literal["summary", "coverage", "defects", "automation", "readiness"]
Branch = Annotated[str | None, Query(max_length=200)]
DateFrom = Annotated[date | None, Query(alias="from")]
DateTo = Annotated[date | None, Query(alias="to")]
RunIds = Annotated[str | None, Query(max_length=200, description="2..10 run ids: 1,2,3")]
PassRate = Annotated[float, Query(ge=0, le=100)]


def _filters(
    session: Session,
    principal: Principal,
    business_project_id: int,
    suite_id: int | None,
    milestone_id: int | None,
    plan_id: int | None,
    config_id: int | None,
    branch: str | None,
    date_from: date | None,
    date_to: date | None,
) -> rp.Filters:
    bp = tc.project_for(session, principal, business_project_id)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "ngày bắt đầu sau ngày kết thúc")
    if suite_id is not None:
        suite = session.get(TestSuite, suite_id)
        if suite is None or bp.id not in tc.projects_of(session, suite):
            raise HTTPException(404, "suite not found")
    if milestone_id is not None:
        m = session.get(TestMilestone, milestone_id)
        if m is None or m.business_project_id != bp.id:
            raise HTTPException(404, "milestone not found")
    if plan_id is not None:
        plan = session.get(TestPlan, plan_id)
        if plan is None or plan.business_project_id != bp.id:
            raise HTTPException(404, "plan not found")
    if config_id is not None:
        found = session.scalar(
            select(TestConfig.id)
            .join(TestConfigGroup, TestConfigGroup.id == TestConfig.group_id)
            .where(TestConfig.id == config_id, TestConfigGroup.business_project_id == bp.id)
        )
        if found is None:
            raise HTTPException(404, "config not found")
    return rp.Filters(
        bp_id=bp.id,
        suite_id=suite_id,
        milestone_id=milestone_id,
        plan_id=plan_id,
        config_id=config_id,
        branch=(branch or "").strip() or None,
        date_from=date_from,
        date_to=date_to,
        tz=get_settings().report_tz,
    )


def _run_ids(raw: str | None) -> list[int]:
    try:
        ids = list(dict.fromkeys(int(x) for x in (raw or "").split(",") if x.strip()))
    except ValueError as exc:
        raise HTTPException(422, "run_ids: danh sách id run, cách nhau bởi dấu phẩy") from exc
    if not rp.COMPARE_MIN <= len(ids) <= rp.COMPARE_MAX:
        raise HTTPException(422, f"chọn từ {rp.COMPARE_MIN} đến {rp.COMPARE_MAX} run để so sánh")
    return ids


def _people_visible(
    principal: Principal, bp_id: int, rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    """Every row for a manager of the dự án; the caller's own row (and CI) for anyone else."""
    if tc.may_manage(principal, bp_id):
        return rows, True
    return [r for r in rows if r.get("user_id") in (None, principal.user_id)], False


def _report(
    kind: str,
    session: Session,
    principal: Principal,
    f: rp.Filters,
    run_ids: str | None,
    min_pass_rate: float,
) -> dict[str, Any]:
    data: dict[str, Any] | None
    if kind == "summary":
        data = rp.summary(session, f)
        data["by_person"], data["people_all"] = _people_visible(
            principal, f.bp_id, data["by_person"]
        )
    elif kind == "compare":
        data = rp.compare(session, f.bp_id, _run_ids(run_ids))
        if data is None:
            raise HTTPException(404, "run not found")
    elif kind == "coverage":
        data = rp.coverage(session, f)
    elif kind == "defects":
        data = rp.defects(session, f)
    elif kind == "productivity":
        data = rp.productivity(session, f)
        data["people"], data["people_all"] = _people_visible(principal, f.bp_id, data["people"])
    elif kind == "automation":
        data = rp.automation(session, f)
    else:
        if f.milestone_id is None:
            raise HTTPException(422, "chọn milestone để xem mức sẵn sàng release")
        data = rp.readiness(session, f.bp_id, f.milestone_id, min_pass_rate=min_pass_rate, tz=f.tz)
        if data is None:
            raise HTTPException(404, "milestone not found")
    data["kind"] = kind
    data["generated_at"] = datetime.now(UTC).isoformat()
    return data


@router.get("/testcases/reports/options")
def report_options(
    principal: CurrentPrincipal, session: SessionDep, business_project_id: int
) -> dict[str, Any]:
    """What the filters offer: suites, milestones, plans, configurations, recent runs and CI
    branches, the statuses (built-in and the dự án's own) and whether the caller manages."""
    bp = tc.project_for(session, principal, business_project_id)
    out = rp.options(session, bp.id)
    out["can_manage"] = tc.may_manage(principal, bp.id)
    out["user_id"] = principal.user_id
    return out


@router.get("/testcases/reports/{kind}")
def report(
    kind: Kind,
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    suite_id: int | None = None,
    milestone_id: int | None = None,
    plan_id: int | None = None,
    config_id: int | None = None,
    branch: Branch = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    run_ids: RunIds = None,
    min_pass_rate: PassRate = rp.PASS_RATE_GO,
) -> dict[str, Any]:
    """One report, filtered (docs/specs/30 §15 G4)."""
    f = _filters(
        session,
        principal,
        business_project_id,
        suite_id,
        milestone_id,
        plan_id,
        config_id,
        branch,
        date_from,
        date_to,
    )
    return _report(kind, session, principal, f, run_ids, min_pass_rate)


@router.get("/testcases/reports/{kind}/export")
def report_export(
    kind: Kind,
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    suite_id: int | None = None,
    milestone_id: int | None = None,
    plan_id: int | None = None,
    config_id: int | None = None,
    branch: Branch = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    run_ids: RunIds = None,
    min_pass_rate: PassRate = rp.PASS_RATE_GO,
) -> Response:
    """The report as Excel: one sheet per section, every cell text (no formula)."""
    f = _filters(
        session,
        principal,
        business_project_id,
        suite_id,
        milestone_id,
        plan_id,
        config_id,
        branch,
        date_from,
        date_to,
    )
    data = _report(kind, session, principal, f, run_ids, min_pass_rate)
    meta = rp.status_meta(session, f.bp_id)
    labels = [
        ("Dự án", str(f.bp_id)),
        ("Từ ngày", f.date_from.isoformat() if f.date_from else ""),
        ("Đến ngày", f.date_to.isoformat() if f.date_to else ""),
    ]
    for label, value in (
        ("Bộ", f.suite_id),
        ("Milestone", f.milestone_id),
        ("Plan", f.plan_id),
        ("Cấu hình", f.config_id),
        ("Nhánh", f.branch),
    ):
        if value:
            labels.append((label, str(value)))
    title = f"Báo cáo testcase: {rp.KIND_LABEL[kind]}"
    body = rp.to_xlsx(title, labels, rp.sheets_for(kind, data, meta))
    name = f"bao-cao-{kind}-{date.today().isoformat()}.xlsx"
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


# -- schedules -------------------------------------------------------------------------------------


class ScheduleIn(BaseModel):
    business_project_id: int
    name: str = Field(min_length=1, max_length=200)
    kind: ScheduleKind = "summary"
    params: dict[str, Any] = Field(default_factory=dict)
    cadence: Literal["daily", "weekly"] = "weekly"
    weekday: int | None = Field(default=0, ge=0, le=6)
    hour: int = Field(default=8, ge=0, le=23)
    webhook_id: int | None = None
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: ScheduleKind | None = None
    params: dict[str, Any] | None = None
    cadence: Literal["daily", "weekly"] | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int | None = Field(default=None, ge=0, le=23)
    webhook_id: int | None = None
    enabled: bool | None = None


class ScheduleOut(BaseModel):
    id: int
    business_project_id: int
    name: str
    kind: str
    params: dict[str, Any]
    cadence: str
    weekday: int | None
    hour: int
    webhook_id: int | None
    webhook_name: str | None
    enabled: bool
    last_sent_at: datetime | None


class HookOut(BaseModel):
    id: int
    name: str
    format: str
    enabled: bool


PARAM_KEYS = ("suite_id", "milestone_id", "plan_id", "config_id", "branch", "days", "min_pass_rate")


def _params(session: Session, bp_id: int, raw: dict[str, Any], kind: str) -> dict[str, Any]:
    """Keep the known parameters, checked like the report's filters."""
    out: dict[str, Any] = {}
    for k in PARAM_KEYS:
        v = raw.get(k)
        if v is None or v == "":
            continue
        if k == "branch":
            if not isinstance(v, str) or len(v) > 200:
                raise HTTPException(422, "params.branch: chuỗi tối đa 200 ký tự")
            out[k] = v.strip()
        elif k == "min_pass_rate":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
                raise HTTPException(422, "params.min_pass_rate: 0..100")
            out[k] = float(v)
        else:
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise HTTPException(422, f"params.{k}: số nguyên dương")
            if k == "days" and v > 366:
                raise HTTPException(422, "params.days: tối đa 366")
            out[k] = v
    if "milestone_id" in out:
        m = session.get(TestMilestone, out["milestone_id"])
        if m is None or m.business_project_id != bp_id:
            raise HTTPException(422, "params.milestone_id: không thuộc dự án")
    if "plan_id" in out:
        plan = session.get(TestPlan, out["plan_id"])
        if plan is None or plan.business_project_id != bp_id:
            raise HTTPException(422, "params.plan_id: không thuộc dự án")
    if "suite_id" in out:
        suite = session.get(TestSuite, out["suite_id"])
        if suite is None or bp_id not in tc.projects_of(session, suite):
            raise HTTPException(422, "params.suite_id: không thuộc dự án")
    if kind == "readiness" and "milestone_id" not in out:
        raise HTTPException(422, "báo cáo sẵn sàng release cần milestone (params.milestone_id)")
    return out


def _hook_ok(session: Session, bp_id: int, webhook_id: int | None) -> None:
    if webhook_id is None:
        return
    if webhook_id not in {h.id for h in rp.schedule_hooks(session, bp_id)}:
        raise HTTPException(422, "webhook không dùng được cho dự án này")


def _out(session: Session, s: TestReportSchedule) -> ScheduleOut:
    hooks = {h.id: h.name for h in rp.schedule_hooks(session, s.business_project_id)}
    return ScheduleOut(
        id=s.id,
        business_project_id=s.business_project_id,
        name=s.name,
        kind=s.kind,
        params=dict(s.params or {}),
        cadence=s.cadence,
        weekday=s.weekday,
        hour=s.hour,
        webhook_id=s.webhook_id,
        webhook_name=hooks.get(s.webhook_id) if s.webhook_id else None,
        enabled=s.enabled,
        last_sent_at=s.last_sent_at,
    )


def _schedule(session: Session, principal: Principal, schedule_id: int) -> TestReportSchedule:
    s = session.get(TestReportSchedule, schedule_id)
    if s is None or not tc.may_view(principal, s.business_project_id):
        raise HTTPException(404, "schedule not found")
    tc.project_for(session, principal, s.business_project_id, manage=True)
    return s


@router.get("/testcases/report-schedules/webhooks", response_model=list[HookOut])
def schedule_webhooks(
    principal: CurrentPrincipal, session: SessionDep, business_project_id: int
) -> list[HookOut]:
    """Webhooks a schedule may send to (Admin > Webhook: of the dự án or of everything)."""
    bp = tc.project_for(session, principal, business_project_id, manage=True)
    return [
        HookOut(id=h.id, name=h.name, format=h.format, enabled=bool(h.enabled))
        for h in rp.schedule_hooks(session, bp.id)
    ]


@router.get("/testcases/report-schedules", response_model=list[ScheduleOut])
def list_schedules(
    principal: CurrentPrincipal, session: SessionDep, business_project_id: int
) -> list[ScheduleOut]:
    bp = tc.project_for(session, principal, business_project_id, manage=True)
    rows = session.scalars(
        select(TestReportSchedule)
        .where(TestReportSchedule.business_project_id == bp.id)
        .order_by(TestReportSchedule.id)
    )
    return [_out(session, s) for s in rows]


@router.post("/testcases/report-schedules", response_model=ScheduleOut, status_code=201)
def create_schedule(
    body: ScheduleIn, principal: CurrentPrincipal, session: SessionDep
) -> ScheduleOut:
    bp = tc.project_for(session, principal, body.business_project_id, manage=True)
    _hook_ok(session, bp.id, body.webhook_id)
    s = TestReportSchedule(
        business_project_id=bp.id,
        name=body.name.strip(),
        kind=body.kind,
        params=_params(session, bp.id, body.params, body.kind),
        cadence=body.cadence,
        weekday=(body.weekday or 0) if body.cadence == "weekly" else None,
        hour=body.hour,
        webhook_id=body.webhook_id,
        enabled=body.enabled,
        created_by=principal.user_id,
    )
    session.add(s)
    session.flush()
    audit(session, principal.user_id, "testreportschedule.create", f"test_report_schedule:{s.id}")
    session.commit()
    return _out(session, s)


@router.patch("/testcases/report-schedules/{schedule_id:int}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: int, body: SchedulePatch, principal: CurrentPrincipal, session: SessionDep
) -> ScheduleOut:
    s = _schedule(session, principal, schedule_id)
    sent = body.model_fields_set
    # Everything is checked before the row changes (a refused change leaves it as it was).
    kind = body.kind or s.kind
    params = _params(
        session,
        s.business_project_id,
        body.params if body.params is not None else dict(s.params or {}),
        kind,
    )
    if "webhook_id" in sent:
        _hook_ok(session, s.business_project_id, body.webhook_id)
    if body.name is not None:
        s.name = body.name.strip()
    s.kind, s.params = kind, params
    if body.cadence is not None:
        s.cadence = body.cadence
    if "weekday" in sent or body.cadence is not None:
        s.weekday = (
            (body.weekday if "weekday" in sent else s.weekday) or 0
            if s.cadence == "weekly"
            else None
        )
    if body.hour is not None:
        s.hour = body.hour
    if "webhook_id" in sent:
        s.webhook_id = body.webhook_id
    if body.enabled is not None:
        s.enabled = body.enabled
    audit(session, principal.user_id, "testreportschedule.update", f"test_report_schedule:{s.id}")
    session.commit()
    return _out(session, s)


@router.delete("/testcases/report-schedules/{schedule_id:int}", status_code=204)
def delete_schedule(schedule_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    s = _schedule(session, principal, schedule_id)
    audit(
        session,
        principal.user_id,
        "testreportschedule.delete",
        f"test_report_schedule:{s.id}",
        name=s.name,
    )
    session.delete(s)
    session.commit()


@router.post("/testcases/report-schedules/{schedule_id:int}/send")
def send_schedule_now(
    schedule_id: int, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    """Send the report now (a test of the schedule); counts as today's sending."""
    s = _schedule(session, principal, schedule_id)
    row = rp.send_schedule(session, s, datetime.now(UTC), get_settings().report_tz)
    if row is None:
        raise HTTPException(422, "chưa chọn webhook, hoặc webhook đang tắt")
    text = str(row.payload.get("text") or "")
    session.commit()
    return {"delivery_id": row.id, "text": text}
