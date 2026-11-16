"""Task automation and timely notifications (docs/specs/31 §19.7).

**Rules (H).** A small library of rule templates, each switched on or off per task space, some
with a parameter. Stored in `task_spaces.rules["automation"]` as `{template: {"on": bool, ...}}`
(no table of its own). No free-form rule builder. Three kinds:

- *ui*: the dashboard asks (closing open sub-tasks with their parent);
- *event*: run from `service.set_status` / `service.create_task` (`on_status`, `on_create`);
- *schedule*: run by the scheduler's tick (`run_schedule`, app/workers/task_automation_jobs.py).

Automation acts as the system: history rows have no user, changes go through the same service
functions (workflow transitions are respected; a move the workflow forbids is skipped), and a
rule never triggers itself or another rule within the same call (`_ACTIVE`, a context variable,
so threads of the API do not share it). Every automatic change runs in a SAVEPOINT: a rule that
fails takes back only its own work, never the person's action that triggered it.

Reminders are sent at most once per task, person and day: their notification `ref` carries the
local date (`auto:<rule>:<task id>:<YYYY-MM-DD>`) and is looked for in the inbox history and in
the held notifications before a new one is written.

**Notifications (I).** Each person's task notification preferences (`app_settings` key
`task_notify:<user id>`, no migration): `mode` immediate | digest, `quiet_hours` [start, end)
local hours (REPORT_TZ), `kinds` muted among MUTABLE_KINDS. `deliver_now` is called by
`app.notifications.inbox.notify` for every `task.*` kind (so `service.tell` needs no change):
muted kinds are dropped, digest mode and quiet hours hold the notification
(`task_notify_held:<user id>`, appended atomically) and `send_digests` later writes the held ones
into the history as read rows plus one unread summary ("3 cập nhật trên CRM-2, CRM-5").
"""

import html
import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, SessionTransaction

from app.models import (
    AppSetting,
    ProjectGrant,
    Task,
    TaskGitLink,
    TaskSpace,
    TaskStatus,
    User,
    UserNotification,
)
from app.models.tasks import TaskLink

log = logging.getLogger(__name__)

# -- the rule library -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    default: int
    min: int
    max: int
    unit: str


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    hint: str
    trigger: str  # ui | event | schedule
    params: tuple[Param, ...] = ()
    default_on: bool = False


TEMPLATES: tuple[Template, ...] = (
    Template(
        "parent_done_ask",
        "Đóng task cha thì hỏi đóng sub-task",
        "Chuyển task cha sang Xong khi còn sub-task mở: hỏi có đóng luôn các sub-task không.",
        "ui",
        default_on=True,
    ),
    Template(
        "subtasks_done_review",
        "Sub-task xong hết → task cha sang review",
        "Sub-task cuối cùng xong thì task cha (đang Cần làm / Đang làm) sang trạng thái review "
        "của luật git.",
        "event",
    ),
    Template(
        "assign_on_start",
        "Bắt đầu task chưa có người làm → giao cho người bắt đầu",
        "Người chuyển task chưa giao sang nhóm Đang làm (trên web / MCP) thành người làm.",
        "event",
    ),
    Template(
        "bug_highest_notify",
        "Bug ưu tiên cao nhất → báo quản lý",
        "Tạo Bug có mức ưu tiên cao nhất: báo các quản lý dự án trong hộp thư, và nhóm Telegram "
        "của bot dự án nếu có.",
        "event",
    ),
    Template(
        "critical_reassign",
        "Code còn critical → báo người làm",
        "Task vào review / Xong, hoặc mỗi ngày khi task mở có code còn finding critical: báo "
        "người làm (không tự đổi người làm).",
        "event",
    ),
    Template(
        "unblock_notify",
        "Hết bị chặn → báo người làm",
        "Task chặn (blocks) task khác vừa xong và không còn gì chặn task đó: báo người làm của nó.",
        "event",
    ),
    Template(
        "review_stale_remind",
        "Nằm ở review quá lâu → nhắc",
        "Task ở trạng thái review quá N giờ: nhắc người làm và người tạo, mỗi ngày một lần.",
        "schedule",
        (Param("hours", "Sau (giờ)", 48, 1, 24 * 30, "giờ"),),
    ),
    Template(
        "overdue_remind",
        "Quá hạn → nhắc người làm",
        "Task mở đã quá hạn: nhắc người làm mỗi ngày một lần.",
        "schedule",
    ),
    Template(
        "due_soon_remind",
        "Sắp tới hạn → nhắc người làm",
        "Task mở còn N ngày tới hạn: nhắc người làm mỗi ngày một lần.",
        "schedule",
        (Param("days", "Trước (ngày)", 1, 0, 14, "ngày"),),
    ),
)
BY_KEY = {t.key: t for t in TEMPLATES}
# Reminders and digests are not sent before this local hour (REPORT_TZ).
SEND_HOUR = 8
DEFAULT_TZ = "Asia/Ho_Chi_Minh"


def config_of(space: TaskSpace | None) -> dict[str, dict[str, Any]]:
    """Every template with its switch and parameters (defaults filled in)."""
    stored = ((space.rules or {}).get("automation") if space else None) or {}
    out: dict[str, dict[str, Any]] = {}
    for t in TEMPLATES:
        mine = stored.get(t.key) or {}
        row: dict[str, Any] = {"on": bool(mine.get("on", t.default_on))}
        for p in t.params:
            value = mine.get(p.key)
            row[p.key] = value if isinstance(value, int) and p.min <= value <= p.max else p.default
        out[t.key] = row
    return out


def is_on(space: TaskSpace | None, key: str) -> bool:
    return bool(config_of(space)[key]["on"])


def set_config(space: TaskSpace, body: dict[str, Any]) -> None:
    """Change some templates (others keep their setting); unknown names or bad values: 422."""
    current = config_of(space)
    for key, value in body.items():
        template = BY_KEY.get(key)
        if template is None or not isinstance(value, dict):
            raise HTTPException(422, f"quy tắc không biết: {key}")
        row = dict(current[key])
        for name, v in value.items():
            if name == "on":
                if not isinstance(v, bool):
                    raise HTTPException(422, f"{key}.on phải là true / false")
                row["on"] = v
                continue
            param = next((p for p in template.params if p.key == name), None)
            if param is None:
                raise HTTPException(422, f"tham số không biết: {key}.{name}")
            if isinstance(v, bool) or not isinstance(v, int) or not param.min <= v <= param.max:
                raise HTTPException(
                    422, f"{key}.{name} phải là số nguyên từ {param.min} đến {param.max}"
                )
            row[name] = v
        current[key] = row
    # A new dict: JSONB columns notice reassignment, not in-place change.
    space.rules = {**(space.rules or {}), "automation": current}


def out(space: TaskSpace) -> list[dict[str, Any]]:
    config = config_of(space)
    return [
        {
            "key": t.key,
            "title": t.title,
            "hint": t.hint,
            "trigger": t.trigger,
            "on": config[t.key]["on"],
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "value": config[t.key][p.key],
                    "default": p.default,
                    "min": p.min,
                    "max": p.max,
                    "unit": p.unit,
                }
                for p in t.params
            ],
        }
        for t in TEMPLATES
    ]


# -- the re-entrancy guard ------------------------------------------------------------------------

_ACTIVE: ContextVar[bool] = ContextVar("task_automation_active", default=False)


@contextmanager
def _acting() -> Iterator[None]:
    token = _ACTIVE.set(True)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def _safely(session: Session, rule: str, task: Task, fn: Any, *args: Any) -> bool:
    """One rule in a SAVEPOINT: a rule that fails (a workflow that forbids the move, a gate)
    takes back only its own changes. The caller flushed its own work first, so an error here is
    the rule's."""
    try:
        with session.begin_nested():
            fn(session, task, *args)
        return True
    except HTTPException as exc:
        log.info("task rule skipped", extra={"rule": rule, "task": task.key, "why": exc.detail})
    except Exception:
        log.exception("task rule failed", extra={"rule": rule, "task": task.key})
    return False


# -- helpers --------------------------------------------------------------------------------------


def _tz() -> ZoneInfo:
    try:
        from app.config import get_settings

        return ZoneInfo(get_settings().report_tz)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _status_of(session: Session, status_id: int | None) -> TaskStatus | None:
    return session.get(TaskStatus, status_id) if status_id is not None else None


def _review_status(session: Session, space: TaskSpace) -> TaskStatus | None:
    review_id = (space.rules or {}).get("review")
    status = _status_of(session, review_id) if isinstance(review_id, int) else None
    if status is None or status.business_project_id != space.business_project_id:
        return None
    return status


def _sent(session: Session, user_id: int, ref: str) -> bool:
    """A notification with this ref reached the person already (or waits in their digest)."""
    found = session.scalar(
        select(UserNotification.id)
        .where(UserNotification.user_id == user_id, UserNotification.ref == ref)
        .limit(1)
    )
    if found is not None:
        return True
    held = session.get(AppSetting, HELD_KEY.format(user_id))
    return bool(held and any(i.get("ref") == ref for i in (held.value or [])))


def _remind(
    session: Session,
    users: Iterable[int | None],
    task: Task,
    rule: str,
    day: date,
    *,
    kind: str,
    title: str,
    body: str | None = None,
    tone: str = "warn",
) -> int:
    """Once per task, rule, person and local day."""
    from app.notifications.inbox import notify
    from app.tasks.service import link_of

    ref = f"auto:{rule}:{task.id}:{day.isoformat()}"
    targets = [u for u in {u for u in users if u is not None} if not _sent(session, u, ref)]
    if not targets:
        return 0
    notify(
        session,
        targets,
        kind=kind,
        title=title,
        body=body,
        link=link_of(task.key),
        tone=tone,
        ref=ref,
    )
    return len(targets)


def _open_critical(session: Session, task_ids: list[int]) -> dict[int, int]:
    from app.tasks.service import git_summaries

    return {i: int(s["open_critical"]) for i, s in git_summaries(session, task_ids).items()}


# -- event rules ----------------------------------------------------------------------------------


def on_status(
    session: Session,
    task: Task,
    old_category: str | None,
    new_status: TaskStatus,
    actor_id: int | None,
    via: str,
) -> None:
    """Called by `service.set_status` once the move is applied (before its flush)."""
    if _ACTIVE.get():
        return
    space = session.get(TaskSpace, task.business_project_id)
    if space is None or not (space.rules or {}).get("automation"):
        return
    config = config_of(space)
    # The person's own change is written first: its errors are theirs, not a rule's.
    session.flush()
    with _acting():
        new = new_status.category
        if (
            config["assign_on_start"]["on"]
            and new == "doing"
            and old_category != "doing"
            and task.assignee_id is None
            and actor_id is not None
            and via in ("web", "mcp")
        ):
            _safely(session, "assign_on_start", task, _assign_to, actor_id)
        review = _review_status(session, space)
        entered_review = review is not None and new_status.id == review.id
        if config["critical_reassign"]["on"] and (new == "done" or entered_review):
            _safely(session, "critical_reassign", task, _critical_now)
        if new == "done" and old_category != "done":
            if config["subtasks_done_review"]["on"] and task.type == "subtask" and task.parent_id:
                _safely(session, "subtasks_done_review", task, _parent_to_review, space, review)
            if config["unblock_notify"]["on"]:
                _safely(session, "unblock_notify", task, _unblocked)


def _assign_to(session: Session, task: Task, user_id: int) -> None:
    from app.tasks.service import log as history
    from app.tasks.service import watch

    user = session.get(User, user_id)
    task.assignee_id = user_id
    history(session, task, None, "assignee", None, user.full_name if user else user_id)
    watch(session, task, user_id)


def _critical_now(session: Session, task: Task) -> None:
    n = _open_critical(session, [task.id]).get(task.id, 0)
    if n and task.assignee_id is not None:
        _remind(
            session,
            [task.assignee_id],
            task,
            "critical",
            datetime.now(_tz()).date(),
            kind="task.alert",
            title=f"{task.key}: code còn {n} finding critical mở",
            body=task.title,
            tone="bad",
        )


def _parent_to_review(
    session: Session, task: Task, space: TaskSpace, review: TaskStatus | None
) -> None:
    from app.tasks.service import set_status

    parent = session.get(Task, task.parent_id)
    if parent is None or review is None or parent.status_id == review.id:
        return
    current = _status_of(session, parent.status_id)
    if current is None or current.category == "done":
        return
    open_left = session.scalar(
        select(Task.id)
        .join(TaskStatus, TaskStatus.id == Task.status_id)
        .where(Task.parent_id == parent.id, Task.type == "subtask", TaskStatus.category != "done")
        .limit(1)
    )
    if open_left is None:
        set_status(session, parent, review, None, "web")


def _unblocked(session: Session, task: Task) -> None:
    from app.notifications.inbox import notify
    from app.tasks.service import link_of

    blocked_ids = list(
        session.scalars(
            select(TaskLink.to_task_id).where(
                TaskLink.from_task_id == task.id, TaskLink.kind == "blocks"
            )
        )
    )
    for blocked in session.scalars(select(Task).where(Task.id.in_(blocked_ids))):
        status = _status_of(session, blocked.status_id)
        if blocked.assignee_id is None or status is None or status.category == "done":
            continue
        still = session.scalar(
            select(Task.id)
            .join(TaskLink, TaskLink.from_task_id == Task.id)
            .join(TaskStatus, TaskStatus.id == Task.status_id)
            .where(
                TaskLink.to_task_id == blocked.id,
                TaskLink.kind == "blocks",
                TaskStatus.category != "done",
                Task.id != task.id,
            )
            .limit(1)
        )
        if still is None:
            notify(
                session,
                [blocked.assignee_id],
                kind="task.alert",
                title=f"{blocked.key} không còn bị chặn: {task.key} đã xong",
                body=blocked.title,
                link=link_of(blocked.key),
                tone="good",
                ref=f"task:{blocked.id}",
            )


def on_create(session: Session, task: Task, actor_id: int | None) -> None:
    """Called by `service.create_task` once the task is written."""
    if _ACTIVE.get() or task.type != "bug":
        return
    space = session.get(TaskSpace, task.business_project_id)
    if space is None or not is_on(space, "bug_highest_notify"):
        return
    session.flush()
    with _acting():
        told = _safely(session, "bug_highest_notify", task, _bug_alert, actor_id)
    if (
        told
        and _priority_level(session, task) == "highest"
        and _telegram_chats(session, task.business_project_id)
    ):
        session.info.setdefault(TG_PENDING, []).append(task.id)


def _priority_level(session: Session, task: Task) -> str:
    from app.tasks.service import _level_of, priority_meta

    meta = priority_meta(session, [task.business_project_id])
    found = meta.get((task.business_project_id, task.priority))
    return found.level if found is not None else _level_of(task.priority)


def managers(session: Session, bp_id: int) -> list[int]:
    """The dự án's managers: people with a "manage" grant (Lead / Manager included), active."""
    return list(
        session.scalars(
            select(User.id)
            .join(ProjectGrant, ProjectGrant.user_id == User.id)
            .where(
                ProjectGrant.business_project_id == bp_id,
                ProjectGrant.role == "manage",
                User.active.is_not(False),
            )
        )
    )


def _bug_alert(session: Session, task: Task, actor_id: int | None) -> None:
    from app.notifications.inbox import notify
    from app.tasks.service import link_of

    if _priority_level(session, task) != "highest":
        return
    notify(
        session,
        managers(session, task.business_project_id),
        kind="task.alert",
        title=f"Bug ưu tiên cao nhất: {task.key}",
        body=task.title,
        link=link_of(task.key),
        tone="bad",
        actor_id=actor_id,
        ref=f"task:{task.id}",
    )


# -- Telegram (after commit, on a worker) ---------------------------------------------------------

TG_PENDING = "task_automation_telegram"


def _telegram_chats(session: Session, bp_id: int) -> list[tuple[int, list[int]]]:
    """(bot id, chat ids) of the dự án's enabled bots that name their chats (a bot answering
    any chat has no group to post in)."""
    from app.models import TelegramBot

    return [
        (bot.id, [int(c) for c in bot.allowed_chat_ids or []])
        for bot in session.scalars(
            select(TelegramBot).where(
                TelegramBot.business_project_id == bp_id, TelegramBot.enabled.is_(True)
            )
        )
        if bot.allowed_chat_ids
    ]


def _enqueue_redis(task_ids: list[int]) -> None:
    from rq import Queue

    from app.db import get_redis

    queue = Queue("default", connection=get_redis())
    for task_id in task_ids:
        queue.enqueue("app.workers.task_automation_jobs.telegram_alert_job", task_id)


# Swapped in tests; never raises into the caller (the event is already committed).
telegram_enqueuer: Any = _enqueue_redis


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    task_ids: list[int] = session.info.pop(TG_PENDING, [])
    if not task_ids:
        return
    try:
        telegram_enqueuer(task_ids)
    except Exception:
        log.warning("telegram task alert not queued", exc_info=True)


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop(TG_PENDING, None)


def telegram_text(session: Session, task: Task, base_url: str) -> str:
    from app.tasks.service import link_of

    who = session.get(User, task.reporter_id) if task.reporter_id else None
    return (
        f"<b>Bug ưu tiên cao nhất: {html.escape(task.key)}</b>\n"
        f"{html.escape(task.title)}\n"
        f"Người tạo: {html.escape(who.full_name or '') if who else '—'}\n"
        f"{html.escape(base_url.rstrip('/') + link_of(task.key))}"
    )


# -- scheduled rules ------------------------------------------------------------------------------


def run_schedule(session: Session, now: datetime | None = None) -> dict[str, int]:
    """The scheduled rules of every space that has one on; reminders go out from SEND_HOUR."""
    now = now or datetime.now(UTC)
    local = now.astimezone(_tz())
    sent: Counter[str] = Counter()
    if local.hour < SEND_HOUR:
        return dict(sent)
    today = local.date()
    for space in session.scalars(select(TaskSpace)):
        if not (space.rules or {}).get("automation"):
            continue
        config = config_of(space)
        mine: Counter[str] = Counter()
        try:
            # One space failing (a bad row) neither stops nor rolls back the others.
            with _acting(), session.begin_nested():
                if config["overdue_remind"]["on"]:
                    mine["overdue"] += _overdue(session, space, today)
                if config["due_soon_remind"]["on"]:
                    mine["due_soon"] += _due_soon(
                        session, space, today, config["due_soon_remind"]["days"]
                    )
                if config["review_stale_remind"]["on"]:
                    hours = config["review_stale_remind"]["hours"]
                    mine["review_stale"] += _review_stale(session, space, now, today, hours)
                if config["critical_reassign"]["on"]:
                    mine["critical"] += _critical_daily(session, space, today)
            sent.update(mine)
        except Exception:
            log.exception("task schedule failed", extra={"space": space.key})
    return dict(sent)


def _open_tasks(session: Session, bp_id: int) -> Any:
    return (
        select(Task)
        .join(TaskStatus, TaskStatus.id == Task.status_id)
        .where(
            Task.business_project_id == bp_id,
            TaskStatus.category != "done",
            Task.type != "epic",
        )
    )


def _overdue(session: Session, space: TaskSpace, today: date) -> int:
    n = 0
    for task in session.scalars(
        _open_tasks(session, space.business_project_id).where(
            Task.due_date < today, Task.assignee_id.is_not(None)
        )
    ):
        late = (today - task.due_date).days if task.due_date else 0
        n += _remind(
            session,
            [task.assignee_id],
            task,
            "overdue",
            today,
            kind="task.remind",
            title=f"{task.key} quá hạn {late} ngày",
            body=task.title,
            tone="bad",
        )
    return n


def _due_soon(session: Session, space: TaskSpace, today: date, days: int) -> int:
    n = 0
    for task in session.scalars(
        _open_tasks(session, space.business_project_id).where(
            Task.due_date >= today,
            Task.due_date <= today + timedelta(days=days),
            Task.assignee_id.is_not(None),
        )
    ):
        left = (task.due_date - today).days if task.due_date else 0
        n += _remind(
            session,
            [task.assignee_id],
            task,
            "due_soon",
            today,
            kind="task.remind",
            title=f"{task.key} tới hạn {'hôm nay' if left == 0 else f'sau {left} ngày'}",
            body=task.title,
        )
    return n


def _review_stale(
    session: Session, space: TaskSpace, now: datetime, today: date, hours: int
) -> int:
    review = _review_status(session, space)
    if review is None:
        return 0
    n = 0
    for task in session.scalars(
        select(Task).where(
            Task.status_id == review.id,
            Task.status_changed_at < now - timedelta(hours=hours),
        )
    ):
        since = task.status_changed_at or now
        waited = int((now - since).total_seconds() // 3600)
        n += _remind(
            session,
            [task.assignee_id, task.reporter_id],
            task,
            "review_stale",
            today,
            kind="task.remind",
            title=f"{task.key} chờ review {waited // 24} ngày {waited % 24} giờ",
            body=task.title,
        )
    return n


def _critical_daily(session: Session, space: TaskSpace, today: date) -> int:
    with_code = select(TaskGitLink.task_id).where(TaskGitLink.kind.in_(("commit", "mr")))
    tasks = list(
        session.scalars(
            _open_tasks(session, space.business_project_id).where(
                Task.id.in_(with_code), Task.assignee_id.is_not(None)
            )
        )
    )
    counts = _open_critical(session, [t.id for t in tasks])
    n = 0
    for task in tasks:
        if counts.get(task.id):
            n += _remind(
                session,
                [task.assignee_id],
                task,
                "critical",
                today,
                kind="task.alert",
                title=f"{task.key}: code còn {counts[task.id]} finding critical mở",
                body=task.title,
                tone="bad",
            )
    return n


# -- the parent asks to close its sub-tasks (parent_done_ask) -------------------------------------


def open_subtasks(session: Session, parent: Task) -> list[Task]:
    return list(
        session.scalars(
            select(Task)
            .join(TaskStatus, TaskStatus.id == Task.status_id)
            .where(
                Task.parent_id == parent.id, Task.type == "subtask", TaskStatus.category != "done"
            )
            .order_by(Task.number)
        )
    )


def done_status(session: Session, space: TaskSpace) -> TaskStatus | None:
    from app.tasks.service import statuses_of

    statuses = statuses_of(session, space.business_project_id)
    wanted = (space.rules or {}).get("done")
    return next((s for s in statuses if s.id == wanted and s.category == "done"), None) or next(
        (s for s in statuses if s.category == "done"), None
    )


# -- notification preferences (I) -----------------------------------------------------------------

PREFS_KEY = "task_notify:{}"
HELD_KEY = "task_notify_held:{}"
# The day of the last digest, apart from the preferences (the job never rewrites what the
# person saves, the person never erases the job's mark).
DIGEST_DAY_KEY = "task_notify_digest:{}"
MODES = ("immediate", "digest")
MUTABLE_KINDS = {
    "task.assigned": "Được giao task",
    "task.status": "Task đổi trạng thái",
    "task.commented": "Bình luận mới",
    "task.mentioned": "Được nhắc (@)",
    "task.remind": "Nhắc việc tự động",
    "task.alert": "Cảnh báo tự động",
}
DIGEST_KIND = "task.digest"
MAX_HELD = 300


def default_prefs() -> dict[str, Any]:
    return {"mode": "immediate", "quiet_hours": None, "kinds": []}


def prefs_of(session: Session, user_id: int) -> dict[str, Any]:
    row = session.get(AppSetting, PREFS_KEY.format(user_id))
    return {**default_prefs(), **(row.value if row is not None and row.value else {})}


def prefs_out(prefs: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": prefs["mode"],
        "quiet_hours": prefs["quiet_hours"],
        "kinds": prefs["kinds"],
        "kind_labels": MUTABLE_KINDS,
        "send_hour": SEND_HOUR,
    }


def set_prefs(session: Session, user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    prefs = prefs_of(session, user_id)
    if "mode" in body:
        if body["mode"] not in MODES:
            raise HTTPException(422, "mode là immediate hoặc digest")
        prefs["mode"] = body["mode"]
    if "quiet_hours" in body:
        quiet = body["quiet_hours"]
        if quiet is not None and not (
            isinstance(quiet, list)
            and len(quiet) == 2
            and all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23 for h in quiet)
            and quiet[0] != quiet[1]
        ):
            raise HTTPException(422, "giờ yên lặng là [giờ bắt đầu, giờ kết thúc], 0-23, khác nhau")
        prefs["quiet_hours"] = quiet
    if "kinds" in body:
        kinds = body["kinds"] or []
        bad = [k for k in kinds if k not in MUTABLE_KINDS]
        if bad:
            raise HTTPException(422, f"loại không tắt được: {bad}")
        prefs["kinds"] = sorted(set(kinds))
    key = PREFS_KEY.format(user_id)
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=prefs, set_by=user_id))
    else:
        row.value = dict(prefs)
    session.flush()
    return prefs


def in_quiet(quiet: list[int] | None, hour: int) -> bool:
    if not quiet:
        return False
    start, end = quiet
    return start <= hour < end if start < end else hour >= start or hour < end


def should_deliver(prefs: dict[str, Any], kind: str, local: datetime) -> str:
    """now | hold | drop, for one person's preferences at their local time."""
    if not kind.startswith("task.") or kind == DIGEST_KIND:
        return "now"
    if kind in prefs.get("kinds", []):
        return "drop"
    if prefs.get("mode") == "digest" or in_quiet(prefs.get("quiet_hours"), local.hour):
        return "hold"
    return "now"


def deliver_now(
    session: Session,
    user_ids: list[int],
    *,
    kind: str,
    title: str,
    body: str | None,
    link: str | None,
    tone: str,
    ref: str | None,
) -> list[int]:
    """Called by `inbox.notify` for task kinds: who gets it now; the others' are held or
    dropped by their preferences."""
    if not user_ids or not kind.startswith("task.") or kind == DIGEST_KIND:
        return user_ids
    keys = [PREFS_KEY.format(u) for u in user_ids]
    stored = {
        row.key: row.value
        for row in session.scalars(select(AppSetting).where(AppSetting.key.in_(keys)))
    }
    if not stored:
        return user_ids
    local = datetime.now(_tz())
    now: list[int] = []
    for user_id in user_ids:
        prefs = {**default_prefs(), **(stored.get(PREFS_KEY.format(user_id)) or {})}
        decision = should_deliver(prefs, kind, local)
        if decision == "now":
            now.append(user_id)
        elif decision == "hold":
            _hold(
                session,
                user_id,
                {
                    "kind": kind,
                    "title": title[:200],
                    "body": body[:500] if body else None,
                    "link": link,
                    "tone": tone,
                    "ref": ref,
                    "at": datetime.now(UTC).isoformat(),
                },
            )
    return now


def _hold(session: Session, user_id: int, item: dict[str, Any]) -> None:
    """Append atomically (two requests holding for one person never lose one)."""
    stmt = pg_insert(AppSetting).values(key=HELD_KEY.format(user_id), value=[item])
    kept = case(
        # Full: the oldest one goes (`jsonb - 0` drops the first element).
        (
            func.jsonb_array_length(AppSetting.value) >= MAX_HELD,
            AppSetting.value.op("-", return_type=JSONB)(0),
        ),
        else_=AppSetting.value,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[AppSetting.key],
        set_={"value": kept.op("||", return_type=JSONB)(stmt.excluded.value)},
    )
    session.execute(stmt)


def _digest_due(prefs: dict[str, Any], local: datetime, last_day: str | None) -> bool:
    if in_quiet(prefs.get("quiet_hours"), local.hour):
        return False
    if prefs.get("mode") != "digest":
        return True  # held for quiet hours only: out as soon as they end
    return local.hour >= SEND_HOUR and last_day != local.date().isoformat()


def _task_key(link: str | None) -> str | None:
    if link and "task=" in link:
        return link.split("task=", 1)[1].split("&", 1)[0]
    return None


def send_digests(session: Session, now: datetime | None = None) -> int:
    """Held notifications of each person whose digest is due: into the history (as read), with
    one unread summary. Returns the number of summaries."""
    from app.notifications.inbox import notify

    now = now or datetime.now(UTC)
    local = now.astimezone(_tz())
    sent = 0
    held_keys = list(
        session.scalars(
            select(AppSetting.key)
            .where(AppSetting.key.like("task_notify_held:%"))
            .order_by(AppSetting.key)
        )
    )
    for key in held_keys:
        user_id = int(key.rsplit(":", 1)[1])
        prefs = prefs_of(session, user_id)
        day_row = session.get(AppSetting, DIGEST_DAY_KEY.format(user_id))
        if not _digest_due(prefs, local, day_row.value if day_row is not None else None):
            continue
        # SKIP LOCKED: a request appending to this person's list right now is never waited for
        # (nor can the two wait on each other); the next tick takes it.
        row = session.get(
            AppSetting, key, with_for_update={"skip_locked": True}, populate_existing=True
        )
        if row is None:
            continue
        items: list[dict[str, Any]] = list(row.value or [])[-MAX_HELD:]
        session.delete(row)
        if prefs.get("mode") == "digest":
            today = local.date().isoformat()
            if day_row is None:
                session.add(AppSetting(key=DIGEST_DAY_KEY.format(user_id), value=today))
            else:
                day_row.value = today
        active = session.scalar(select(User.active).where(User.id == user_id))
        if not items or active is False:
            continue
        for item in items:
            at = _parse_at(item.get("at")) or now
            session.add(
                UserNotification(
                    user_id=user_id,
                    kind=str(item.get("kind") or "task.status"),
                    title=str(item.get("title") or "")[:200],
                    body=item.get("body"),
                    link=item.get("link"),
                    tone=str(item.get("tone") or "info"),
                    ref=item.get("ref"),
                    read_at=now,
                    created_at=at,
                )
            )
        per_task: dict[str, int] = defaultdict(int)
        for item in items:
            per_task[_task_key(item.get("link")) or "khác"] += 1
        ordered = sorted(per_task.items(), key=lambda kv: -kv[1])
        names = ", ".join(k for k, _ in ordered[:3]) + (" …" if len(ordered) > 3 else "")
        only = ordered[0][0] if len(ordered) == 1 and ordered[0][0] != "khác" else None
        notify(
            session,
            [user_id],
            kind=DIGEST_KIND,
            title=f"{len(items)} cập nhật trên {names}",
            body=" · ".join(f"{k}: {n}" for k, n in ordered[:8]),
            link=f"/tasks?task={only}" if only else "/notifications",
            tone="info",
            ref=f"digest:{local.date().isoformat()}",
        )
        sent += 1
    session.flush()
    return sent


def _parse_at(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
