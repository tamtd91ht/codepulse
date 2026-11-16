"""Việc của tôi (docs/specs/31 §19.3): one page that answers "what do I do next, what is blocking
me". Everything is read from what CodePulse already knows; nothing to fill in.

- attention: my open tasks past their due date; my open tasks whose code still has open critical
  findings; open MRs where I am a GitLab reviewer (not the author); task @mentions I have not read;
  failed tests on the cases that cover my tasks (once linked, §19.4).
- doing / waiting (blocked by an open task) / todo (active sprint first) / done this week.
- sprint: each active sprint where I hold tasks: days left, my points done / total.

Only tasks of dự án the person may see (tasks.view), only MRs of repos they may see.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.models import (
    Account,
    Connection,
    MergeRequest,
    Project,
    Sprint,
    Task,
    TaskGitLink,
    TaskLink,
    TaskSpace,
    TaskStatus,
    UserNotification,
)
from app.tasks import reports
from app.tasks import service as ts

TODO_MAX = 30
REVIEW_MAX = 20


def _visible_spaces(session: Session, p: Principal) -> list[int]:
    return [
        int(bp)
        for bp in session.scalars(select(TaskSpace.business_project_id))
        if ts.can_view_space(p, int(bp))
    ]


def _mr_url(session: Session, project: Project, iid: int) -> str | None:
    if not project.connection_id or not project.path_with_namespace:
        return None
    conn = session.get(Connection, project.connection_id)
    if conn is None or not conn.base_url:
        return None
    return f"{conn.base_url.rstrip('/')}/{project.path_with_namespace}/-/merge_requests/{iid}"


def my_work(session: Session, p: Principal, tz: ZoneInfo) -> dict[str, Any]:
    me = p.user_id
    today = datetime.now(tz).date()
    spaces = _visible_spaces(session, p)
    category = {
        int(s.id): str(s.category)
        for s in session.scalars(
            select(TaskStatus).where(TaskStatus.business_project_id.in_(spaces))
        )
    }
    mine = list(
        session.scalars(
            select(Task).where(Task.assignee_id == me, Task.business_project_id.in_(spaces))
        )
    )
    open_ = [t for t in mine if category.get(t.status_id) != "done"]
    rows = {r["id"]: r for r in ts.rows(session, open_)}
    attention: list[dict[str, Any]] = []
    seen: set[int] = set()

    def flag(task: Task, kind: str, text: str, action: str) -> None:
        attention.append({"kind": kind, "text": text, "action": action, "task": rows[task.id]})
        seen.add(task.id)

    for t in sorted(open_, key=lambda x: (x.due_date or date.max, x.id)):
        if t.due_date is not None and t.due_date < today:
            late = (today - t.due_date).days
            flag(
                t,
                "overdue",
                f"quá hạn {late} ngày",
                "start" if category.get(t.status_id) == "todo" else "open",
            )
    for t in open_:
        crit = rows[t.id]["git"].get("open_critical", 0) if t.id in rows else 0
        if crit:
            flag(t, "critical", f"code còn {crit} finding critical mở", "findings")

    # MRs waiting for my review (GitLab reviewers), oldest first.
    my_accounts = [
        int(a)
        for a in session.scalars(
            select(Account.id).where(Account.user_id == me, Account.kind == "gitlab_user")
        )
    ]
    reviews: list[dict[str, Any]] = []
    if my_accounts:
        # In SQL: a JSONB containment per account, the author excluded (no scan of every MR).
        from sqlalchemy import or_

        found = session.scalars(
            select(MergeRequest)
            .where(
                MergeRequest.state == "opened",
                or_(*(MergeRequest.reviewer_account_ids.contains([a]) for a in my_accounts)),
                or_(
                    MergeRequest.author_account_id.is_(None),
                    MergeRequest.author_account_id.not_in(my_accounts),
                ),
            )
            .order_by(MergeRequest.created_at_src)
            .limit(REVIEW_MAX * 3)
        )
        for mr in found:
            if not set(mr.reviewer_account_ids or []) & set(my_accounts):
                continue
            if mr.author_account_id in my_accounts or mr.project_id is None:
                continue
            project = session.get(Project, mr.project_id)
            if project is None or not p.can_view_project(project.business_project_id):
                continue
            task_key = session.scalar(
                select(Task.key)
                .join(TaskGitLink, TaskGitLink.task_id == Task.id)
                .where(
                    TaskGitLink.kind == "mr",
                    TaskGitLink.project_id == mr.project_id,
                    TaskGitLink.mr_iid == mr.iid,
                    Task.business_project_id.in_(spaces),
                )
                .limit(1)
            )
            age_h = (
                int((datetime.now(UTC) - mr.created_at_src).total_seconds() // 3600)
                if mr.created_at_src
                else None
            )
            reviews.append(
                {
                    "kind": "review",
                    "text": f"được nhờ review MR !{mr.iid}"
                    + (f" từ {age_h // 24} ngày" if age_h and age_h >= 24 else ""),
                    "action": "review",
                    "mr": {
                        "project": project.path_with_namespace,
                        "iid": mr.iid,
                        "title": mr.title,
                        "web_url": _mr_url(session, project, mr.iid),
                        "age_hours": age_h,
                        "first_review_at": mr.first_review_at.isoformat()
                        if mr.first_review_at
                        else None,
                    },
                    "task_key": task_key,
                }
            )
            if len(reviews) >= REVIEW_MAX:
                break
    attention += reviews

    mentions = list(
        session.scalars(
            select(UserNotification)
            .where(
                UserNotification.user_id == me,
                UserNotification.kind == "task.mentioned",
                UserNotification.read_at.is_(None),
            )
            .order_by(UserNotification.id.desc())
            .limit(10)
        )
    )
    for n in mentions:
        attention.append(
            {
                "kind": "mention",
                "text": n.title,
                "action": "reply",
                "link": n.link,
                "notification_id": n.id,
            }
        )

    # Failed tests on cases covering my tasks (§19.4 links).
    from app.tasks import quality

    for item in quality.failing_for(session, [t.id for t in open_]):
        task = next((t for t in open_ if t.id == item["task_id"]), None)
        if task is not None:
            flag(task, "test_failed", item["text"], "open")
    # Tests to run again now their bugs are done (§20.3): mine, or failed by me.
    attention += quality.retests_for(session, p)

    # Blocked: an open task "blocks" mine.
    blocked: dict[int, list[str]] = defaultdict(list)
    if open_:
        for link, blocker in session.execute(
            select(TaskLink, Task)
            .join(Task, Task.id == TaskLink.from_task_id)
            .where(TaskLink.kind == "blocks", TaskLink.to_task_id.in_([t.id for t in open_]))
        ).all():
            if category.get(blocker.status_id) != "done":
                blocked[link.to_task_id].append(blocker.key)
    waiting = [{**rows[t.id], "blocked_by": blocked[t.id]} for t in open_ if blocked.get(t.id)]
    doing = [
        rows[t.id]
        for t in sorted(open_, key=lambda x: x.rank)
        if category.get(t.status_id) == "doing" and t.id not in blocked
    ]
    active = {
        int(s.id): s
        for s in session.scalars(
            select(Sprint).where(Sprint.business_project_id.in_(spaces), Sprint.state == "active")
        )
    }
    todo = [
        rows[t.id]
        for t in sorted(
            open_, key=lambda x: (x.sprint_id not in active, x.due_date or date.max, x.rank)
        )
        if category.get(t.status_id) == "todo" and t.id not in blocked
    ][:TODO_MAX]
    monday = today - timedelta(days=today.weekday())
    since = datetime.combine(monday, datetime.min.time(), tz)
    done_week = [
        t
        for t in mine
        if category.get(t.status_id) == "done"
        and (t.resolution or "done") == "done"
        and t.resolved_at is not None
        and t.resolved_at >= since
    ]
    sprints = []
    for sprint in active.values():
        held = [t for t in mine if t.sprint_id == sprint.id]
        if not held:
            continue
        total = sum(reports.points(t) for t in held)
        done_points = sum(reports.points(t) for t in held if category.get(t.status_id) == "done")
        sprints.append(
            {
                "id": sprint.id,
                "name": sprint.name,
                "space": sprint.business_project_id,
                "end_date": sprint.end_date.isoformat() if sprint.end_date else None,
                "days_left": (sprint.end_date - today).days if sprint.end_date else None,
                "points": round(total, 1),
                "done_points": round(done_points, 1),
                "tasks": len(held),
                "done_tasks": sum(1 for t in held if category.get(t.status_id) == "done"),
            }
        )
    return {
        "attention": attention,
        "doing": doing,
        "waiting": waiting,
        "todo": todo,
        "done_week": ts.rows(session, sorted(done_week, key=lambda t: t.resolved_at or since)),
        "sprints": sprints,
    }
