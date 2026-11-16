"""Sprint review (docs/specs/31 §20.6, round 2 stage 2.6): what a sprint delivered, carried
over, took on after it started, and what quality signals it left, plus the 4-sprint trends of
the multi-project overview. Read-only; the retro page is written through the documents API.

The moment a sprint started is its `task.sprint.start` audit row (the date fields are only
days and may be back-dated); without one, the start date's midnight in the report zone."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from statistics import median, pstdev
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    and_,
    cast,
    column,
    func,
    or_,
    select,
    union,
    values,
)
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.docs import service as docs
from app.models import (
    AuditLog,
    MergeRequest,
    Review,
    ReviewFinding,
    Sprint,
    Task,
    TaskGitLink,
    TaskHistory,
    TaskPriority,
    TaskSpace,
    TaskStatus,
)
from app.tasks import reports, service

# Kinds that are a sprint's work items (an epic spans sprints; a sub-task is its parent's).
ITEM_EXCLUDED = ("epic", "subtask")
SEVERE = ("critical", "high")
TREND_SPRINTS = 4
KANBAN_DAYS = 14
# Velocity spread for the forecast range needs this many closed sprints (and uses at most 6).
VELOCITY_MIN = 3
VELOCITY_MAX = 6
LIST_MAX = 50


def _item(task: Task) -> bool:
    return task.type not in ITEM_EXCLUDED


def _midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, datetime.min.time(), tz)


def started_moment(session: Session, sprint: Sprint, tz: ZoneInfo) -> datetime | None:
    if sprint.state == "planned":
        return None
    at = session.scalar(
        select(func.max(AuditLog.at)).where(
            AuditLog.target == f"sprint:{sprint.id}", AuditLog.action == "task.sprint.start"
        )
    )
    if at is not None:
        return at if at.tzinfo else at.replace(tzinfo=UTC)
    return _midnight(sprint.start_date, tz) if sprint.start_date else None


def _window(session: Session, sprint: Sprint, tz: ZoneInfo) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    lo = started_moment(session, sprint, tz)
    if lo is None:
        lo = _midnight(sprint.start_date, tz) if sprint.start_date else now
    hi = sprint.closed_at or now
    return lo, max(hi, lo)


def _brief(task: Task) -> dict[str, Any]:
    return {
        "key": task.key,
        "title": task.title,
        "type": task.type,
        "points": reports.points(task),
    }


def _group(tasks: list[Task]) -> dict[str, Any]:
    ordered = sorted(tasks, key=lambda t: t.number)
    return {
        "count": len(ordered),
        "points": round(sum(reports.points(t) for t in ordered), 1),
        "keys": [t.key for t in ordered],
        "tasks": [_brief(t) for t in ordered[:LIST_MAX]],
    }


# -- scope change ------------------------------------------------------------------------------


def scope_change(session: Session, sprint: Sprint, tz: ZoneInfo) -> dict[str, Any]:
    """Tasks that entered or left the sprint after it started (scope creep).

    Entered: a "sprint" history row naming this sprint as the new value after the start, or a
    task created after the start straight into it (quick create on the board does that). Left:
    a history row naming it as the old value after the start and before it closed. History keeps
    the sprint's name (service._named), so the match is by name within the dự án."""
    empty = {"since": None, "added_after_start": _group([]), "removed_after_start": _group([])}
    start = started_moment(session, sprint, tz)
    if start is None:
        return empty
    until = sprint.closed_at
    bp = sprint.business_project_id
    in_window = [TaskHistory.at > start]
    if until is not None:
        in_window.append(TaskHistory.at <= until)
    added: set[int] = set()
    removed: set[int] = set()
    for task_id, old, new in session.execute(
        select(TaskHistory.task_id, TaskHistory.old, TaskHistory.new)
        .join(Task, Task.id == TaskHistory.task_id)
        .where(
            Task.business_project_id == bp,
            TaskHistory.field == "sprint",
            or_(TaskHistory.old == sprint.name, TaskHistory.new == sprint.name),
            *in_window,
        )
    ).all():
        if new == sprint.name:
            added.add(int(task_id))
        if old == sprint.name:
            removed.add(int(task_id))

    # Created after the start with this sprint as their first one.
    created_q = select(Task.id, Task.sprint_id).where(
        Task.business_project_id == bp,
        Task.created_at > start,
        Task.type.not_in(ITEM_EXCLUDED),
    )
    if until is not None:
        created_q = created_q.where(Task.created_at <= until)
    born = {int(i): sid for i, sid in session.execute(created_q).all()}
    if born:
        first_old: dict[int, str | None] = {}
        for task_id, old in session.execute(
            select(TaskHistory.task_id, TaskHistory.old)
            .where(TaskHistory.task_id.in_(list(born)), TaskHistory.field == "sprint")
            .order_by(TaskHistory.task_id, TaskHistory.at, TaskHistory.id)
        ).all():
            first_old.setdefault(int(task_id), old)
        for task_id, sid in born.items():
            if (task_id in first_old and first_old[task_id] == sprint.name) or (
                task_id not in first_old and sid == sprint.id
            ):
                added.add(task_id)

    rows = (
        {
            t.id: t
            for t in session.scalars(select(Task).where(Task.id.in_(list(added | removed))))
            if _item(t)
        }
        if added or removed
        else {}
    )
    return {
        "since": start.isoformat(),
        "added_after_start": _group([rows[i] for i in added if i in rows]),
        "removed_after_start": _group([rows[i] for i in removed if i in rows]),
    }


# -- code reviews of a set of tasks -----------------------------------------------------------


def _code_reviews(task_filter: Any) -> Any:
    """(business_project_id, review id) of every finished review of commits / MRs linked to the
    tasks `task_filter` selects: one union, deduplicated."""
    by_commit = (
        select(Task.business_project_id.label("bp"), Review.id.label("rid"))
        .join(TaskGitLink, TaskGitLink.task_id == Task.id)
        .join(Review, Review.commit_sha == TaskGitLink.sha)
        .where(TaskGitLink.kind == "commit", Review.status == "done", task_filter)
    )
    by_mr = (
        select(Task.business_project_id.label("bp"), Review.id.label("rid"))
        .join(TaskGitLink, TaskGitLink.task_id == Task.id)
        .join(
            MergeRequest,
            and_(
                MergeRequest.project_id == TaskGitLink.project_id,
                MergeRequest.iid == TaskGitLink.mr_iid,
            ),
        )
        .join(Review, Review.mr_id == MergeRequest.id)
        .where(TaskGitLink.kind == "mr", Review.status == "done", task_filter)
    )
    return union(by_commit, by_mr).subquery()


def _finding_key() -> Any:
    # A re-review repeats a finding with the same fingerprint: count it once.
    return func.coalesce(ReviewFinding.fingerprint, cast(ReviewFinding.id, String))


# -- the review --------------------------------------------------------------------------------


def _cycle_h(tasks: list[Task]) -> float | None:
    hours = [
        max((t.resolved_at - t.started_at).total_seconds(), 0) / 3600
        for t in tasks
        if t.started_at is not None and t.resolved_at is not None
    ]
    return round(median(hours), 1) if hours else None


def _carried_on_close(session: Session, sprint: Sprint) -> int | None:
    """How many tasks the close moved on: completing a sprint moves them without history, so a
    closed sprint only knows the count its audit row kept."""
    details = session.scalar(
        select(AuditLog.details)
        .where(AuditLog.target == f"sprint:{sprint.id}", AuditLog.action == "task.sprint.complete")
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    if not details:
        return None
    moved = details.get("moved")
    return int(moved) if isinstance(moved, int | float) else None


def review(
    session: Session, sprint: Sprint, tz: ZoneInfo, principal: Principal | None = None
) -> dict[str, Any]:
    bp = sprint.business_project_id
    lo, hi = _window(session, sprint, tz)
    statuses = service.statuses_of(session, bp)
    done_ids = {s.id for s in statuses if s.category == "done"}
    tasks = reports.sprint_tasks(session, sprint.id)
    items = [t for t in tasks if _item(t)]
    done = [t for t in items if t.status_id in done_ids and (t.resolution or "done") == "done"]
    closed_other = [
        t for t in items if t.status_id in done_ids and (t.resolution or "done") != "done"
    ]
    if sprint.state == "closed":
        carried: dict[str, Any] = {**_group([]), "count": _carried_on_close(session, sprint)}
        carried["points"] = None
    else:
        carried = _group([t for t in items if t.status_id not in done_ids])

    bugs = list(
        session.scalars(
            select(Task)
            .where(
                Task.business_project_id == bp,
                Task.type == "bug",
                Task.created_at >= lo,
                Task.created_at < hi,
            )
            .order_by(Task.number)
        )
    )
    meta = service.priority_meta(session, [bp])
    highest = [
        b
        for b in bugs
        if (prio := meta.get((bp, b.priority))) is not None and prio.level == "highest"
    ]

    findings = {s: 0 for s in SEVERE}
    if tasks:
        code = _code_reviews(Task.id.in_([t.id for t in tasks]))
        # Opened in the window: a finding's first sighting (by fingerprint) falls in it, so a
        # re-review repeating an older finding does not count it again.
        first = (
            select(
                ReviewFinding.severity.label("sev"),
                func.min(ReviewFinding.created_at).label("first_at"),
            )
            .join(code, code.c.rid == ReviewFinding.review_id)
            .where(ReviewFinding.severity.in_(SEVERE))
            .group_by(ReviewFinding.severity, _finding_key())
            .subquery()
        )
        for severity, n in session.execute(
            select(first.c.sev, func.count())
            .where(first.c.first_at >= lo, first.c.first_at < hi)
            .group_by(first.c.sev)
        ).all():
            findings[str(severity)] = int(n)

    scope = scope_change(session, sprint, tz)
    committed = float(sprint.committed_points) if sprint.committed_points is not None else None
    done_points = round(sum(reports.points(t) for t in done), 1)
    can_retro = bool(
        principal is not None and docs.may_view(principal, bp) and docs.may_edit(principal, bp)
    )
    out: dict[str, Any] = {
        "sprint": {
            "id": sprint.id,
            "name": sprint.name,
            "goal": sprint.goal,
            "state": sprint.state,
            "start_date": sprint.start_date.isoformat() if sprint.start_date else None,
            "end_date": sprint.end_date.isoformat() if sprint.end_date else None,
            "closed_at": sprint.closed_at.isoformat() if sprint.closed_at else None,
            "business_project_id": bp,
        },
        "window": {"from": lo.isoformat(), "to": hi.isoformat()},
        "committed_points": committed,
        "done": _group(done),
        "done_pct": round(done_points / committed * 100, 1) if committed else None,
        "closed_without_done": _group(closed_other),
        "carried_over": carried,
        "added_after_start": scope["added_after_start"],
        "removed_after_start": scope["removed_after_start"],
        "bugs_created": {
            "count": len(bugs),
            "highest": len(highest),
            "keys": [b.key for b in bugs[:LIST_MAX]],
        },
        "findings": findings,
        "cycle_time_h": _cycle_h(done),
        "can_create_retro": can_retro,
    }
    out["markdown"] = markdown(out)
    return out


def _hours_text(h: float | None) -> str:
    if h is None:
        return "—"
    return f"{round(h / 24, 1)} ngày" if h >= 48 else f"{h} giờ"


def _task_lines(group: dict[str, Any]) -> list[str]:
    lines = [f"- **{t['key']}** {t['title']} ({t['points']:g} SP)" for t in group["tasks"]]
    more = int(group["count"] or 0) - len(group["tasks"])
    if more > 0:
        lines.append(f"- … và {more} task khác")
    return lines or ["- (không có)"]


def markdown(r: dict[str, Any]) -> str:
    """The review as a retro page: summary, lists, then the three sections the team fills."""
    s = r["sprint"]
    days = f"{s['start_date'] or '?'} – {s['end_date'] or '?'}"  # noqa: RUF001
    carried = r["carried_over"]
    carried_text = (f"{carried['count']} task" if carried["count"] is not None else "không rõ") + (
        f" · {carried['points']:g} SP" if carried.get("points") is not None else ""
    )
    committed = r["committed_points"]
    done = r["done"]
    lines = [
        f"# Retro {s['name']}",
        "",
        f"_{days}_",
        "",
    ]
    if s["goal"]:
        lines += [f"**Mục tiêu:** {s['goal']}", ""]
    lines += [
        "## Tóm tắt",
        "",
        "| Chỉ số | Giá trị |",
        "|---|---|",
        f"| Đã xong | {done['count']} task · {done['points']:g} SP"
        + (f" / cam kết {committed:g} SP" if committed is not None else "")
        + " |",
        f"| Chuyển sang sprint sau | {carried_text} |",
        f"| Thêm sau khi bắt đầu | {r['added_after_start']['count']} task"
        f" · {r['added_after_start']['points']:g} SP |",
        f"| Bỏ ra sau khi bắt đầu | {r['removed_after_start']['count']} task"
        f" · {r['removed_after_start']['points']:g} SP |",
        f"| Bug tạo trong sprint | {r['bugs_created']['count']}"
        f" (mức cao nhất: {r['bugs_created']['highest']}) |",
        f"| Finding mới trên code của sprint | critical {r['findings']['critical']}"
        f" · high {r['findings']['high']} |",
        f"| Cycle time trung vị | {_hours_text(r['cycle_time_h'])} |",
        "",
        "## Đã xong",
        "",
        *_task_lines(done),
        "",
    ]
    if carried["tasks"]:
        lines += ["## Chuyển sang sprint sau", "", *_task_lines(carried), ""]
    if r["added_after_start"]["count"]:
        lines += ["## Thêm sau khi bắt đầu", "", *_task_lines(r["added_after_start"]), ""]
    lines += [
        "## Điều tốt",
        "",
        "- ",
        "",
        "## Cần cải thiện",
        "",
        "- ",
        "",
        "## Hành động",
        "",
        "- [ ] ",
    ]
    return "\n".join(lines)


# -- no sprint running: the last one, or the Kanban flow --------------------------------------


def last_closed(session: Session, bp_id: int) -> Sprint | None:
    return session.scalars(
        select(Sprint)
        .where(Sprint.business_project_id == bp_id, Sprint.state == "closed")
        .order_by(Sprint.closed_at.desc().nulls_last(), Sprint.id.desc())
        .limit(1)
    ).first()


def kanban_flow(session: Session, bp_id: int, now: datetime) -> dict[str, Any]:
    since = now - timedelta(days=KANBAN_DAYS)
    finished = [
        t
        for t in session.scalars(
            select(Task).where(
                Task.business_project_id == bp_id,
                Task.resolution == "done",
                Task.resolved_at >= since,
                Task.type.not_in(ITEM_EXCLUDED),
            )
        )
    ]
    created = int(
        session.scalar(
            select(func.count(Task.id)).where(
                Task.business_project_id == bp_id,
                Task.created_at >= since,
                Task.type.not_in(ITEM_EXCLUDED),
            )
        )
        or 0
    )
    return {
        "days": KANBAN_DAYS,
        "throughput": len(finished),
        "throughput_points": round(sum(reports.points(t) for t in finished), 1),
        "per_week": round(len(finished) / KANBAN_DAYS * 7, 1),
        "created": created,
        "cycle_time_h": _cycle_h(finished),
    }


def latest(session: Session, bp_id: int, tz: ZoneInfo, principal: Principal) -> dict[str, Any]:
    space = session.get(TaskSpace, bp_id)
    mode = space.board_mode if space is not None else "scrum"
    if mode == "kanban":
        return {
            "mode": mode,
            "review": None,
            "flow": kanban_flow(session, bp_id, datetime.now(UTC)),
        }
    sprint = last_closed(session, bp_id)
    return {
        "mode": mode,
        "review": review(session, sprint, tz, principal) if sprint is not None else None,
        "flow": None,
    }


# -- forecast range and 4-sprint trends --------------------------------------------------------


def closed_velocities(session: Session, bp_id: int, limit: int = VELOCITY_MAX) -> list[float]:
    """Points delivered ("done" resolution) by each of the last closed sprints, newest first,
    in one grouped query."""
    order = [
        int(i)
        for i in session.scalars(
            select(Sprint.id)
            .where(Sprint.business_project_id == bp_id, Sprint.state == "closed")
            .order_by(Sprint.closed_at.desc().nulls_last(), Sprint.id.desc())
            .limit(limit)
        )
    ]
    if not order:
        return []
    by_id = {
        int(sid or 0): float(total or 0)
        for sid, total in session.execute(
            select(Task.sprint_id, func.sum(Task.story_points))
            .join(TaskStatus, TaskStatus.id == Task.status_id)
            .where(
                Task.sprint_id.in_(order),
                Task.type.in_(tuple(service.POINTED_TYPES)),
                func.coalesce(Task.resolution, "done") == "done",
                TaskStatus.category == "done",
            )
            .group_by(Task.sprint_id)
        ).all()
    }
    return [by_id.get(i, 0.0) for i in order]


def forecast_range(
    *,
    done_points: float,
    scope: float,
    daily: list[float],
    remaining_days: int,
    velocities: list[float],
    min_days: int,
) -> dict[str, Any] | None:
    """A low-high band for the points done at the end: the daily pace ± one standard deviation
    over the days so far (from `min_days` days), and the closed sprints' velocity ± one standard
    deviation (from VELOCITY_MIN sprints). With both, the mean of the two bounds."""
    if scope <= 0:
        return None
    bands: list[tuple[float, float]] = []
    basis: list[str] = []
    if len(daily) >= min_days:
        mean = sum(daily) / len(daily)
        spread = pstdev(daily)
        rest = max(remaining_days, 0)
        bands.append(
            (done_points + rest * max(mean - spread, 0.0), done_points + rest * (mean + spread))
        )
        basis.append("daily")
    if len(velocities) >= VELOCITY_MIN:
        mean = sum(velocities) / len(velocities)
        spread = pstdev(velocities)
        bands.append((mean - spread, mean + spread))
        basis.append("velocity")
    if not bands:
        return None
    low = sum(b[0] for b in bands) / len(bands)
    high = sum(b[1] for b in bands) / len(bands)
    low = min(max(low, done_points), scope)
    high = min(max(high, low), scope)
    return {
        "low_points": round(low, 1),
        "high_points": round(high, 1),
        "low_pct": round(low / scope * 100, 1),
        "high_pct": round(high / scope * 100, 1),
        "basis": basis,
    }


def _direction(values: list[float | None]) -> str | None:
    """Latest value against the mean of the ones before it: within 10 % (or half a unit) is
    flat. Needs two values."""
    seen = [v for v in values if v is not None]
    if len(seen) < 2:
        return None
    before = sum(seen[:-1]) / len(seen[:-1])
    delta = seen[-1] - before
    if abs(delta) <= max(0.1 * abs(before), 0.5):
        return "flat"
    return "up" if delta > 0 else "down"


def trends(session: Session, bp_ids: list[int], tz: ZoneInfo) -> dict[int, dict[str, Any]]:
    """Per dự án, its last 4 closed sprints (oldest first): median lead time of the tasks they
    delivered, bugs at the highest priority level created and critical findings opened on the
    dự án's code during each. Three grouped queries for every dự án at once."""
    if not bp_ids:
        return {}
    ranked = (
        select(
            Sprint.id,
            Sprint.business_project_id,
            Sprint.name,
            Sprint.start_date,
            Sprint.closed_at,
            func.row_number()
            .over(
                partition_by=Sprint.business_project_id,
                order_by=(Sprint.closed_at.desc().nulls_last(), Sprint.id.desc()),
            )
            .label("rn"),
        )
        .where(Sprint.business_project_id.in_(bp_ids), Sprint.state == "closed")
        .subquery()
    )
    sprints = session.execute(
        select(ranked).where(ranked.c.rn <= TREND_SPRINTS).order_by(ranked.c.rn.desc())
    ).all()
    if not sprints:
        return {}
    windows = []
    for row in sprints:
        hi = row.closed_at or datetime.now(UTC)
        lo = _midnight(row.start_date, tz) if row.start_date else hi - timedelta(days=14)
        windows.append((int(row.id), int(row.business_project_id), lo, max(hi, lo)))
    w = (
        values(
            column("sid", Integer),
            column("bp", Integer),
            column("lo", DateTime(timezone=True)),
            column("hi", DateTime(timezone=True)),
            name="w",
        )
        .data(windows)
        .alias("w")
    )
    sids = [x[0] for x in windows]

    lead: dict[int, float] = {}
    hours = func.extract("epoch", Task.resolved_at - Task.created_at) / 3600
    for sid, med in session.execute(
        select(Task.sprint_id, func.percentile_cont(0.5).within_group(hours))
        .where(
            Task.sprint_id.in_(sids),
            Task.resolution == "done",
            Task.resolved_at.is_not(None),
            Task.type.not_in(ITEM_EXCLUDED),
        )
        .group_by(Task.sprint_id)
    ).all():
        if med is not None:
            lead[int(sid)] = round(float(med), 1)

    bugs: dict[int, int] = defaultdict(int)
    for sid, n in session.execute(
        select(w.c.sid, func.count(Task.id))
        .select_from(w)
        .join(
            Task,
            and_(
                Task.business_project_id == w.c.bp,
                Task.created_at >= w.c.lo,
                Task.created_at < w.c.hi,
                Task.type == "bug",
            ),
        )
        .join(
            TaskPriority,
            and_(
                TaskPriority.business_project_id == Task.business_project_id,
                TaskPriority.key == Task.priority,
            ),
        )
        .where(TaskPriority.level == "highest")
        .group_by(w.c.sid)
    ).all():
        bugs[int(sid)] = int(n)

    critical: dict[int, int] = defaultdict(int)
    code = _code_reviews(Task.business_project_id.in_(bp_ids))
    # Each critical finding counts in the window of its first sighting (by fingerprint).
    first = (
        select(code.c.bp.label("bp"), func.min(ReviewFinding.created_at).label("first_at"))
        .select_from(code)
        .join(ReviewFinding, ReviewFinding.review_id == code.c.rid)
        .where(ReviewFinding.severity == "critical")
        .group_by(code.c.bp, _finding_key())
        .subquery()
    )
    for sid, n in session.execute(
        select(w.c.sid, func.count())
        .select_from(w)
        .join(
            first,
            and_(first.c.bp == w.c.bp, first.c.first_at >= w.c.lo, first.c.first_at < w.c.hi),
        )
        .group_by(w.c.sid)
    ).all():
        critical[int(sid)] = int(n)

    per: dict[int, list[Any]] = defaultdict(list)
    for row in sprints:
        per[int(row.business_project_id)].append(row)
    out: dict[int, dict[str, Any]] = {}
    for bp, rows in per.items():
        lt: list[float | None] = [lead.get(int(r.id)) for r in rows]
        hb: list[float | None] = [float(bugs[int(r.id)]) for r in rows]
        cf: list[float | None] = [float(critical[int(r.id)]) for r in rows]
        out[bp] = {
            "sprints": [r.name for r in rows],
            "lead_time_h": {"values": lt, "direction": _direction(lt)},
            "highest_bugs": {"values": [int(v or 0) for v in hb], "direction": _direction(hb)},
            "critical_findings": {
                "values": [int(v or 0) for v in cf],
                "direction": _direction(cf),
            },
        }
    return out
