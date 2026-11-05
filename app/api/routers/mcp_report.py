"""Tích hợp MCP, the report (docs/specs/26 §13): how people use the MCP server and how their close
requests sent through it fare, per member over a period, against the period before and against
their requests sent on the web; the close-request log, the tool-call log (admins) and an Excel
workbook.

Whose numbers one sees is the scope of `report.mcp` (like `report.member`): people by
`person_scope`, their work (close requests, backlog) by `work_scope` on reviews. Every number is
aggregated in SQL, one query per kind of fact, with a ROLLUP row for the total. A coaching
signal only: none of it counts in DPI or review scores.
"""

import io
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import (
    Boolean,
    ColumnElement,
    Float,
    Select,
    and_,
    case,
    cast,
    false,
    func,
    literal_column,
    not_,
    or_,
    select,
)
from sqlalchemy.orm import Session

from app.api.access import team_reach, whole_projects, work_scope
from app.api.audit import audit
from app.api.deps import get_session
from app.api.personal import person_scope, sees_team
from app.api.routers.evaluation import _team_members
from app.api.routers.member_report import ScopeFilters, sheet_title, xlsx_row
from app.api.security import AdminPrincipal, Principal, require
from app.config import Settings, get_settings
from app.models import (
    ApiToken,
    Commit,
    FindingClosure,
    McpCall,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    Team,
    User,
)
from app.review.status import SEVERE

router = APIRouter(tags=["mcp-report"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
Viewer = Annotated[Principal, require("report.mcp")]

PAGE_SIZE = 50
EXPORT_ROWS = 5000
# Close-request outcomes (docs/specs/26 §13.3): `pending` gathers what is still in flight.
BUCKETS: dict[str, tuple[str, ...]] = {
    "closed": ("closed",),
    "unmet": ("unmet",),
    "rejected": ("rejected",),
    "pending": ("pending", "checking", "proposed"),
    "cancelled": ("cancelled",),
    "failed": ("failed",),
}
# A request the checker or the author settled: the denominator of pass rates (a failed check is
# the system's, not the author's; in-flight ones are not settled yet).
SETTLED = ("closed", "unmet", "rejected", "cancelled")
READ_ONE = ("get_finding",)
READ_MANY = ("get_commit_findings", "get_mr_findings")
NOT_IN_SCORES = (
    "Chỉ số Claude Code không cộng vào điểm DPI hay điểm review: là tín hiệu coaching, dùng "
    "hay không dùng Claude Code không làm ai bị trừ điểm."
)


# -- filters ---------------------------------------------------------------------------------------


class McpFilters(ScopeFilters):
    """The member report's period / dự án / repo, plus team, member and severity, and whose
    rows the viewer's `report.mcp` covers."""

    def __init__(
        self,
        session: Session,
        principal: Principal,
        settings: Settings,
        date_from: date | None,
        date_to: date | None,
        business_project_id: int | None,
        project_id: int | None,
        team_id: int | None,
        user_id: int | None,
        severity: str | None,
    ) -> None:
        super().__init__(
            session, principal, settings, date_from, date_to, business_project_id, project_id
        )
        wanted = [s.strip().lower() for s in (severity or "").split(",") if s.strip()]
        if wrong := sorted(set(wanted) - set(SEVERE)):
            raise HTTPException(422, f"severity chỉ nhận critical/high (không có {wrong})")
        self.severities = tuple(s for s in SEVERE if s in wanted) or SEVERE
        scope = person_scope(session, principal, "report.mcp")
        people: set[int] | None = scope
        self.team: Team | None = None
        if team_id is not None:
            if not sees_team(principal, team_id):
                raise HTTPException(403, "not allowed to see this team")
            self.team = session.get(Team, team_id)
            if self.team is None:
                raise HTTPException(404, "team not found")
            members = _team_members(session, team_id, self.start, self.end)
            people = members if people is None else people & members
        self.user_id = user_id
        if user_id is not None:
            if scope is not None and user_id not in scope:
                raise HTTPException(
                    403, "individual metrics are visible to the person, their lead and managers"
                )
            people = {user_id} if people is None else people & {user_id}
        self.people = people  # None: everyone (admin / "Quản lý tổng")
        self.work = work_scope(principal, "report.mcp")
        self.calls = call_scope(principal)

    def as_dict(self) -> dict[str, Any]:
        return {
            **super().as_dict(),
            "team_id": self.team.id if self.team else None,
            "team_name": self.team.name if self.team else None,
            "user_id": self.user_id,
            "severity": list(self.severities),
        }

    def audit_args(self) -> dict[str, Any]:
        return {
            **super().audit_args(),
            **({"team_id": self.team.id} if self.team else {}),
            **({"user_id": self.user_id} if self.user_id is not None else {}),
            **({"severity": list(self.severities)} if self.severities != SEVERE else {}),
        }


def _filters(
    session: Session,
    principal: Principal,
    settings: Settings,
    date_from: date | None,
    date_to: date | None,
    business_project_id: int | None,
    project_id: int | None,
    team_id: int | None,
    user_id: int | None,
    severity: str | None,
) -> McpFilters:
    return McpFilters(
        session,
        principal,
        settings,
        date_from,
        date_to,
        business_project_id,
        project_id,
        team_id,
        user_id,
        severity,
    )


# -- SQL pieces ------------------------------------------------------------------------------------


def _people(column: Any, f: McpFilters) -> ColumnElement[bool]:
    return and_(column.in_(f.people) if f.people is not None else column.is_not(None))


def _repos_of(bp_ids: set[int]) -> ColumnElement[bool]:
    return McpCall.project_id.in_(select(Project.id).where(Project.business_project_id.in_(bp_ids)))


def call_scope(principal: Principal) -> ColumnElement[bool] | None:
    """`work_scope(report.mcp)` for tool calls: one's own calls; calls about a repo of a dự án
    where `report.mcp` covers everyone's work; calls of the people one leads about a repo of a
    dự án one sees; and calls about no repo (counted only for people in the person scope,
    `_people`). A manager at "project" scope does not see calls about a dự án they only view.
    None: nothing is hidden (admin)."""
    if principal.is_admin:
        return None
    if principal.scope("report.mcp") is None:
        return false()
    allowed: list[ColumnElement[bool]] = [
        McpCall.user_id == principal.user_id,
        McpCall.project_id.is_(None),
    ]
    whole = whole_projects(principal, "report.mcp") or set()
    if whole:
        allowed.append(_repos_of(whole))
    viewed = (principal.visible_projects() or set()) - whole
    people = team_reach(principal, "report.mcp")
    if viewed and people:
        allowed.append(and_(McpCall.user_id.in_(people), _repos_of(viewed)))
    return or_(*allowed)


def _calls_where(f: McpFilters, lo: datetime, hi: datetime) -> list[ColumnElement[bool]]:
    """Calls of the period by people in scope; a dự án / repo filter keeps the calls about one
    of its repos; a call about a repo outside the viewer's `report.mcp` is left out
    (`call_scope`)."""
    where: list[ColumnElement[bool]] = [
        McpCall.created_at >= lo,
        McpCall.created_at < hi,
        _people(McpCall.user_id, f),
    ]
    if f.project_id is not None:
        where.append(McpCall.project_id == f.project_id)
    elif f.bp_id is not None:
        where.append(
            McpCall.project_id.in_(select(Project.id).where(Project.business_project_id == f.bp_id))
        )
    if f.calls is not None:
        where.append(f.calls)
    return where


def _repo_id() -> Any:
    return func.coalesce(MergeRequest.project_id, Commit.project_id)


def _with_repo(query: Select[Any]) -> Select[Any]:
    """A query on `reviews` joined to its MR / commit and repo."""
    return (
        query.outerjoin(MergeRequest, MergeRequest.id == Review.mr_id)
        .outerjoin(Commit, Commit.sha == Review.commit_sha)
        .outerjoin(Project, Project.id == _repo_id())
    )


def _work_where(f: McpFilters) -> list[ColumnElement[bool]]:
    """Reviews the viewer's `report.mcp` covers, in the dự án / repo chosen."""
    where: list[ColumnElement[bool]] = []
    if f.work is not None:
        where.append(f.work)
    if f.project_id is not None:
        where.append(Project.id == f.project_id)
    if f.bp_id is not None:
        where.append(Project.business_project_id == f.bp_id)
    return where


def _ranked() -> Any:
    """Every MCP close request with its rank among the finding's MCP requests (1 = first)."""
    return (
        select(
            FindingClosure.id.label("id"),
            func.row_number()
            .over(
                partition_by=FindingClosure.finding_id,
                order_by=(FindingClosure.created_at, FindingClosure.id),
            )
            .label("rn"),
        )
        .where(FindingClosure.kind == "request", FindingClosure.via == "mcp")
        .subquery()
    )


def _severity() -> Any:
    # A request closed on an argument lowers the finding: judge it by its severity before.
    return func.coalesce(FindingClosure.severity_before, ReviewFinding.severity)


def _requests_query(
    columns: list[Any], f: McpFilters, lo: datetime, hi: datetime, via: str
) -> Select[Any]:
    query = (
        select(*columns)
        .select_from(FindingClosure)
        .join(ReviewFinding, ReviewFinding.id == FindingClosure.finding_id)
        .join(Review, Review.id == ReviewFinding.review_id)
    )
    return _with_repo(query).where(
        FindingClosure.kind == "request",
        FindingClosure.via == via,
        FindingClosure.created_at >= lo,
        FindingClosure.created_at < hi,
        _people(FindingClosure.requested_by, f),
        _severity().in_(f.severities),
        *_work_where(f),
    )


def _hours(later: Any, earlier: Any) -> Any:
    return func.extract("epoch", later - earlier) / 3600.0


def _median(value: Any) -> Any:
    # NULLs (rows the median is not about) are ignored by percentile_cont.
    return func.percentile_cont(0.5).within_group(cast(value, Float))


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _round(value: Any) -> float | None:
    return round(float(value), 2) if value is not None else None


# -- aggregates (ROLLUP: one row per person, and the total under key None) ---------------------


def _calls_by_person(
    session: Session, f: McpFilters, lo: datetime, hi: datetime
) -> dict[int | None, dict[str, Any]]:
    who = McpCall.user_id
    rate_limited = McpCall.error_code == "rate_limited"
    day = func.date(func.timezone(f.tz.key, McpCall.created_at))
    rows = session.execute(
        select(
            func.grouping(who).label("total"),
            who,
            func.count().label("calls"),
            func.count(func.distinct(who)).label("users"),
            func.count(func.distinct(day)).label("days"),
            func.count(func.distinct(McpCall.finding_id))
            .filter(McpCall.tool.in_(READ_ONE), McpCall.ok.is_(True))
            .label("read_one"),
            func.coalesce(
                func.sum(McpCall.result_count).filter(
                    McpCall.tool.in_(READ_MANY), McpCall.ok.is_(True)
                ),
                0,
            ).label("read_many"),
            func.count().filter(McpCall.ok.is_(False), ~rate_limited).label("errors"),
            func.count().filter(rate_limited).label("rate_limited"),
        )
        .where(*_calls_where(f, lo, hi))
        .group_by(func.rollup(who))
    ).all()
    out: dict[int | None, dict[str, Any]] = {}
    for r in rows:
        counted = int(r.calls) - int(r.rate_limited)
        out[None if r.total else int(r.user_id)] = {
            "calls": int(r.calls),
            "active_users": int(r.users),
            "active_days": int(r.days),
            "findings_read": int(r.read_one) + int(r.read_many),
            "error_rate": _rate(int(r.errors), counted),
            "rate_limited": int(r.rate_limited),
        }
    return out


def _mcp_requests_by_person(
    session: Session, f: McpFilters, lo: datetime, hi: datetime
) -> dict[int | None, dict[str, Any]]:
    ranked = _ranked()
    who = FindingClosure.requested_by
    status = FindingClosure.status
    first = ranked.c.rn == 1
    tests = FindingClosure.meta["tests"]
    has_tests = (
        case((func.jsonb_typeof(tests) == "array", func.jsonb_array_length(tests)), else_=0) > 0
    )
    any_not_passed = func.jsonb_path_exists(
        tests, literal_column("""'$[*] ? (@.result != "passed")'::jsonpath"""), type_=Boolean
    )
    columns: list[Any] = [
        func.grouping(who).label("total"),
        who.label("user_id"),
        *(func.count().filter(status.in_(s)).label(k) for k, s in BUCKETS.items()),
        func.count().filter(first, status.in_(SETTLED)).label("first_settled"),
        func.count().filter(first, status == "closed").label("first_closed"),
        func.count().filter(ranked.c.rn >= 2).label("resubmits"),
        _median(case((first, _hours(FindingClosure.created_at, Review.finished_at)))).label(
            "to_request"
        ),
        _median(
            case((status == "closed", _hours(FindingClosure.decided_at, Review.finished_at)))
        ).label("to_close"),
        func.count().label("requests"),
        func.count().filter(has_tests).label("with_tests"),
        # The path is only evaluated on an array (CASE keeps the order).
        func.count()
        .filter(case((has_tests, not_(any_not_passed)), else_=False))
        .label("tests_passed"),
    ]
    query = _requests_query(columns, f, lo, hi, "mcp").join(
        ranked, ranked.c.id == FindingClosure.id
    )
    out: dict[int | None, dict[str, Any]] = {}
    for r in session.execute(query.group_by(func.rollup(who))).all():
        out[None if r.total else int(r.user_id)] = {
            "requests": {k: int(getattr(r, k)) for k in BUCKETS},
            "request_count": int(r.requests),
            "first_pass_rate": _rate(int(r.first_closed), int(r.first_settled)),
            "resubmits": int(r.resubmits),
            "median_hours_to_request": _round(r.to_request),
            "median_hours_to_close": _round(r.to_close),
            "with_tests_rate": _rate(int(r.with_tests), int(r.requests)),
            "tests_pass_rate": _rate(int(r.tests_passed), int(r.with_tests)),
        }
    return out


def _web_by_person(
    session: Session, f: McpFilters, lo: datetime, hi: datetime, ids: set[int]
) -> dict[int | None, dict[str, Any]]:
    if not ids:
        return {}
    who = FindingClosure.requested_by
    status = FindingClosure.status
    columns = [
        func.grouping(who).label("total"),
        who.label("user_id"),
        func.count().label("requests"),
        func.count().filter(status == "closed").label("closed"),
        func.count().filter(status.in_(SETTLED)).label("settled"),
    ]
    query = _requests_query(columns, f, lo, hi, "web").where(who.in_(ids))
    return {
        (None if r.total else int(r.user_id)): {
            "requests": int(r.requests),
            "closed": int(r.closed),
            "pass_rate": _rate(int(r.closed), int(r.settled)),
        }
        for r in session.execute(query.group_by(func.rollup(who))).all()
    }


def _backlog_by_person(session: Session, f: McpFilters, ids: set[int]) -> dict[int | None, int]:
    """Critical/high still open now (as MCP's list_findings sees them), whatever the period."""
    if not ids:
        return {}
    who = Review.author_user_id
    query = _with_repo(
        select(func.grouping(who).label("total"), who, func.count().label("n"))
        .select_from(ReviewFinding)
        .join(Review, Review.id == ReviewFinding.review_id)
    ).where(
        Review.status == "done",
        who.in_(ids),
        ReviewFinding.severity.in_(f.severities),
        ReviewFinding.status == "open",
        ReviewFinding.verified.is_distinct_from("rejected"),
        *_work_where(f),
    )
    return {
        (None if r.total else int(r.author_user_id)): int(r.n)
        for r in session.execute(query.group_by(func.rollup(who))).all()
    }


EMPTY_CALLS: dict[str, Any] = {
    "calls": 0,
    "active_users": 0,
    "active_days": 0,
    "findings_read": 0,
    "error_rate": None,
    "rate_limited": 0,
}
EMPTY_REQUESTS: dict[str, Any] = {
    "requests": dict.fromkeys(BUCKETS, 0),
    "request_count": 0,
    "first_pass_rate": None,
    "resubmits": 0,
    "median_hours_to_request": None,
    "median_hours_to_close": None,
    "with_tests_rate": None,
    "tests_pass_rate": None,
}
EMPTY_WEB: dict[str, Any] = {"requests": 0, "closed": 0, "pass_rate": None}


def _row(
    key: int | None,
    calls: dict[int | None, dict[str, Any]],
    requests: dict[int | None, dict[str, Any]],
    web: dict[int | None, dict[str, Any]],
    backlog: dict[int | None, int],
    user: User | None,
) -> dict[str, Any]:
    c = calls.get(key, EMPTY_CALLS)
    r = requests.get(key, EMPTY_REQUESTS)
    return {
        "user_id": key,
        "name": (user.full_name or user.primary_email)
        if user
        else ("Tổng" if key is None else None),
        "email": user.primary_email if user else None,
        "calls": c["calls"],
        "active_days": c["active_days"],
        "findings_read": c["findings_read"],
        "error_rate": c["error_rate"],
        "rate_limited": c["rate_limited"],
        "requests": dict(r["requests"]),
        "first_pass_rate": r["first_pass_rate"],
        "resubmits": r["resubmits"],
        "median_hours_to_request": r["median_hours_to_request"],
        "median_hours_to_close": r["median_hours_to_close"],
        "with_tests_rate": r["with_tests_rate"],
        "tests_pass_rate": r["tests_pass_rate"],
        "backlog": backlog.get(key, 0),
        "web": dict(web.get(key, EMPTY_WEB)),
    }


def _kpis(
    calls: dict[int | None, dict[str, Any]], requests: dict[int | None, dict[str, Any]]
) -> dict[str, Any]:
    c = calls.get(None, EMPTY_CALLS)
    r = requests.get(None, EMPTY_REQUESTS)
    return {
        "calls": c["calls"],
        "active_users": c["active_users"],
        "requests": r["request_count"],
        "first_pass_rate": r["first_pass_rate"],
        "median_hours_to_close": r["median_hours_to_close"],
    }


def build_summary(session: Session, f: McpFilters) -> dict[str, Any]:
    lo, hi = f.bounds(f.start, f.end)
    calls = _calls_by_person(session, f, lo, hi)
    requests = _mcp_requests_by_person(session, f, lo, hi)
    # A row per person who used the MCP server in the period (calls or requests), and the
    # person asked for even when they did not.
    ids = {k for k in (*calls, *requests) if k is not None}
    if f.user_id is not None and (f.people is None or f.user_id in f.people):
        ids.add(f.user_id)
    web = _web_by_person(session, f, lo, hi, ids)
    backlog = _backlog_by_person(session, f, ids)
    users = {u.id: u for u in session.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    members = [_row(i, calls, requests, web, backlog, users.get(i)) for i in ids]
    members.sort(key=lambda m: (-m["calls"], -sum(m["requests"].values()), m["name"] or ""))
    prev_lo, prev_hi = f.bounds(f.prev_start, f.prev_end)
    previous = _kpis(
        _calls_by_person(session, f, prev_lo, prev_hi),
        _mcp_requests_by_person(session, f, prev_lo, prev_hi),
    )
    return {
        "period": {"from": f.start.isoformat(), "to": f.end.isoformat()},
        "previous": {"from": f.prev_start.isoformat(), "to": f.prev_end.isoformat()},
        "filters": f.as_dict(),
        "kpis": {**_kpis(calls, requests), "prev": previous},
        "members": members,
        "total": _row(None, calls, requests, web, backlog, None),
        "note": NOT_IN_SCORES,
    }


# -- the close-request log -------------------------------------------------------------------------


def _tests_of(meta: dict[str, Any] | None) -> dict[str, int]:
    runs = (meta or {}).get("tests") or []
    out = {"total": 0, "passed": 0, "failed": 0, "not_run": 0}
    for run in runs if isinstance(runs, list) else []:
        result = run.get("result") if isinstance(run, dict) else None
        out["total"] += 1
        if result in ("passed", "failed", "not_run"):
            out[str(result)] += 1
    return out


def request_rows(
    session: Session, principal: Principal, f: McpFilters, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """MCP close requests of the period, newest first. Finding titles and the checker's reason
    are review content: shown only where the viewer's `review.view` reaches."""
    lo, hi = f.bounds(f.start, f.end)
    ranked = _ranked()
    base = _requests_query(
        [
            FindingClosure,
            ReviewFinding.severity,
            ReviewFinding.title,
            ReviewFinding.file_path,
            ReviewFinding.line_start,
            Review.id.label("review_id"),
            Project.id.label("project_id"),
            Project.path_with_namespace,
            User.full_name,
            User.primary_email,
            ranked.c.rn,
            _severity().label("asked_severity"),
        ],
        f,
        lo,
        hi,
        "mcp",
    )
    base = base.join(ranked, ranked.c.id == FindingClosure.id).outerjoin(
        User, User.id == FindingClosure.requested_by
    )
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = session.execute(
        base.order_by(FindingClosure.created_at.desc(), FindingClosure.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    review_ids = {r.review_id for r in rows}
    readable: set[int] = set()
    if review_ids:
        query = select(Review.id).where(Review.id.in_(review_ids))
        if (allowed := work_scope(principal, "review.view")) is not None:
            query = query.where(allowed)
        readable = set(session.scalars(query))
    items: list[dict[str, Any]] = []
    for r in rows:
        c: FindingClosure = r[0]
        meta = c.meta or {}
        seen = r.review_id in readable
        items.append(
            {
                "id": c.id,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "decided_at": c.decided_at.isoformat() if c.decided_at else None,
                "status": c.status,
                "closed_as": c.closed_as,
                "reason": c.reason if seen else None,
                "attempt": int(r.rn),
                "finding": {
                    "id": c.finding_id,
                    "severity": r.asked_severity,
                    "title": r.title if seen else None,
                    "file_path": r.file_path if seen else None,
                    "line_start": r.line_start if seen else None,
                },
                "review_id": r.review_id,
                "project": (
                    {"id": r.project_id, "path": r.path_with_namespace}
                    if r.project_id is not None
                    else None
                ),
                "user": {
                    "id": c.requested_by,
                    "name": r.full_name or r.primary_email,
                    "email": r.primary_email,
                },
                "commits": list(c.shas or []),
                "files_changed": len(meta.get("files_changed") or []),
                "tests": _tests_of(meta),
                "token_name": meta.get("token_name"),
                "client": meta.get("client"),
            }
        )
    return items, total


# -- the tool-call log (admins) --------------------------------------------------------------------


def call_rows(
    session: Session,
    f: McpFilters,
    tool: str | None,
    ok: bool | None,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    lo, hi = f.bounds(f.start, f.end)
    where = _calls_where(f, lo, hi)
    if tool:
        where.append(McpCall.tool == tool)
    if ok is not None:
        where.append(McpCall.ok.is_(ok))
    total = int(session.scalar(select(func.count()).select_from(McpCall).where(*where)) or 0)
    rows = session.execute(
        select(
            McpCall,
            User.full_name,
            User.primary_email,
            ApiToken.name.label("token_name"),
            Project.path_with_namespace,
        )
        .outerjoin(User, User.id == McpCall.user_id)
        .outerjoin(ApiToken, ApiToken.id == McpCall.token_id)
        .outerjoin(Project, Project.id == McpCall.project_id)
        .where(*where)
        .order_by(McpCall.created_at.desc(), McpCall.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    items = [
        {
            "id": c.id,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "user": {"id": c.user_id, "name": name or email, "email": email},
            "token_id": c.token_id,
            "token_name": token_name,
            "tool": c.tool,
            "ok": c.ok,
            "error_code": c.error_code,
            "duration_ms": c.duration_ms,
            "project": {"id": c.project_id, "path": path} if c.project_id is not None else None,
            "finding_id": c.finding_id,
            "closure_id": c.closure_id,
            "commit_sha": c.commit_sha,
            "result_count": c.result_count,
            "client": c.client,
        }
        for c, name, email, token_name, path in rows
    ]
    return items, total


# -- endpoints -------------------------------------------------------------------------------------

From = Annotated[date | None, Query(alias="from")]
To = Annotated[date | None, Query(alias="to")]
Page = Annotated[int, Query(ge=1, le=10_000)]


@router.get("/mcp-report/summary")
def summary(
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    date_from: From = None,
    date_to: To = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
    team_id: int | None = None,
    user_id: int | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    f = _filters(
        session,
        principal,
        settings,
        date_from,
        date_to,
        business_project_id,
        project_id,
        team_id,
        user_id,
        severity,
    )
    report = build_summary(session, f)
    audit(session, principal.user_id, "mcp_report.read", "mcp-report", **f.audit_args())
    session.commit()
    return report


@router.get("/mcp-report/requests")
def requests_log(
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    date_from: From = None,
    date_to: To = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
    team_id: int | None = None,
    user_id: int | None = None,
    severity: str | None = None,
    page: Page = 1,
) -> dict[str, Any]:
    f = _filters(
        session,
        principal,
        settings,
        date_from,
        date_to,
        business_project_id,
        project_id,
        team_id,
        user_id,
        severity,
    )
    items, total = request_rows(session, principal, f, (page - 1) * PAGE_SIZE, PAGE_SIZE)
    return {"items": items, "total": total, "page": page, "page_size": PAGE_SIZE}


@router.get("/mcp-report/calls")
def calls_log(
    principal: AdminPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    date_from: From = None,
    date_to: To = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
    team_id: int | None = None,
    user_id: int | None = None,
    severity: str | None = None,
    tool: str | None = None,
    ok: bool | None = None,
    page: Page = 1,
) -> dict[str, Any]:
    """The tool-call log (docs/specs/26 §13.1): admins only."""
    f = _filters(
        session,
        principal,
        settings,
        date_from,
        date_to,
        business_project_id,
        project_id,
        team_id,
        user_id,
        severity,
    )
    items, total = call_rows(session, f, tool, ok, (page - 1) * PAGE_SIZE, PAGE_SIZE)
    return {"items": items, "total": total, "page": page, "page_size": PAGE_SIZE}


@router.get("/mcp-report/export.xlsx")
def export_xlsx(
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    date_from: From = None,
    date_to: To = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
    team_id: int | None = None,
    user_id: int | None = None,
    severity: str | None = None,
) -> Response:
    """Both tables of the page as a workbook (docs/specs/26 §13.4): exporting needs only the
    view (`report.mcp`)."""
    f = _filters(
        session,
        principal,
        settings,
        date_from,
        date_to,
        business_project_id,
        project_id,
        team_id,
        user_id,
        severity,
    )
    report = build_summary(session, f)
    requests, _ = request_rows(session, principal, f, 0, EXPORT_ROWS)
    body = workbook(f, report, requests)
    audit(session, principal.user_id, "mcp_report.export", "mcp-report", **f.audit_args())
    session.commit()
    filename = f"codepulse-tich-hop-mcp-{f.start:%Y%m%d}-{f.end:%Y%m%d}.xlsx"
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# -- the workbook ----------------------------------------------------------------------------------

MEMBER_SHEET = "Theo thành viên"
REQUEST_SHEET = "Yêu cầu đóng qua MCP"


def _pct(rate: float | None) -> float | None:
    return round(rate * 100, 1) if rate is not None else None


def workbook(f: McpFilters, report: dict[str, Any], requests: list[dict[str, Any]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.worksheet import Worksheet

    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="EEF1F5")
    wb = Workbook()

    def sheet(
        title: str, intro: list[str], header: list[str], rows: list[list[Any]], first: bool
    ) -> None:
        ws: Worksheet = wb.active if first else wb.create_sheet()  # type: ignore[assignment]
        ws.title = sheet_title(title)
        for line in intro:
            xlsx_row(ws, [line])
        xlsx_row(ws, [])
        xlsx_row(ws, header)
        for cell in ws[ws.max_row]:
            cell.font = bold
            cell.fill = head_fill
        ws.freeze_panes = f"A{ws.max_row + 1}"
        for row in rows:
            xlsx_row(ws, row)
        for i, h in enumerate(header, start=1):
            width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r)])
            ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 50)
        ws["A1"].font = Font(bold=True, size=14)

    period = f"Khoảng: {f.start:%d/%m/%Y} - {f.end:%d/%m/%Y}"
    where = " / ".join(
        x
        for x in (
            f.team.name if f.team else None,
            f.business.name if f.business else None,
            f.repo.path_with_namespace if f.repo else None,
            "mức: " + ", ".join(f.severities),
        )
        if x
    )

    def member(m: dict[str, Any]) -> list[Any]:
        r, w = m["requests"], m["web"]
        return [
            m["name"] or (f"#{m['user_id']}" if m["user_id"] is not None else ""),
            m["email"] or "",
            m["calls"],
            m["active_days"],
            m["findings_read"],
            _pct(m["error_rate"]),
            m["rate_limited"],
            *(r[k] for k in BUCKETS),
            _pct(m["first_pass_rate"]),
            m["resubmits"],
            m["median_hours_to_request"],
            m["median_hours_to_close"],
            _pct(m["with_tests_rate"]),
            _pct(m["tests_pass_rate"]),
            m["backlog"],
            w["requests"],
            w["closed"],
            _pct(w["pass_rate"]),
        ]

    sheet(
        MEMBER_SHEET,
        [f"Tích hợp MCP (Claude Code) theo thành viên · {where}", period, NOT_IN_SCORES],
        [
            "Thành viên",
            "Email",
            "Lần gọi tool",
            "Ngày hoạt động",
            "Finding đã đọc",
            "Tỉ lệ lỗi gọi (%)",
            "Bị rate_limited",
            "YC đã đóng",
            "YC chưa đạt",
            "YC bị từ chối",
            "YC đang chờ",
            "YC đã rút",
            "YC lỗi kiểm",
            "Đạt lần đầu (%)",
            "Lần gửi lại",
            "Trung vị giờ đến khi gửi",
            "Trung vị giờ đến khi đóng",
            "Có kèm test (%)",
            "Test pass (%)",
            "Backlog critical/high",
            "YC qua web",
            "YC web đã đóng",
            "Tỉ lệ đạt web (%)",
        ],
        [member(m) for m in report["members"]] + [member(report["total"])],
        first=True,
    )
    sheet(
        REQUEST_SHEET,
        [f"Yêu cầu đóng gửi qua Claude Code (MCP) · {where}", period],
        [
            "Thời điểm",
            "Finding",
            "Mức",
            "Tiêu đề",
            "Repo",
            "Thành viên",
            "Commit",
            "Số file",
            "Test (pass/tổng)",
            "Trạng thái",
            "Lần gửi",
            "Lý do của bước kiểm",
            "Review",
        ],
        [
            [
                r["created_at"] or "",
                r["finding"]["id"],
                r["finding"]["severity"],
                r["finding"]["title"] or "",
                r["project"]["path"] if r["project"] else "",
                r["user"]["name"] or "",
                ", ".join(sha[:12] for sha in r["commits"]),
                r["files_changed"],
                f"{r['tests']['passed']}/{r['tests']['total']}",
                r["status"],
                r["attempt"],
                r["reason"] or "",
                r["review_id"],
            ]
            for r in requests
        ],
        first=False,
    )
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
