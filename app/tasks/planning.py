"""Sprint planning help (docs/specs/31 §21.3, round 3 stage 3.3).

- Capacity: the mean finished points of the last 3 closed sprints (`reports.capacity`, §19.3), or
  a number typed in (`capacity_source = "manual"`), which is the only way before that history.
- Load per person: points of the sprint's Story / Task / Bug per assignee; a person holding more
  than 40 % of the sprint's points is flagged.
- Preflight before starting: tasks without an estimate, without an assignee, blocked by an open
  task outside the sprint (plus over capacity and overload). Warnings only: starting stays allowed.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import Sprint, Task, TaskLink, TaskStatus
from app.tasks import reports, service

OVERLOAD_PCT = 40.0
LIST_MAX = 50


def capacity_of(session: Session, sprint: Sprint) -> dict[str, Any]:
    """{capacity, source, auto}: the capacity in force and where it comes from."""
    auto = reports.capacity(session, sprint.business_project_id)
    if sprint.capacity_source == "manual" and sprint.capacity_points is not None:
        return {"capacity": float(sprint.capacity_points), "source": "manual", "auto": auto}
    return {"capacity": auto, "source": "auto", "auto": auto}


def set_capacity(sprint: Sprint, points: float | None) -> None:
    """A number makes the capacity manual; None goes back to the 3-sprint mean."""
    if points is None:
        sprint.capacity_points, sprint.capacity_source = None, "auto"
    else:
        sprint.capacity_points, sprint.capacity_source = Decimal(str(round(points, 1))), "manual"


def _items(session: Session, sprint: Sprint) -> list[Task]:
    """Story / Task / Bug of the sprint (epics and sub-tasks carry no sprint points)."""
    return [t for t in reports.sprint_tasks(session, sprint.id) if t.type in service.POINTED_TYPES]


def load(session: Session, tasks: list[Task]) -> list[dict[str, Any]]:
    """Points and tasks per assignee, by name; `over` when above 40 % of the sprint's points."""
    per: dict[int | None, dict[str, float]] = defaultdict(lambda: {"tasks": 0, "points": 0.0})
    for t in tasks:
        per[t.assignee_id]["tasks"] += 1
        per[t.assignee_id]["points"] += reports.points(t)
    total = sum(v["points"] for v in per.values())
    users = service.users_by_id(session, per.keys())
    out: list[dict[str, Any]] = []
    for uid, v in per.items():
        user = users.get(uid) if uid is not None else None
        pct = round(v["points"] * 100 / total, 1) if total > 0 else 0.0
        out.append(
            {
                "user": service.person(user),
                "name": (user.full_name or user.primary_email or f"#{user.id}")
                if user
                else "Chưa giao",
                "tasks": int(v["tasks"]),
                "points": round(v["points"], 1),
                "pct": pct,
                "over": uid is not None and pct > OVERLOAD_PCT,
            }
        )
    out.sort(key=lambda r: (r["user"] is None, -float(r["points"] or 0), str(r["name"]).casefold()))
    return out


def plan(session: Session, sprint: Sprint, can_sprint: bool) -> dict[str, Any]:
    """The Backlog's sprint header: points against capacity and the load per person."""
    tasks = _items(session, sprint)
    points = round(sum(reports.points(t) for t in tasks), 1)
    cap = capacity_of(session, sprint)
    rows = load(session, tasks)
    return {
        "sprint_id": sprint.id,
        "points": points,
        **cap,
        "over_capacity": cap["capacity"] is not None and points > float(cap["capacity"]),
        "load": rows,
        "overload_pct": OVERLOAD_PCT,
        "overloaded": [r["name"] for r in rows if r["over"]],
        "can_sprint": can_sprint,
    }


def _brief(t: Task) -> dict[str, Any]:
    return {"key": t.key, "title": t.title}


def preflight(session: Session, sprint: Sprint) -> dict[str, Any]:
    """Warnings before starting the sprint; `ok` when there is none."""
    done = {
        int(i)
        for i in session.scalars(
            select(TaskStatus.id).where(
                TaskStatus.business_project_id == sprint.business_project_id,
                TaskStatus.category == "done",
            )
        )
    }
    tasks = [t for t in _items(session, sprint) if t.status_id not in done]
    unestimated = [t for t in tasks if t.story_points is None]
    unassigned = [t for t in tasks if t.assignee_id is None]
    blocked: list[dict[str, Any]] = []
    if tasks:
        blocker = aliased(Task)
        status = aliased(TaskStatus)
        by_id = {t.id: t for t in tasks}
        for to_id, b in session.execute(
            select(TaskLink.to_task_id, blocker)
            .join(blocker, blocker.id == TaskLink.from_task_id)
            .join(status, status.id == blocker.status_id)
            .where(
                TaskLink.kind == "blocks",
                TaskLink.to_task_id.in_(list(by_id)),
                status.category != "done",
                (blocker.sprint_id.is_(None)) | (blocker.sprint_id != sprint.id),
            )
            .order_by(TaskLink.to_task_id, blocker.id)
        ).all():
            blocked.append(
                {**_brief(by_id[int(to_id)]), "blocker": _brief(b), "blocker_sprint": b.sprint_id}
            )
    p = plan(session, sprint, False)
    warnings: list[dict[str, Any]] = []
    if unestimated:
        warnings.append(
            {
                "kind": "unestimated",
                "text": f"{len(unestimated)} task chưa ước lượng SP",
                "count": len(unestimated),
                "tasks": [_brief(t) for t in unestimated[:LIST_MAX]],
            }
        )
    if unassigned:
        warnings.append(
            {
                "kind": "unassigned",
                "text": f"{len(unassigned)} task chưa có người làm",
                "count": len(unassigned),
                "tasks": [_brief(t) for t in unassigned[:LIST_MAX]],
            }
        )
    if blocked:
        warnings.append(
            {
                "kind": "blocked_outside",
                "text": f"{len(blocked)} task bị chặn bởi task ngoài sprint",
                "count": len(blocked),
                "tasks": blocked[:LIST_MAX],
            }
        )
    if p["over_capacity"]:
        warnings.append(
            {
                "kind": "over_capacity",
                "text": f"{p['points']:g} SP vượt sức chứa {float(p['capacity']):g} SP",
                "count": 1,
                "tasks": [],
            }
        )
    if p["overloaded"]:
        warnings.append(
            {
                "kind": "overloaded",
                "text": f"{', '.join(p['overloaded'])} giữ hơn {OVERLOAD_PCT:g}% SP của sprint",
                "count": len(p["overloaded"]),
                "tasks": [],
            }
        )
    return {"sprint_id": sprint.id, "ok": not warnings, "warnings": warnings}
