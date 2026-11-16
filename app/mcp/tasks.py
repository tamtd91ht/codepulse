"""MCP tools on CodePulse tasks (docs/specs/31 §10): what to work on next, a task with its
code, and the few writes a developer makes from Claude Code (move, comment, log time). The rules
are the dashboard's (app/tasks/service.py)."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.mcp import queries, render
from app.models import Task, TaskComment, TaskStatus, TaskWorklog
from app.tasks import keys as tk
from app.tasks import service as ts

Work = tuple[str, dict[str, Any], str | None]


def _task(session: Session, caller: Any, key: str) -> Task:
    if not caller.principal.can("tasks.view"):
        raise queries.McpError("forbidden", "tài khoản không có quyền xem task")
    task = session.scalars(select(Task).where(Task.key == key.strip().upper())).first()
    if task is None or not ts.can_view_space(caller.principal, task.business_project_id):
        raise queries.McpError("not_found", f"không thấy task {key}")
    return task


def _line(row: dict[str, Any]) -> str:
    status = (row.get("status") or {}).get("name", "")
    git = row["git"]
    warn = f" ⚠ {git['open_critical']} critical mở" if git["open_critical"] else ""
    return f"- **{row['key']}** [{render.inline(status)}] {render.inline(row['title'])}{warn}"


def my_tasks_work(
    session: Session,
    caller: Any,
    settings: Settings,
    facts: Any,
    *,
    status: str | None,
    space: str | None,
    limit: int,
) -> Work:
    if not caller.principal.can("tasks.view"):
        raise queries.McpError("forbidden", "tài khoản không có quyền xem task")
    facts.own_only = True
    query = (
        select(Task)
        .join(TaskStatus, TaskStatus.id == Task.status_id)
        .where(Task.assignee_id == caller.user_id)
    )
    if status in ("todo", "doing", "done"):
        query = query.where(TaskStatus.category == status)
    else:
        query = query.where(TaskStatus.category != "done")
    if space:
        query = query.where(Task.key.startswith(f"{space.strip().upper()}-"))
    tasks = [
        t
        for t in session.scalars(query.order_by(Task.updated_at.desc()).limit(limit * 2))
        if ts.can_view_space(caller.principal, t.business_project_id)
    ][:limit]
    rows = ts.rows(session, tasks)
    text = "\n".join(_line(r) for r in rows) or "Không có task nào giao cho bạn."
    return f"## Task của bạn ({len(rows)})\n{text}", {"tasks": rows}, None


def get_task_work(
    session: Session, caller: Any, settings: Settings, facts: Any, *, key: str
) -> Work:
    task = _task(session, caller, key)
    row = ts.rows(session, [task])[0]
    git = ts.git_detail(session, task)
    facts.own_only = caller.user_id in (task.assignee_id, task.reporter_id)
    lines = [
        f"## {task.key}: {render.inline(task.title)}",
        f"Loại {task.type} · trạng thái {render.inline(row['status']['name'])}"
        f" · ưu tiên {task.priority}",
        "",
        "Mô tả: " + render.inline((task.description or "")[:6000]),
        "",
        "### Code",
    ]
    s = git["summary"]
    lines.append(
        f"{s['commits']} commit, {s['mrs']} MR, điểm TB {s['avg_score']}, "
        f"critical mở {s['open_critical']}, high mở {s['open_high']}"
    )
    for c in git["commits"][:20]:
        review = c["review"]
        extra = f" — review {review['score']}" if review else ""
        lines.append(f"- {c['sha'][:10]} {render.inline(c['title'])} [{c['relation']}]{extra}")
    for m in git["mrs"][:20]:
        lines.append(f"- MR {m['repo']}!{m['iid']} ({m['state']}) {render.inline(m['title'])}")
    statuses = ", ".join(
        f"{tk.slug(s.name)}" for s in ts.statuses_of(session, task.business_project_id)
    )
    lines += ["", f"Trạng thái dùng được với move_task: {statuses}"]
    return (
        "\n".join(lines),
        {"task": row, "description": task.description, "git": git},
        f"task:{task.key}",
    )


def move_task_work(
    session: Session, caller: Any, settings: Settings, facts: Any, *, key: str, status: str
) -> Work:
    task = _task(session, caller, key)
    if not ts.can_edit(caller.principal, task):
        raise queries.McpError("forbidden", "bạn không sửa được task này")
    wanted = tk.slug(status)
    target = next(
        (s for s in ts.statuses_of(session, task.business_project_id) if tk.slug(s.name) == wanted),
        None,
    )
    if target is None:
        raise queries.McpError("invalid", f"dự án không có trạng thái {status}")
    ts.set_status(session, task, target, caller.user_id, "mcp")
    return (
        f"{task.key} → {render.inline(target.name)}",
        {"key": task.key, "status": target.name},
        f"task:{task.key}",
    )


def comment_task_work(
    session: Session, caller: Any, settings: Settings, facts: Any, *, key: str, body: str
) -> Work:
    task = _task(session, caller, key)
    text = body.strip()
    if not text:
        raise queries.McpError("invalid", "bình luận trống")
    session.add(TaskComment(task_id=task.id, author_id=caller.user_id, body=text[:20_000]))
    ts.watch(session, task, caller.user_id)
    who = ts.mentioned(session, task.business_project_id, text)
    if who:
        ts.tell(
            session,
            who,
            task,
            "task.mentioned",
            f"Bạn được nhắc ở {task.key}",
            text[:300],
            actor_id=caller.user_id,
        )
    others = (
        (ts.watchers(session, task) | {task.assignee_id or 0, task.reporter_id or 0}) - who - {0}
    )
    ts.tell(
        session,
        others,
        task,
        "task.commented",
        f"Bình luận mới ở {task.key}",
        text[:300],
        actor_id=caller.user_id,
    )
    return f"Đã bình luận ở {task.key}.", {"key": task.key}, f"task:{task.key}"


def log_work_work(
    session: Session,
    caller: Any,
    settings: Settings,
    facts: Any,
    *,
    key: str,
    minutes: int,
    note: str | None,
) -> Work:
    task = _task(session, caller, key)
    if not ts.can_edit(caller.principal, task):
        raise queries.McpError("forbidden", "bạn không sửa được task này")
    from datetime import UTC, datetime

    session.add(
        TaskWorklog(
            task_id=task.id,
            user_id=caller.user_id,
            minutes=minutes,
            day=datetime.now(UTC).date(),
            note=(note or "")[:500] or None,
            via="mcp",
        )
    )
    task.spent_min = (task.spent_min or 0) + minutes
    ts.log(session, task, caller.user_id, "worklog", None, f"{minutes} phút", "mcp")
    return (
        f"Đã ghi {minutes} phút cho {task.key} (tổng {task.spent_min} phút).",
        {"key": task.key, "spent_min": task.spent_min},
        f"task:{task.key}",
    )
