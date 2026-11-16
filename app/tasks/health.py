"""Sprint health and the multi-project overview (docs/specs/31 §19.5, roadmap P4): read-only
analytics for a Lead (one dự án's active sprint) and a Manager (every dự án they can see).

Nothing here writes; it reads tasks, sprints, git links and reviews, and reuses the report
helpers (burndown, sprint points) so the numbers match the Reports tab."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select, tuple_
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.models import (
    BusinessProject,
    MergeRequest,
    Sprint,
    Task,
    TaskGitLink,
    TaskHistory,
    TaskLink,
    TaskPriority,
    TaskSpace,
    TaskStatus,
    TaskVersion,
)
from app.tasks import reports, service, sprint_review

# Hours a task may sit in a "doing" status before it counts as stuck, when the status has no
# `age_warning_hours` of its own. "todo" statuses have no default: waiting there is normal.
DOING_WARNING_H = 72
# An open MR with no first review after this long is waiting on reviewers.
MR_REVIEW_H = 24
# A projection needs some pace to go on.
MIN_ELAPSED_DAYS = 2
# A range (§20.6) needs this many elapsed days (or 3 closed sprints); else one value, "ít dữ liệu".
RANGE_MIN_DAYS = 4
LEAD_TIME_DAYS = 30
# Health thresholds (§19.5): red below 70 % projected, or any highest bug / open critical
# finding; amber below 90 % projected; green otherwise.
RED_PCT = 70.0
AMBER_PCT = 90.0


def _hours(since: datetime | None, now: datetime) -> float | None:
    if since is None:
        return None
    return max((now - since).total_seconds(), 0) / 3600


def active_sprint(session: Session, bp_id: int) -> Sprint | None:
    return session.scalars(
        select(Sprint).where(Sprint.business_project_id == bp_id, Sprint.state == "active")
    ).first()


def _done_status_ids(statuses: list[TaskStatus]) -> set[int]:
    return {s.id for s in statuses if s.category == "done"}


def forecast(
    session: Session, sprint: Sprint, tasks: list[Task], done: set[int], tz: ZoneInfo
) -> dict[str, Any]:
    """The burndown plus a straight-line projection from the pace so far: points done per
    elapsed day, carried to the end date."""
    burn = reports.burndown(session, sprint, tz)
    scope = sum(reports.points(t) for t in tasks)
    done_points = sum(reports.points(t) for t in tasks if t.status_id in done)
    today = datetime.now(tz).date()
    start = sprint.start_date or today
    end = sprint.end_date or start + timedelta(days=13)
    span = max((end - start).days, 1)
    elapsed = (min(today, end) - start).days
    ideal = next((d["ideal_points"] for d in burn["days"] if d["day"] == today.isoformat()), None)
    projected_done: float | None = None
    projected_pct: float | None = None
    if elapsed >= MIN_ELAPSED_DAYS:
        projected_done = min(done_points / elapsed * span, scope) if scope else done_points
        projected_pct = round(projected_done / scope * 100, 1) if scope else 100.0
        projected_done = round(projected_done, 1)
    # docs/specs/31 §20.6: a band around the projection, from the spread of the daily pace (each
    # elapsed day's done points; today's and earlier ones fall in the nearest day) and of the
    # closed sprints' velocity.
    daily = [0.0] * max(elapsed, 0)
    if daily:
        for t in tasks:
            if t.status_id not in done:
                continue
            day = t.resolved_at.astimezone(tz).date() if t.resolved_at else start
            daily[min(max((day - start).days, 0), len(daily) - 1)] += reports.points(t)
    band = sprint_review.forecast_range(
        done_points=done_points,
        scope=scope,
        daily=daily,
        remaining_days=span - max(elapsed, 0),
        velocities=sprint_review.closed_velocities(session, sprint.business_project_id),
        min_days=RANGE_MIN_DAYS,
    )
    return {
        "days": burn["days"],
        "committed_points": burn["committed_points"],
        "scope_points": round(scope, 1),
        "done_points": round(done_points, 1),
        "done_pct": round(done_points / scope * 100, 1) if scope else None,
        "ideal_remaining": ideal,
        "elapsed_days": max(elapsed, 0),
        "total_days": span,
        "projected_done_points": projected_done,
        "projected_pct": projected_pct,
        "range": band,
        "few_data": band is None,
    }


def _brief(task: Task) -> dict[str, Any]:
    return {"key": task.key, "title": task.title, "assignee_id": task.assignee_id}


def stuck(
    session: Session, tasks: list[Task], statuses: list[TaskStatus], now: datetime
) -> dict[str, Any]:
    by_id = {s.id: s for s in statuses}
    open_tasks = [
        t for t in tasks if by_id.get(t.status_id) and by_id[t.status_id].category != "done"
    ]
    users = service.users_by_id(session, {t.assignee_id for t in open_tasks})

    aged = []
    for t in open_tasks:
        status = by_id[t.status_id]
        limit = getattr(status, "age_warning_hours", None)
        if limit is None and status.category == "doing":
            limit = DOING_WARNING_H
        hours = _hours(t.status_changed_at, now)
        if limit and hours is not None and hours > limit:
            aged.append(
                {
                    **_brief(t),
                    "assignee": service.person(users.get(t.assignee_id or -1)),
                    "status": status.name,
                    "hours": round(hours, 1),
                    "limit_hours": int(limit),
                }
            )
    aged.sort(key=lambda r: -float(r["hours"]))

    ids = {t.id: t for t in tasks}
    links = (
        list(
            session.scalars(
                select(TaskGitLink).where(
                    TaskGitLink.task_id.in_(list(ids)),
                    TaskGitLink.kind == "mr",
                    TaskGitLink.state == "opened",
                )
            )
        )
        if ids
        else []
    )
    keys = {(gl.project_id, gl.mr_iid) for gl in links if gl.project_id and gl.mr_iid}
    mrs: dict[tuple[int | None, int | None], MergeRequest] = (
        {
            (mr.project_id, mr.iid): mr
            for mr in session.scalars(
                select(MergeRequest).where(
                    tuple_(MergeRequest.project_id, MergeRequest.iid).in_(list(keys))
                )
            )
        }
        if keys
        else {}
    )
    waiting: list[dict[str, Any]] = []
    seen: set[tuple[int | None, int | None]] = set()
    for gl in links:
        key = (gl.project_id, gl.mr_iid)
        mr = mrs.get(key)
        if mr is None or key in seen or mr.first_review_at is not None:
            continue
        if mr.state not in (None, "opened"):
            continue
        hours = _hours(mr.created_at_src or mr.created_at or gl.created_at, now)
        if hours is None or hours <= MR_REVIEW_H:
            continue
        seen.add(key)
        waiting.append(
            {
                "task": _brief(ids[gl.task_id]),
                "project_id": gl.project_id,
                "iid": gl.mr_iid,
                "title": mr.title or gl.title,
                "hours": round(hours, 1),
            }
        )
    waiting.sort(key=lambda r: -float(r["hours"]))

    open_ids = {t.id for t in open_tasks}
    blocked = []
    if open_ids:
        rows = session.execute(
            select(TaskLink.to_task_id, Task)
            .join(Task, Task.id == TaskLink.from_task_id)
            .join(TaskStatus, TaskStatus.id == Task.status_id)
            .where(
                TaskLink.kind == "blocks",
                TaskLink.to_task_id.in_(open_ids),
                TaskStatus.category != "done",
            )
        ).all()
        by_blocked: dict[int, list[str]] = defaultdict(list)
        for to_id, blocker in rows:
            by_blocked[int(to_id)].append(blocker.key)
        blocked = [
            {**_brief(ids[tid]), "blocked_by": sorted(keys_)}
            for tid, keys_ in sorted(by_blocked.items(), key=lambda kv: ids[kv[0]].number)
        ]
    return {
        "aged": aged,
        "mrs_waiting": waiting,
        "blocked": blocked,
        "count": len(aged) + len(waiting) + len(blocked),
        "doing_warning_hours": DOING_WARNING_H,
        "mr_review_hours": MR_REVIEW_H,
    }


def load(session: Session, tasks: list[Task], done: set[int]) -> list[dict[str, Any]]:
    """Open tasks and points per person in the sprint, by name (not a ranking)."""
    per: dict[int | None, dict[str, float]] = defaultdict(lambda: {"open": 0, "points": 0.0})
    for t in tasks:
        if t.status_id in done or t.type == "epic":
            continue
        per[t.assignee_id]["open"] += 1
        per[t.assignee_id]["points"] += reports.points(t)
    users = service.users_by_id(session, per.keys())
    out = []
    for uid, v in per.items():
        user = users.get(uid) if uid is not None else None
        out.append(
            {
                "user": service.person(user),
                "name": (user.full_name or user.primary_email or f"#{user.id}")
                if user
                else "Chưa giao",
                "open": int(v["open"]),
                "open_points": round(v["points"], 1),
            }
        )
    out.sort(key=lambda r: (r["user"] is None, str(r["name"]).casefold()))
    return out


def quality(
    session: Session,
    bp_id: int,
    sprint: Sprint,
    tasks: list[Task],
    statuses: list[TaskStatus],
    tz: ZoneInfo,
) -> dict[str, Any]:
    summaries = service.git_summaries(session, [t.id for t in tasks])
    critical = sum(int(s["open_critical"]) for s in summaries.values())
    today = datetime.now(tz).date()
    start = sprint.start_date or today
    end = sprint.end_date or start + timedelta(days=13)
    lo = datetime.combine(start, datetime.min.time(), tz)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tz)
    bugs = int(
        session.scalar(
            select(func.count(Task.id)).where(
                Task.business_project_id == bp_id,
                Task.type == "bug",
                Task.created_at >= lo,
                Task.created_at < hi,
            )
        )
        or 0
    )
    done_names = {s.name for s in statuses if s.category == "done"}
    open_names = {s.name for s in statuses if s.category != "done"}
    reopened = 0
    if tasks and done_names:
        reopened = len(
            set(
                session.scalars(
                    select(TaskHistory.task_id).where(
                        TaskHistory.task_id.in_([t.id for t in tasks]),
                        TaskHistory.field == "status",
                        TaskHistory.old.in_(done_names),
                        TaskHistory.new.in_(open_names)
                        if open_names
                        else TaskHistory.new.is_(None),
                        TaskHistory.at >= lo,
                        TaskHistory.at < hi,
                    )
                )
            )
        )
    return {"open_critical": critical, "bugs_created": bugs, "reopened": reopened}


def health_color(projected_pct: float | None, highest_bugs: int, critical: int) -> str:
    if (projected_pct is not None and projected_pct < RED_PCT) or highest_bugs or critical:
        return "red"
    if projected_pct is not None and projected_pct < AMBER_PCT:
        return "amber"
    return "green"


def sprint_out(sprint: Sprint) -> dict[str, Any]:
    return {
        "id": sprint.id,
        "name": sprint.name,
        "goal": sprint.goal,
        "start_date": sprint.start_date.isoformat() if sprint.start_date else None,
        "end_date": sprint.end_date.isoformat() if sprint.end_date else None,
    }


def sprint_health(session: Session, bp_id: int, tz: ZoneInfo) -> dict[str, Any]:
    """The active sprint's forecast, what is stuck, load per person and quality signals."""
    sprint = active_sprint(session, bp_id)
    if sprint is None:
        return {"sprint": None}
    statuses = service.statuses_of(session, bp_id)
    done = _done_status_ids(statuses)
    tasks = reports.sprint_tasks(session, sprint.id)
    now = datetime.now(UTC)
    fc = forecast(session, sprint, tasks, done, tz)
    st = stuck(session, tasks, statuses, now)
    qa = quality(session, bp_id, sprint, tasks, statuses, tz)
    qa["highest_bugs"] = _highest_bugs(
        session, bp_id, [t for t in tasks if t.status_id not in done]
    )
    return {
        "sprint": sprint_out(sprint),
        "forecast": fc,
        "stuck": st,
        "load": load(session, tasks, done),
        "quality": qa,
        "scope_change": sprint_review.scope_change(session, sprint, tz),
        "health": health_color(
            fc["projected_pct"], int(qa["highest_bugs"]), int(qa["open_critical"])
        ),
    }


def _highest_bugs(session: Session, bp_id: int, open_tasks: list[Task]) -> int:
    """Open bugs whose priority is at the "highest" level."""
    meta = service.priority_meta(session, [bp_id])
    return sum(
        1
        for t in open_tasks
        if t.type == "bug"
        and (prio := meta.get((bp_id, t.priority))) is not None
        and prio.level == "highest"
    )


def _lead_time_h(session: Session, bp_id: int, now: datetime) -> float | None:
    since = now - timedelta(days=LEAD_TIME_DAYS)
    hours = [
        max((resolved - created).total_seconds(), 0) / 3600
        for created, resolved in session.execute(
            select(Task.created_at, Task.resolved_at).where(
                Task.business_project_id == bp_id,
                Task.resolution == "done",
                Task.resolved_at >= since,
                Task.type != "epic",
            )
        ).all()
        if created is not None and resolved is not None
    ]
    return round(median(hours), 1) if hours else None


def _next_version(session: Session, bp_id: int, done: set[int]) -> dict[str, Any] | None:
    version = session.scalars(
        select(TaskVersion)
        .where(TaskVersion.business_project_id == bp_id, TaskVersion.released_at.is_(None))
        .order_by(TaskVersion.release_date.asc().nulls_last(), TaskVersion.id)
    ).first()
    if version is None:
        return None
    held = list(
        session.scalars(
            select(Task.status_id).where(Task.version_id == version.id, Task.type != "epic")
        )
    )
    finished = sum(1 for s in held if s in done)
    return {
        "id": version.id,
        "name": version.name,
        "release_date": version.release_date.isoformat() if version.release_date else None,
        "total": len(held),
        "done": finished,
        "done_pct": round(finished / len(held) * 100, 1) if held else None,
    }


def overview_row(
    session: Session, bp: BusinessProject, space: TaskSpace, tz: ZoneInfo, now: datetime
) -> dict[str, Any]:
    statuses = service.statuses_of(session, bp.id)
    done = _done_status_ids(statuses)
    sprint = active_sprint(session, bp.id)
    fc: dict[str, Any] | None = None
    if sprint is not None:
        fc = forecast(session, sprint, reports.sprint_tasks(session, sprint.id), done, tz)
    # Counted in SQL (review: no loading every open task of every dự án): the open bugs at the
    # "highest" level, and critical findings only over open tasks that have code linked.
    still_open = Task.status_id.not_in(done) if done else Task.id.is_not(None)
    highest = int(
        session.scalar(
            select(func.count())
            .select_from(Task)
            .join(
                TaskPriority,
                (TaskPriority.business_project_id == Task.business_project_id)
                & (TaskPriority.key == Task.priority),
            )
            .where(
                Task.business_project_id == bp.id,
                still_open,
                Task.type == "bug",
                TaskPriority.level == "highest",
            )
        )
        or 0
    )
    coded = list(
        session.scalars(
            select(Task.id)
            .where(Task.business_project_id == bp.id, still_open)
            .where(
                exists().where(
                    TaskGitLink.task_id == Task.id, TaskGitLink.kind.in_(("commit", "mr"))
                )
            )
        )
    )
    summaries = service.git_summaries(session, coded)
    critical = sum(int(s["open_critical"]) for s in summaries.values())
    projected = fc["projected_pct"] if fc else None
    return {
        "business_project_id": bp.id,
        "name": bp.name,
        "key": space.key,
        "sprint": sprint_out(sprint) if sprint else None,
        "done_pct": fc["done_pct"] if fc else None,
        "projected_pct": projected,
        "highest_bugs": highest,
        "open_critical": critical,
        "lead_time_h": _lead_time_h(session, bp.id, now),
        "next_version": _next_version(session, bp.id, done),
        "health": health_color(projected, highest, critical),
    }


def overview(session: Session, p: Principal, tz: ZoneInfo) -> list[dict[str, Any]]:
    """One row per dự án the person can see with Task on, by name."""
    visible = p.visible_projects()
    query = (
        select(BusinessProject, TaskSpace)
        .join(TaskSpace, TaskSpace.business_project_id == BusinessProject.id)
        .order_by(BusinessProject.name)
    )
    if visible is not None:
        query = query.where(BusinessProject.id.in_(visible))
    now = datetime.now(UTC)
    rows = [
        overview_row(session, bp, space, tz, now)
        for bp, space in session.execute(query).all()
        if service.can_view_space(p, bp.id)
    ]
    # docs/specs/31 §20.6: 4-sprint trends, grouped SQL over every row at once.
    trend = sprint_review.trends(session, [int(r["business_project_id"]) for r in rows], tz)
    for r in rows:
        r["trends"] = trend.get(int(r["business_project_id"]))
    return rows
