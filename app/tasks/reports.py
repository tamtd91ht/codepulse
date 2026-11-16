"""Task reports (docs/specs/31 §8): sprint burndown, velocity, created vs resolved, cycle time,
by status / person, logged time, and a version's release notes."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    MergeRequest,
    Project,
    Sprint,
    Task,
    TaskGitLink,
    TaskHistory,
    TaskStatus,
    TaskVersion,
    TaskWorklog,
    User,
)
from app.tasks import service

TYPE_TITLES = {
    "epic": "Epic",
    "story": "Tính năng",
    "task": "Việc",
    "bug": "Sửa lỗi",
    "subtask": "Việc con",
}


def _points(task: Task) -> float:
    return float(task.story_points or 0)


def _day(at: datetime | None, tz: ZoneInfo) -> date | None:
    return at.astimezone(tz).date() if at else None


def sprint_tasks(session: Session, sprint_id: int) -> list[Task]:
    return list(session.scalars(select(Task).where(Task.sprint_id == sprint_id)))


def burndown(session: Session, sprint: Sprint, tz: ZoneInfo) -> dict[str, Any]:
    """Points and tasks left each day of the sprint (tasks it holds now), with the ideal line
    from the points committed when it started."""
    tasks = sprint_tasks(session, sprint.id)
    total = sum(_points(t) for t in tasks)
    committed = float(sprint.committed_points) if sprint.committed_points is not None else total
    today = datetime.now(tz).date()
    start = sprint.start_date or today
    end = sprint.end_date or start + timedelta(days=13)
    last = min(end, today) if sprint.state != "closed" else end
    span = max((end - start).days, 1)
    days = []
    day = start
    while day <= end:
        row: dict[str, Any] = {
            "day": day.isoformat(),
            "ideal_points": round(committed * (1 - (day - start).days / span), 1),
            "remaining_points": None,
            "remaining_tasks": None,
        }
        if day <= last:
            left = [t for t in tasks if (_day(t.resolved_at, tz) or date.max) > day]
            row["remaining_points"] = round(sum(_points(t) for t in left), 1)
            row["remaining_tasks"] = len(left)
        days.append(row)
        day += timedelta(days=1)
    return {"days": days, "committed_points": round(committed, 1)}


def _done_ids(session: Session, bp_id: int) -> set[int]:
    return set(
        session.scalars(
            select(TaskStatus.id).where(
                TaskStatus.business_project_id == bp_id, TaskStatus.category == "done"
            )
        )
    )


def report(session: Session, bp_id: int, first: date, last: date, tz: ZoneInfo) -> dict[str, Any]:
    lo = datetime.combine(first, datetime.min.time(), tz)
    hi = datetime.combine(last + timedelta(days=1), datetime.min.time(), tz)
    done = _done_ids(session, bp_id)
    tasks = list(session.scalars(select(Task).where(Task.business_project_id == bp_id)))

    sprints = list(
        session.scalars(
            select(Sprint)
            .where(Sprint.business_project_id == bp_id, Sprint.state == "closed")
            .order_by(Sprint.closed_at.desc().nulls_last())
            .limit(8)
        )
    )
    velocity = []
    for sprint in reversed(sprints):
        held = [t for t in tasks if t.sprint_id == sprint.id]
        completed = sum(_points(t) for t in held if t.status_id in done)
        velocity.append(
            {
                "sprint_id": sprint.id,
                "name": sprint.name,
                "committed": round(
                    float(sprint.committed_points)
                    if sprint.committed_points is not None
                    else sum(_points(t) for t in held),
                    1,
                ),
                "completed": round(completed, 1),
            }
        )

    created: dict[date, int] = defaultdict(int)
    resolved: dict[date, int] = defaultdict(int)
    for t in tasks:
        if t.created_at and lo <= t.created_at < hi:
            created[t.created_at.astimezone(tz).date()] += 1
        if t.resolved_at and lo <= t.resolved_at < hi:
            resolved[t.resolved_at.astimezone(tz).date()] += 1
    flow = []
    day = first
    while day <= last:
        flow.append({"day": day.isoformat(), "created": created[day], "resolved": resolved[day]})
        day += timedelta(days=1)

    finished = [t for t in tasks if t.resolved_at and lo <= t.resolved_at < hi]
    started: dict[int, datetime] = {}
    if finished:
        for task_id, at in session.execute(
            select(TaskHistory.task_id, func.min(TaskHistory.at))
            .where(TaskHistory.task_id.in_([t.id for t in finished]), TaskHistory.field == "status")
            .group_by(TaskHistory.task_id)
        ).all():
            started[int(task_id)] = at
    hours = sorted(
        max(
            ((t.resolved_at or t.created_at) - (started.get(t.id) or t.created_at)).total_seconds(),
            0,
        )
        / 3600
        for t in finished
        if t.created_at
    )

    def pct(q: float) -> float | None:
        if not hours:
            return None
        return round(hours[min(len(hours) - 1, int(q * (len(hours) - 1) + 0.5))], 1)

    statuses = service.statuses_of(session, bp_id)
    by_status = [
        {
            "status": service.status_out(s),
            "count": sum(1 for t in tasks if t.status_id == s.id),
        }
        for s in statuses
    ]

    # docs/specs/31 §18.3: tasks by priority in the dự án's order (Jira's "Issues by priority").
    today = datetime.now(tz).date()
    by_priority = []
    for prio in service.priorities_of(session, bp_id):
        held = [t for t in tasks if t.priority == prio.key]
        open_ = [t for t in held if t.status_id not in done]
        by_priority.append(
            {
                "priority": service.priority_out(prio),
                "open": len(open_),
                "overdue": sum(1 for t in open_ if t.due_date and t.due_date < today),
                "created": len([t for t in held if t.created_at and lo <= t.created_at < hi]),
                "resolved": len([t for t in held if t.resolved_at and lo <= t.resolved_at < hi]),
                "open_points": round(sum(_points(t) for t in open_), 1),
            }
        )

    users = service.users_by_id(session, {t.assignee_id for t in tasks})
    per: dict[int, dict[str, float]] = defaultdict(lambda: {"open": 0, "done": 0, "points": 0.0})
    for t in tasks:
        if t.assignee_id is None:
            continue
        if t.status_id not in done:
            per[t.assignee_id]["open"] += 1
        elif t.resolved_at and lo <= t.resolved_at < hi:
            per[t.assignee_id]["done"] += 1
            per[t.assignee_id]["points"] += _points(t)
    by_assignee = sorted(
        (
            {
                "user": service.person(users.get(uid)),
                "open": int(v["open"]),
                "done": int(v["done"]),
                "points": round(v["points"], 1),
            }
            for uid, v in per.items()
        ),
        key=lambda r: (-int(r["done"] or 0), -int(r["open"] or 0)),  # type: ignore[arg-type]
    )

    worklog_rows = session.execute(
        select(TaskWorklog.user_id, func.sum(TaskWorklog.minutes))
        .join(Task, Task.id == TaskWorklog.task_id)
        .where(
            Task.business_project_id == bp_id,
            TaskWorklog.day >= first,
            TaskWorklog.day <= last,
        )
        .group_by(TaskWorklog.user_id)
    ).all()
    worklog_pairs = [(uid, int(m or 0)) for uid, m in worklog_rows]
    wl_users = service.users_by_id(session, [uid for uid, _ in worklog_pairs])
    worklog = sorted(
        (
            {"user": service.person(wl_users.get(uid)) if uid else None, "minutes": minutes}
            for uid, minutes in worklog_pairs
        ),
        key=lambda r: -int(r["minutes"] or 0),  # type: ignore[arg-type]
    )
    return {
        "velocity": velocity,
        "flow": flow,
        "cycle_time": {"median_h": pct(0.5), "p85_h": pct(0.85), "count": len(hours)},
        "by_status": by_status,
        "by_priority": by_priority,
        "by_assignee": by_assignee,
        "worklog": worklog,
    }


def release_notes(session: Session, version: TaskVersion) -> str:
    """Markdown of a version: its finished tasks by type, each with its merged MRs."""
    done = _done_ids(session, version.business_project_id)
    tasks = [
        t
        for t in session.scalars(
            select(Task).where(Task.version_id == version.id).order_by(Task.number)
        )
        if t.status_id in done
    ]
    links = (
        list(
            session.scalars(
                select(TaskGitLink).where(
                    TaskGitLink.task_id.in_([t.id for t in tasks]), TaskGitLink.kind == "mr"
                )
            )
        )
        if tasks
        else []
    )
    repos = {
        p.id: p
        for p in session.scalars(
            select(Project).where(Project.id.in_({gl.project_id for gl in links if gl.project_id}))
        )
    }
    merged = {
        (mr.project_id, mr.iid)
        for mr in session.scalars(
            select(MergeRequest).where(
                MergeRequest.project_id.in_(list(repos)), MergeRequest.state == "merged"
            )
        )
    }
    by_task: dict[int, list[str]] = defaultdict(list)
    for gl in links:
        if (gl.project_id, gl.mr_iid) in merged:
            repo = repos.get(gl.project_id or -1)
            by_task[gl.task_id].append(f"{repo.path_with_namespace if repo else ''}!{gl.mr_iid}")
    when = version.release_date or (
        version.released_at.astimezone(UTC).date() if version.released_at else None
    )
    out = [f"# {version.name}" + (f" ({when.isoformat()})" if when else ""), ""]
    if version.description:
        out += [version.description.strip(), ""]
    if not tasks:
        out.append("_Chưa có task nào xong trong phiên bản này._")
    for kind in ("story", "task", "bug", "epic", "subtask"):
        group = [t for t in tasks if t.type == kind]
        if not group:
            continue
        out.append(f"## {TYPE_TITLES[kind]}")
        for t in group:
            mrs = f" ({', '.join(by_task[t.id])})" if by_task.get(t.id) else ""
            out.append(f"- **{t.key}** {t.title}{mrs}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def committed(session: Session, sprint: Sprint) -> float:
    return round(sum(_points(t) for t in sprint_tasks(session, sprint.id)), 1)


def names(session: Session, ids: set[int]) -> dict[int, str]:
    return {
        u.id: u.full_name or u.primary_email or f"#{u.id}"
        for u in session.scalars(select(User).where(User.id.in_(ids)))
    }
