"""Timeline by epic (docs/specs/31 §21.3, round 3 stage 3.3): read-only.

One dự án: its epics with their Story / Task / Bug children as bars (start from `started_at`, the
sprint's start or the creation day; end from the due date or the sprint's end), progress, the part
past due, sprint bands, version milestones and the "blocks" links among the bars. Several dự án
(the Manager view of /tasks/overview): epics only.

Nothing here writes: the UI changes a due date or a sprint through the existing task endpoints."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.models import BusinessProject, Sprint, Task, TaskLink, TaskSpace, TaskVersion
from app.tasks import reports, service

DEFAULT_WEEKS_BEFORE = 2
DEFAULT_WEEKS = 12
MAX_DAYS = 366
MAX_EPICS = 100
MAX_LOOSE = 200
MAX_SPACES = 30
CHILD_TYPES = service.POINTED_TYPES


def default_window(today: date) -> tuple[date, date]:
    """From the Monday two weeks back, twelve weeks."""
    start = today - timedelta(days=today.weekday() + 7 * DEFAULT_WEEKS_BEFORE)
    return start, start + timedelta(days=7 * DEFAULT_WEEKS - 1)


def window(frm: date | None, to: date | None, today: date) -> tuple[date, date]:
    """The asked window; ValueError when it is upside down or longer than a year."""
    lo, hi = default_window(today)
    if frm is not None and to is None:
        lo, hi = frm, frm + timedelta(days=7 * DEFAULT_WEEKS - 1)
    elif frm is None and to is not None:
        lo, hi = to - timedelta(days=7 * DEFAULT_WEEKS - 1), to
    elif frm is not None and to is not None:
        lo, hi = frm, to
    if hi < lo:
        raise ValueError("ngày kết thúc trước ngày bắt đầu")
    if (hi - lo).days > MAX_DAYS:
        raise ValueError(f"khoảng tối đa {MAX_DAYS} ngày")
    return lo, hi


def _day(at: datetime | None, tz: ZoneInfo) -> date | None:
    return at.astimezone(tz).date() if at else None


class _Ctx:
    """What every bar of one dự án needs: done statuses, sprints, today."""

    def __init__(self, session: Session, bp_id: int, tz: ZoneInfo, today: date) -> None:
        self.tz, self.today = tz, today
        statuses = service.statuses_of(session, bp_id)
        self.status = {s.id: s for s in statuses}
        self.done = {s.id for s in statuses if s.category == "done"}
        self.sprints = {
            s.id: s
            for s in session.scalars(select(Sprint).where(Sprint.business_project_id == bp_id))
        }

    def is_done(self, t: Task) -> bool:
        return t.status_id in self.done

    def span(self, t: Task) -> tuple[date, date | None, str | None]:
        """(start, end, where the end comes from: "due" / "sprint" / None)."""
        sprint = self.sprints.get(t.sprint_id) if t.sprint_id else None
        start = (
            _day(t.started_at, self.tz)
            or (sprint.start_date if sprint else None)
            or _day(t.created_at, self.tz)
            or self.today
        )
        if t.due_date is not None:
            return start, t.due_date, "due"
        if sprint is not None and sprint.end_date is not None:
            return start, sprint.end_date, "sprint"
        return start, None, None


def _overlaps(start: date, end: date | None, lo: date, hi: date) -> bool:
    return start <= hi and (end or start) >= lo


def _bar(
    ctx: _Ctx, t: Task, names: dict[int, str], p: Principal | None, progress: float | None = None
) -> dict[str, Any]:
    start, end, source = ctx.span(t)
    done = ctx.is_done(t)
    status = ctx.status.get(t.status_id)
    overdue = not done and end is not None and end < ctx.today
    return {
        "id": t.id,
        "key": t.key,
        "title": t.title,
        "type": t.type,
        "status": status.name if status else None,
        "category": status.category if status else None,
        "done": done,
        "assignee": names.get(t.assignee_id) if t.assignee_id else None,
        "points": float(t.story_points) if t.story_points is not None else None,
        "sprint_id": t.sprint_id,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "start": start.isoformat(),
        "end": end.isoformat() if end else None,
        "end_source": source,
        "progress": (1.0 if done else 0.0) if progress is None else progress,
        "overdue": overdue,
        "overdue_from": end.isoformat() if overdue and end else None,
        "blocked_late": False,
        "blockers": [],
        "can_edit": service.may(p, "edit", t) if p is not None else False,
    }


def _epic_progress(ctx: _Ctx, epic: Task, children: list[Task]) -> tuple[float, int]:
    """Done points over all points of the children, or done children over children when none
    is estimated; (progress, done children)."""
    done_kids = [c for c in children if ctx.is_done(c)]
    if not children:
        return (1.0 if ctx.is_done(epic) else 0.0), 0
    total = sum(reports.points(c) for c in children)
    if total > 0:
        got = sum(reports.points(c) for c in done_kids if reports.delivered(c))
        return round(got / total, 3), len(done_kids)
    return round(len(done_kids) / len(children), 3), len(done_kids)


def _epic_span(ctx: _Ctx, epic: Task, children: list[Task], bar: dict[str, Any]) -> None:
    """An epic without its own dates spans its children."""
    spans = [ctx.span(c) for c in children]
    if spans and epic.started_at is None:
        bar["start"] = min(s for s, _, _ in spans).isoformat()
    if epic.due_date is None:
        ends = [e for _, e, _ in spans if e is not None]
        if ends:
            end = max(ends)
            bar["end"], bar["end_source"] = end.isoformat(), "children"
            overdue = not ctx.is_done(epic) and end < ctx.today
            bar["overdue"], bar["overdue_from"] = overdue, end.isoformat() if overdue else None
    if bar["end"] is not None and bar["end"] < bar["start"]:
        bar["start"] = bar["end"]


def _epics(
    session: Session, ctx: _Ctx, bp_id: int, lo: date, hi: date
) -> tuple[list[Task], dict[int, list[Task]]]:
    """Open epics, and done ones whose span meets the window; their children."""
    epics = list(
        session.scalars(
            select(Task)
            .where(Task.business_project_id == bp_id, Task.type == "epic")
            .order_by(Task.rank, Task.id)
        )
    )
    kids: dict[int, list[Task]] = {e.id: [] for e in epics}
    if epics:
        for c in session.scalars(
            select(Task)
            .where(Task.parent_id.in_(list(kids)), Task.type.in_(CHILD_TYPES))
            .order_by(Task.rank, Task.id)
        ):
            kids[int(c.parent_id or 0)].append(c)
    keep = []
    for e in epics:
        if not ctx.is_done(e):
            keep.append(e)
            continue
        spans = [ctx.span(x) for x in [e, *kids[e.id]]]
        if any(_overlaps(s, en, lo, hi) for s, en, _ in spans):
            keep.append(e)
    keep = keep[:MAX_EPICS]
    return keep, {e.id: kids[e.id] for e in keep}


def _names(session: Session, tasks: list[Task]) -> dict[int, str]:
    users = service.users_by_id(session, {t.assignee_id for t in tasks})
    return {uid: (u.full_name or u.primary_email or f"#{u.id}") for uid, u in users.items()}


def _versions(session: Session, bp_id: int, lo: date, hi: date) -> list[dict[str, Any]]:
    return [
        {
            "id": v.id,
            "name": v.name,
            "date": v.release_date.isoformat() if v.release_date else None,
            "released": v.released_at is not None,
        }
        for v in session.scalars(
            select(TaskVersion)
            .where(
                TaskVersion.business_project_id == bp_id,
                TaskVersion.release_date.is_not(None),
                TaskVersion.release_date >= lo,
                TaskVersion.release_date <= hi,
            )
            .order_by(TaskVersion.release_date, TaskVersion.id)
        )
    ]


def _fmt(d: str | None) -> str:
    return f"{d[8:10]}/{d[5:7]}" if d else "?"


def space_timeline(
    session: Session,
    p: Principal,
    bp_id: int,
    lo: date,
    hi: date,
    tz: ZoneInfo,
    today: date,
) -> dict[str, Any]:
    ctx = _Ctx(session, bp_id, tz, today)
    epics, kids = _epics(session, ctx, bp_id, lo, hi)
    in_epics = {c.id for cs in kids.values() for c in cs}
    # Story / Task / Bug outside every epic but planned (sprint or due date) in the window.
    loose = [
        t
        for t in session.scalars(
            select(Task)
            .where(
                Task.business_project_id == bp_id,
                Task.type.in_(CHILD_TYPES),
                Task.parent_id.is_(None),
                (Task.sprint_id.is_not(None)) | (Task.due_date.is_not(None)),
            )
            .order_by(Task.rank, Task.id)
        )
        if t.id not in in_epics and _overlaps(*ctx.span(t)[:2], lo, hi)
    ][:MAX_LOOSE]
    everyone = [*epics, *(c for cs in kids.values() for c in cs), *loose]
    names = _names(session, everyone)

    bars: dict[int, dict[str, Any]] = {}
    epic_rows = []
    for e in epics:
        progress, done_kids = _epic_progress(ctx, e, kids[e.id])
        row = _bar(ctx, e, names, p, progress)
        _epic_span(ctx, e, kids[e.id], row)
        children = [_bar(ctx, c, names, p) for c in kids[e.id]]
        for c in children:
            bars[int(c["id"])] = c
        row.update(children=children, children_count=len(children), done_count=done_kids)
        bars[e.id] = row
        epic_rows.append(row)
    loose_rows = [_bar(ctx, t, names, p) for t in loose]
    for r in loose_rows:
        bars[int(r["id"])] = r

    links, warnings = [], []
    if bars:
        for frm_id, to_id in session.execute(
            select(TaskLink.from_task_id, TaskLink.to_task_id).where(
                TaskLink.kind == "blocks",
                TaskLink.from_task_id.in_(list(bars)),
                TaskLink.to_task_id.in_(list(bars)),
            )
        ).all():
            blocker, blocked = bars[int(frm_id)], bars[int(to_id)]
            late = bool(
                not blocker["done"]
                and not blocked["done"]
                and blocker["end"] is not None
                and blocked["end"] is not None
                and blocker["end"] > blocked["end"]
            )
            links.append({"from": blocker["key"], "to": blocked["key"], "late": late})
            if not blocker["done"]:
                blocked["blockers"].append(blocker["key"])
            if late:
                blocked["blocked_late"] = True
                warnings.append(
                    {
                        "kind": "blocked_late",
                        "key": blocked["key"],
                        "blocker": blocker["key"],
                        "text": f"{blocked['key']} bị chặn quá hạn: {blocker['key']} xong "
                        f"{_fmt(blocker['end'])}, sau hạn {_fmt(blocked['end'])} của "
                        f"{blocked['key']}",
                    }
                )
    sprints = [
        {
            "id": s.id,
            "name": s.name,
            "state": s.state,
            "start": s.start_date.isoformat(),
            "end": s.end_date.isoformat(),
        }
        for s in sorted(ctx.sprints.values(), key=lambda s: (s.start_date or date.max, s.id))
        if s.start_date is not None
        and s.end_date is not None
        and _overlaps(s.start_date, s.end_date, lo, hi)
    ]
    return {
        "business_project_id": bp_id,
        "from": lo.isoformat(),
        "to": hi.isoformat(),
        "today": today.isoformat(),
        "epics": epic_rows,
        "loose": loose_rows,
        "sprints": sprints,
        "versions": _versions(session, bp_id, lo, hi),
        "links": links,
        "warnings": warnings,
        "can_sprint": service.can_sprint(p, bp_id),
    }


def many_timeline(
    session: Session,
    p: Principal,
    bp_ids: list[int],
    lo: date,
    hi: date,
    tz: ZoneInfo,
    today: date,
) -> dict[str, Any]:
    """Epics of several dự án (read-only): only those the person sees with Task on; none asked
    means all of them."""
    query = (
        select(BusinessProject, TaskSpace)
        .join(TaskSpace, TaskSpace.business_project_id == BusinessProject.id)
        .order_by(BusinessProject.name)
    )
    if bp_ids:
        query = query.where(BusinessProject.id.in_(bp_ids))
    visible = p.visible_projects()
    if visible is not None:
        query = query.where(BusinessProject.id.in_(visible))
    out: list[dict[str, Any]] = []
    for bp, space in session.execute(query).all():
        if not service.can_view_space(p, bp.id):
            continue
        if len(out) >= MAX_SPACES:
            break
        ctx = _Ctx(session, bp.id, tz, today)
        epics, kids = _epics(session, ctx, bp.id, lo, hi)
        names = _names(session, epics)
        rows = []
        for e in epics:
            progress, done_kids = _epic_progress(ctx, e, kids[e.id])
            row = _bar(ctx, e, names, None, progress)
            _epic_span(ctx, e, kids[e.id], row)
            row.update(children=[], children_count=len(kids[e.id]), done_count=done_kids)
            rows.append(row)
        out.append(
            {
                "business_project_id": bp.id,
                "name": bp.name,
                "key": space.key,
                "epics": rows,
                "versions": _versions(session, bp.id, lo, hi),
            }
        )
    return {
        "from": lo.isoformat(),
        "to": hi.isoformat(),
        "today": today.isoformat(),
        "spaces": out,
    }
