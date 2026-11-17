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

**Round 2, stage 2.5 (docs/specs/31 §20.5).** A channel per task kind (`inbox | telegram |
off`; "telegram" also writes the inbox row, which keeps the history and what the Telegram
buttons act on), `task.alert` past quiet hours and digests, a daily summary at the person's
time, five more rule templates, and the rule log (`task_rule_runs`): every action of a rule writes
one row; a dry run (`dry_run`) runs a scheduled rule inside a SAVEPOINT that is rolled back and
keeps a single log row marked `dry_run` with what it would have done.

**Round 3, stage 3.2 (docs/specs/31 §21.2).** The settings page's event table (three more kinds:
`task.unblocked`, `task.review`, `task.test_failed`, the last two found on the tick by
`send_personal`), Telegram only where it is worth a ping (TELEGRAM_KINDS), quiet weekends
(`quiet_days`), the organisation's defaults (`task_notify_defaults`, for whoever never saved
their own), "việc ngày mai" in the summary and the Telegram "Bắt đầu" button.
"""

import html
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, case, event, func, inspect, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, SessionTransaction

from app.models import (
    Account,
    AppSetting,
    MergeRequest,
    ProjectGrant,
    Sprint,
    Task,
    TaskGitLink,
    TaskSpace,
    TaskStatus,
    TaskVersion,
    User,
    UserNotification,
)
from app.models.tasks import TaskLink, TaskRuleRun

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
    # Round 2 (§20.5). The two "event" ones are checked on the scheduler's tick (every 15
    # minutes), not inside the git / test result code paths.
    Template(
        "release_version",
        "MR merge vào nhánh release → gắn phiên bản",
        "MR của task merge vào nhánh release/<tên> hoặc release-<tên> trùng tên một phiên bản của "
        "dự án: task chưa có phiên bản được gắn phiên bản đó (mỗi task một lần).",
        "event",
    ),
    Template(
        "column_age_warn",
        "Task quá tuổi cột → báo quản lý",
        "Task nằm ở một cột lâu hơn mức cảnh báo tuổi của cột đó: báo quản lý dự án một lần "
        "(mỗi lần vào cột).",
        "schedule",
    ),
    Template(
        "test_failed_back",
        "Testcase Failed khi đang review → về Đang làm",
        "Testcase phủ task bị Failed trong lúc task ở trạng thái review: chuyển task về trạng "
        "thái bắt đầu của luật git và báo người làm (mỗi task tối đa một lần mỗi ngày).",
        "event",
    ),
    Template(
        "sprint_risk",
        "Sprint sắp hết mà còn nhiều điểm → báo quản lý",
        "Sprint đang chạy còn tối đa N ngày mà phần điểm chưa xong vượt ngưỡng: báo quản lý dự "
        "án (mỗi sprint một lần).",
        "schedule",
        (
            Param("days", "Còn (ngày)", 2, 0, 14, "ngày"),
            Param("percent", "Chưa xong trên", 40, 1, 99, "%"),
        ),
    ),
    Template(
        "unassigned_in_sprint",
        "Task không người làm trong sprint → báo quản lý",
        "Task mở trong sprint đang chạy chưa có người làm quá N ngày (tính từ lúc tạo): báo "
        "quản lý dự án (mỗi task một lần mỗi sprint).",
        "schedule",
        (Param("days", "Sau (ngày)", 3, 1, 30, "ngày"),),
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
            "can_dry_run": t.key in SCHEDULED,
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
        log_run(session, task.business_project_id, rule, task, "skipped", why=str(exc.detail))
    except Exception:
        log.exception("task rule failed", extra={"rule": rule, "task": task.key})
        log_run(session, task.business_project_id, rule, task, "failed")
    return False


# -- the rule log (§20.5) -------------------------------------------------------------------------

# What a dry run collects (None outside one), and the clock of the current run (the scheduler's
# `now`, so "once a day" is the same day for the log and the reminders).
_PLAN: ContextVar[list[dict[str, Any]] | None] = ContextVar("task_rule_plan", default=None)
_NOW: ContextVar[datetime | None] = ContextVar("task_rule_now", default=None)
LOG_MAX = 200
DRY_RUN_KEEP = 50


def _clock() -> datetime:
    return _NOW.get() or datetime.now(UTC)


def log_run(
    session: Session,
    bp_id: int,
    rule: str,
    task: Task | None,
    action: str,
    **detail: Any,
) -> None:
    """One row per action of a rule. Inside a dry run the row is rolled back with the rest; what
    it says is kept in the plan."""
    session.add(
        TaskRuleRun(
            business_project_id=bp_id,
            rule=rule,
            task_id=task.id if task is not None else None,
            action=action,
            detail=detail,
            dry_run=False,
            at=_clock(),
        )
    )
    plan = _PLAN.get()
    if plan is not None:
        plan.append(
            {"task_key": task.key if task is not None else None, "action": action, **detail}
        )


def _logged_tasks(
    session: Session,
    rule: str,
    task_ids: list[int],
    since: datetime | None = None,
    *,
    with_skips: bool = False,
) -> dict[int, datetime]:
    """Tasks a rule really acted on (not a dry run; a skip only `with_skips`), last time."""
    if not task_ids:
        return {}
    query = select(TaskRuleRun.task_id, func.max(TaskRuleRun.at)).where(
        TaskRuleRun.rule == rule,
        TaskRuleRun.task_id.in_(task_ids),
        TaskRuleRun.dry_run.is_(False),
    )
    if not with_skips:
        query = query.where(TaskRuleRun.action.not_in(("skipped", "failed")))
    if since is not None:
        query = query.where(TaskRuleRun.at >= since)
    return {
        int(task_id): at
        for task_id, at in session.execute(query.group_by(TaskRuleRun.task_id)).all()
        if task_id is not None
    }


def log_out(session: Session, bp_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = session.execute(
        select(TaskRuleRun, Task.key)
        .outerjoin(Task, Task.id == TaskRuleRun.task_id)
        .where(TaskRuleRun.business_project_id == bp_id)
        .order_by(TaskRuleRun.id.desc())
        .limit(max(1, min(limit, LOG_MAX)))
    ).all()
    return [
        {
            "id": run.id,
            "rule": run.rule,
            "rule_title": BY_KEY[run.rule].title if run.rule in BY_KEY else run.rule,
            "task_key": key,
            "action": run.action,
            "detail": run.detail or {},
            "dry_run": run.dry_run,
            "at": run.at.isoformat() if run.at else None,
        }
        for run, key in rows
    ]


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


# The short rule names in reminder refs (kept: refs already sent) -> their template.
REF_RULE = {
    "overdue": "overdue_remind",
    "due_soon": "due_soon_remind",
    "review_stale": "review_stale_remind",
    "critical": "critical_reassign",
}


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
    log_run(
        session,
        task.business_project_id,
        REF_RULE.get(rule, rule),
        task,
        "notify",
        users=sorted(targets),
        title=title,
    )
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
    log_run(session, task.business_project_id, "assign_on_start", task, "assign", user=user_id)


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
        log_run(
            session,
            parent.business_project_id,
            "subtasks_done_review",
            parent,
            "move",
            to=review.name,
            by=task.key,
        )


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
            log_run(
                session,
                blocked.business_project_id,
                "unblock_notify",
                blocked,
                "notify",
                users=[blocked.assignee_id],
                by=task.key,
            )
            notify(
                session,
                [blocked.assignee_id],
                kind="task.unblocked",  # §21.2: its own event (was task.alert)
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
    told = managers(session, task.business_project_id)
    log_run(
        session,
        task.business_project_id,
        "bug_highest_notify",
        task,
        "notify",
        users=sorted(told),
    )
    notify(
        session,
        told,
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


def _enqueue_notes_redis(note_ids: list[int]) -> None:
    from rq import Queue

    from app.db import get_redis

    Queue("default", connection=get_redis()).enqueue(
        "app.workers.task_automation_jobs.telegram_note_job", note_ids
    )


# Swapped in tests, like telegram_enqueuer.
telegram_note_enqueuer: Any = _enqueue_notes_redis
TG_NOTES = "task_notify_telegram"


def queue_telegram(session: Session, rows: Iterable[UserNotification]) -> None:
    """Inbox rows whose person chose Telegram: sent by a worker once the event commits."""
    session.info.setdefault(TG_NOTES, []).extend(rows)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    task_ids: list[int] = session.info.pop(TG_PENDING, [])
    notes: list[UserNotification] = session.info.pop(TG_NOTES, [])
    # A row added in a SAVEPOINT that was rolled back (a dry run) is not sent.
    note_ids = sorted({row.id for row in notes if inspect(row).persistent})
    if task_ids:
        try:
            telegram_enqueuer(task_ids)
        except Exception:
            log.warning("telegram task alert not queued", exc_info=True)
    if note_ids:
        try:
            telegram_note_enqueuer(note_ids)
        except Exception:
            log.warning("telegram task notes not queued", exc_info=True)


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop(TG_PENDING, None)
        session.info.pop(TG_NOTES, None)


# -- Telegram: a person's own chat (§20.5) --------------------------------------------------------

NOTE_CALLBACK = "nt"  # callback data "nt|<notification id>|r" (seen) or "|s" (snooze a day)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def telegram_chats_of(session: Session, user_id: int) -> list[int]:
    """The private chats of a person's linked Telegram accounts (a user id is its private chat
    id); linked with /link CODE (the `telegram.self` page)."""
    from app.telegram.service import TELEGRAM_KIND

    out: list[int] = []
    for key in session.scalars(
        select(Account.external_key).where(
            Account.kind == TELEGRAM_KIND, Account.user_id == user_id
        )
    ):
        try:
            out.append(int(key))
        except (TypeError, ValueError):
            continue
    return out


def telegram_linked(session: Session, user_id: int) -> bool:
    return bool(telegram_chats_of(session, user_id))


def note_bots(session: Session, bp_id: int | None) -> list[int]:
    """Enabled bots to send a person's note with: the task's dự án first. A bot can only write
    to someone who has started it, so the job tries them in order."""
    from app.models import TelegramBot

    bots = list(session.scalars(select(TelegramBot).where(TelegramBot.enabled.is_(True))))
    bots.sort(key=lambda b: (b.business_project_id != bp_id, b.id))
    return [b.id for b in bots]


def _url_ok(url: str) -> bool:
    """Telegram refuses URL buttons that are not public http(s) links."""
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    host = lowered.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    return host not in LOCAL_HOSTS and "." in host


def telegram_note(
    note: UserNotification, base_url: str, start: bool = False
) -> tuple[str, dict[str, Any]]:
    """The message and its buttons: Mở (a URL, when the dashboard has a public address), Đã xem
    and Ẩn 1 ngày (callbacks, answered by the bot poller, which polls every enabled bot), and
    Bắt đầu (§21.2) when `start` (`can_start`: the task waits in a "todo" status)."""
    url = base_url.rstrip("/") + (note.link or "/notifications")
    text = f"<b>{html.escape(note.title)}</b>"
    if note.body:
        text += f"\n{html.escape(note.body)}"
    rows: list[list[dict[str, str]]] = []
    if _url_ok(url):
        rows.append([{"text": "Mở", "url": url}])
    else:
        text += f"\n{html.escape(url)}"
    if start:
        rows.append([{"text": "Bắt đầu", "callback_data": f"{NOTE_CALLBACK}|{note.id}|b"}])
    rows.append(
        [
            {"text": "Đã xem", "callback_data": f"{NOTE_CALLBACK}|{note.id}|r"},
            {"text": "Ẩn 1 ngày", "callback_data": f"{NOTE_CALLBACK}|{note.id}|s"},
        ]
    )
    return text, {"inline_keyboard": rows}


NO_START_KINDS = frozenset({"task.digest", "task.summary", "task.review"})


def _start_target(session: Session, note: UserNotification) -> tuple[Task, TaskStatus] | None:
    """The note's task and its space's "start" status (git rules), when the task still waits in
    a "todo" status."""
    from app.tasks.service import rules_of

    key = _task_key(note.link)
    if note.kind in NO_START_KINDS or not key:
        return None
    task = session.scalar(select(Task).where(Task.key == key))
    space = session.get(TaskSpace, task.business_project_id) if task is not None else None
    if task is None or space is None:
        return None
    start_id = rules_of(space).get("start")
    start = _status_of(session, start_id) if isinstance(start_id, int) else None
    current = _status_of(session, task.status_id)
    if start is None or start.business_project_id != task.business_project_id:
        return None
    if current is None or current.category != "todo" or current.id == start.id:
        return None
    return task, start


def can_start(session: Session, note: UserNotification) -> bool:
    return _start_target(session, note) is not None


def _start_from_telegram(session: Session, user_id: int, note: UserNotification) -> str:
    """ "Bắt đầu": the task to its space's start status, if the person may move it (workflow
    transitions apply, as on the board)."""
    from app import usage
    from app.api.security import load_principal
    from app.config import get_settings
    from app.tasks.service import may, set_status

    key = _task_key(note.link) or "task"
    target = _start_target(session, note)
    if target is None:
        task = session.scalar(select(Task).where(Task.key == key))
        status = _status_of(session, task.status_id) if task is not None else None
        return f"{key} đang ở {status.name}." if status else "Task không còn."
    task, start = target
    try:
        principal = load_principal(session, user_id, get_settings())
    except HTTPException:
        return "Tài khoản của bạn đang tắt."
    if not principal.can("tasks.view") or not may(principal, "transition", task):
        return f"Bạn không có quyền chuyển trạng thái {task.key}."
    try:
        with usage.surface("telegram"), session.begin_nested():
            set_status(session, task, start, user_id, "web")
    except HTTPException as exc:
        return f"Chưa chuyển được {task.key}: {exc.detail}"
    note.read_at = note.read_at or datetime.now(UTC)
    return f"{task.key} → {start.name}."


def note_action(session: Session, from_id: int, data: str, now: datetime | None = None) -> str:
    """A tap on Đã xem / Ẩn 1 ngày: only on a notification of the person linked to this
    Telegram account. Returns the text of the answer."""
    from app.notifications.inbox import SNOOZE
    from app.telegram.service import TELEGRAM_KIND

    parts = data.split("|")
    if len(parts) != 3 or parts[0] != NOTE_CALLBACK or not parts[1].isdigit():
        return "Nút này không còn hiệu lực."
    user_id = session.scalar(
        select(Account.user_id).where(
            Account.kind == TELEGRAM_KIND, Account.external_key == str(from_id)
        )
    )
    note = session.get(UserNotification, int(parts[1]))
    if user_id is None or note is None or note.user_id != user_id:
        return "Thông báo không còn hoặc không phải của bạn."
    now = now or datetime.now(UTC)
    if parts[2] == "r":
        note.read_at = note.read_at or now
        return "Đã đánh dấu đã xem."
    if parts[2] == "s":
        note.snoozed_until = now + SNOOZE
        return f"Đã ẩn tới {note.snoozed_until.astimezone(_tz()):%H:%M %d/%m}."
    if parts[2] == "b":
        return _start_from_telegram(session, user_id, note)
    return "Nút này không còn hiệu lực."


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


@dataclass(frozen=True)
class Tick:
    now: datetime
    today: date
    config: dict[str, dict[str, Any]]


# The rules the scheduler's tick runs (and a dry run can run): template -> fn. Those in ANYTIME
# run on every tick; the others (reminders) from SEND_HOUR.
SCHEDULED: dict[str, Callable[[Session, TaskSpace, Tick], int]] = {
    "overdue_remind": lambda s, sp, t: _overdue(s, sp, t.today),
    "due_soon_remind": lambda s, sp, t: _due_soon(
        s, sp, t.today, t.config["due_soon_remind"]["days"]
    ),
    "review_stale_remind": lambda s, sp, t: _review_stale(
        s, sp, t.now, t.today, t.config["review_stale_remind"]["hours"]
    ),
    "critical_reassign": lambda s, sp, t: _critical_daily(s, sp, t.today),
    "release_version": lambda s, sp, t: _release_version(s, sp, t),
    "column_age_warn": lambda s, sp, t: _column_age(s, sp, t),
    "test_failed_back": lambda s, sp, t: _test_failed_back(s, sp, t),
    "sprint_risk": lambda s, sp, t: _sprint_risk(s, sp, t),
    "unassigned_in_sprint": lambda s, sp, t: _unassigned(s, sp, t),
}
ANYTIME = frozenset({"release_version", "test_failed_back"})
# The counters run_schedule returns (the first four kept their names).
COUNTER = {v: k for k, v in REF_RULE.items()}


def run_schedule(session: Session, now: datetime | None = None) -> dict[str, int]:
    """The scheduled rules of every space that has one on; reminders go out from SEND_HOUR."""
    now = now or datetime.now(UTC)
    local = now.astimezone(_tz())
    sent: Counter[str] = Counter()
    morning = local.hour >= SEND_HOUR
    tick_now = _NOW.set(now)
    try:
        for space in session.scalars(select(TaskSpace)):
            if not (space.rules or {}).get("automation"):
                continue
            config = config_of(space)
            tick = Tick(now, local.date(), config)
            mine: Counter[str] = Counter()
            try:
                # One space failing (a bad row) neither stops nor rolls back the others.
                with _acting(), session.begin_nested():
                    for key, fn in SCHEDULED.items():
                        if config[key]["on"] and (morning or key in ANYTIME):
                            mine[COUNTER.get(key, key)] += fn(session, space, tick)
                sent.update(mine)
            except Exception:
                log.exception("task schedule failed", extra={"space": space.key})
    finally:
        _NOW.reset(tick_now)
    return dict(sent)


def dry_run(
    session: Session, space: TaskSpace, rule: str, now: datetime | None = None
) -> list[dict[str, Any]]:
    """What a scheduled rule would do now (on or off, at any hour): it runs inside a SAVEPOINT
    that is always rolled back; one log row marked `dry_run` keeps the plan."""
    fn = SCHEDULED.get(rule)
    if fn is None:
        if rule in BY_KEY:
            raise HTTPException(409, "quy tắc này chạy khi thao tác, không chạy thử được")
        raise HTTPException(404, f"quy tắc không biết: {rule}")
    now = now or datetime.now(UTC)
    tick = Tick(now, now.astimezone(_tz()).date(), config_of(space))
    plan: list[dict[str, Any]] = []
    tokens = (_PLAN.set(plan), _NOW.set(now))
    from app.notifications.inbox import PENDING

    session.flush()
    # What the run queues for after the commit (live pushes, Telegram) is dropped with it.
    marks = {k: len(session.info.get(k, [])) for k in (PENDING, TG_NOTES, TG_PENDING)}
    nested = session.begin_nested()
    try:
        with _acting():
            fn(session, space, tick)
    finally:
        _PLAN.reset(tokens[0])
        _NOW.reset(tokens[1])
        nested.rollback()
        for key, n in marks.items():
            if key in session.info:
                del session.info[key][n:]
    session.add(
        TaskRuleRun(
            business_project_id=space.business_project_id,
            rule=rule,
            task_id=None,
            action="dry_run",
            detail={"count": len(plan), "items": plan[:DRY_RUN_KEEP]},
            dry_run=True,
            at=now,
        )
    )
    session.flush()
    return plan


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


# -- round 2 rules (§20.5) ------------------------------------------------------------------------

# release/<name> or release-<name>; <name> is matched to a version of the dự án (case ignored).
RELEASE_BRANCH = re.compile(r"^release[/-](.+)$", re.IGNORECASE)
RELEASE_LOOKBACK = timedelta(days=14)


def release_name(branch: str | None) -> str | None:
    match = RELEASE_BRANCH.match((branch or "").strip())
    return match.group(1).strip() if match else None


def _tell_managers(
    session: Session,
    space: TaskSpace,
    rule: str,
    tasks: list[Task],
    *,
    title_one: str,
    title_many: str,
    body: str | None = None,
    ref: str,
    tone: str = "warn",
) -> list[int]:
    """One note to the dự án's managers about one or more tasks; [] when there is nobody to
    tell (nothing is logged then, so the rule tells them once someone manages the dự án)."""
    from app.notifications.inbox import notify
    from app.tasks.service import link_of

    told = managers(session, space.business_project_id)
    if not told:
        return []
    keys = [t.key for t in tasks]
    notify(
        session,
        told,
        kind="task.remind",
        title=title_one if len(tasks) == 1 else title_many,
        body=body or (", ".join(keys[:8]) + (" …" if len(keys) > 8 else "")),
        link=link_of(keys[0]) if len(tasks) == 1 else "/tasks",
        tone=tone,
        ref=ref,
    )
    return told


def _release_version(session: Session, space: TaskSpace, tick: Tick) -> int:
    from app.tasks.service import log as history

    bp = space.business_project_id
    found = session.execute(
        select(Task, MergeRequest.target_branch, MergeRequest.iid)
        .join(TaskGitLink, TaskGitLink.task_id == Task.id)
        .join(
            MergeRequest,
            and_(
                MergeRequest.project_id == TaskGitLink.project_id,
                MergeRequest.iid == TaskGitLink.mr_iid,
            ),
        )
        .where(
            Task.business_project_id == bp,
            Task.version_id.is_(None),
            TaskGitLink.kind == "mr",
            MergeRequest.state == "merged",
            MergeRequest.merged_at >= tick.now - RELEASE_LOOKBACK,
            or_(
                MergeRequest.target_branch.ilike("release/%"),
                MergeRequest.target_branch.ilike("release-%"),
            ),
        )
        .order_by(MergeRequest.merged_at, Task.id)
    ).all()
    if not found:
        return 0
    versions = {
        v.name.strip().lower(): v
        for v in session.scalars(select(TaskVersion).where(TaskVersion.business_project_id == bp))
    }
    done = _logged_tasks(session, "release_version", [t.id for t, _, _ in found])
    n = 0
    for task, branch, iid in found:
        name = release_name(branch)
        version = versions.get(name.lower()) if name else None
        if version is None or task.id in done or task.version_id is not None:
            continue
        task.version_id = version.id
        history(session, task, None, "version", None, version.name)
        if task.type != "subtask":  # sub-tasks go with their parent, as on a person's change
            for child in session.scalars(
                select(Task).where(Task.parent_id == task.id, Task.type == "subtask")
            ):
                child.version_id = version.id
        log_run(
            session,
            bp,
            "release_version",
            task,
            "set_version",
            version=version.name,
            branch=branch,
            mr=iid,
        )
        done[task.id] = tick.now
        n += 1
    return n


def _column_age(session: Session, space: TaskSpace, tick: Tick) -> int:
    from app.tasks.service import statuses_of

    watched = [
        s
        for s in statuses_of(session, space.business_project_id)
        if s.age_warning_hours and s.category != "done"
    ]
    old: list[tuple[Task, TaskStatus]] = []
    for status in watched:
        for task in session.scalars(
            select(Task)
            .where(
                Task.status_id == status.id,
                Task.status_changed_at < tick.now - timedelta(hours=status.age_warning_hours or 0),
            )
            .order_by(Task.status_changed_at, Task.id)
        ):
            old.append((task, status))
    if not old:
        return 0
    told = _logged_tasks(session, "column_age_warn", [t.id for t, _ in old])
    # Once per stay in the column: told after the task entered it -> already told.
    new = [
        (t, s)
        for t, s in old
        if not (t.id in told and t.status_changed_at and told[t.id] >= t.status_changed_at)
    ]
    if not new:
        return 0
    first, status = new[0]
    who = _tell_managers(
        session,
        space,
        "column_age_warn",
        [t for t, _ in new],
        title_one=f"{first.key} nằm ở {status.name} quá {status.age_warning_hours} giờ",
        title_many=f"{len(new)} task quá tuổi cột",
        body=first.title if len(new) == 1 else None,
        ref=f"auto:column_age_warn:{space.business_project_id}:{tick.now.isoformat()}",
    )
    if not who:
        return 0
    for task, st in new:
        log_run(
            session,
            space.business_project_id,
            "column_age_warn",
            task,
            "notify",
            users=who,
            status=st.name,
            hours=st.age_warning_hours,
        )
    return len(new)


def _test_failed_back(session: Session, space: TaskSpace, tick: Tick) -> int:
    from app.notifications.inbox import notify
    from app.tasks import quality
    from app.tasks.service import link_of, rules_of, set_status
    from app.testcases import content

    review = _review_status(session, space)
    start_id = rules_of(space).get("start")
    start = _status_of(session, start_id) if isinstance(start_id, int) else None
    if review is None or start is None or start.business_project_id != space.business_project_id:
        return 0
    tasks = list(session.scalars(select(Task).where(Task.status_id == review.id).order_by(Task.id)))
    cover = quality.covering(session, [t.id for t in tasks])
    tests = quality.latest_tests(session, sorted({c for cs in cover.values() for c in cs}))
    midnight = datetime.combine(tick.today, time.min, _tz()).astimezone(UTC)
    # A move the workflow refused counts too: tried once a day, not on every tick.
    today_done = _logged_tasks(
        session, "test_failed_back", [t.id for t in tasks], midnight, with_skips=True
    )
    n = 0
    for task in tasks:
        since = task.status_changed_at
        failed = []
        for case_id in cover.get(task.id, []):
            test = tests.get(case_id)
            at = test.tested_at if test is not None else None
            # Failed while the task sat in review: recorded after it entered the status.
            if (
                test is not None
                and test.status == "failed"
                and at is not None
                and (since is None or at >= since)
            ):
                failed.append(test)
        if not failed or task.id in today_done:
            continue
        cases = [k for t in failed[:3] if (k := content.key("case", t.case_id))]
        try:
            with session.begin_nested():
                set_status(session, task, start, None, "web")
        except HTTPException as exc:
            log_run(
                session,
                space.business_project_id,
                "test_failed_back",
                task,
                "skipped",
                why=str(exc.detail),
            )
            continue
        except Exception:
            log.exception("test_failed_back failed", extra={"task": task.key})
            log_run(session, space.business_project_id, "test_failed_back", task, "failed")
            continue
        log_run(
            session,
            space.business_project_id,
            "test_failed_back",
            task,
            "move",
            to=start.name,
            cases=cases,
        )
        if task.assignee_id is not None:
            notify(
                session,
                [task.assignee_id],
                kind="task.alert",
                title=f"{task.key} về {start.name}: testcase Failed khi đang review",
                body=", ".join(cases) or task.title,
                link=link_of(task.key),
                tone="bad",
                ref=f"auto:test_failed_back:{task.id}:{tick.today.isoformat()}",
            )
        n += 1
    return n


def _active_sprints(session: Session, bp_id: int) -> list[Sprint]:
    return list(
        session.scalars(
            select(Sprint).where(Sprint.business_project_id == bp_id, Sprint.state == "active")
        )
    )


def _sprint_risk(session: Session, space: TaskSpace, tick: Tick) -> int:
    from app.tasks import reports

    params = tick.config["sprint_risk"]
    n = 0
    for sprint in _active_sprints(session, space.business_project_id):
        if (
            sprint.end_date is None
            or not 0 <= (sprint.end_date - tick.today).days <= params["days"]
        ):
            continue
        rows = session.execute(
            select(Task, TaskStatus.category)
            .join(TaskStatus, TaskStatus.id == Task.status_id)
            .where(Task.sprint_id == sprint.id)
        ).all()
        total = sum(reports.points(t) for t, _ in rows)
        left = sum(reports.points(t) for t, cat in rows if cat != "done")
        if total <= 0 or left * 100 <= total * params["percent"]:
            continue
        told_before = session.scalar(
            select(TaskRuleRun.id)
            .where(
                TaskRuleRun.business_project_id == space.business_project_id,
                TaskRuleRun.rule == "sprint_risk",
                TaskRuleRun.dry_run.is_(False),
                TaskRuleRun.action == "notify",
                TaskRuleRun.detail["sprint_id"].as_integer() == sprint.id,
            )
            .limit(1)
        )
        if told_before is not None:
            continue
        days = (sprint.end_date - tick.today).days
        share = round(left * 100 / total)
        when = "hôm nay" if days == 0 else f"sau {days} ngày"
        from app.notifications.inbox import notify

        told = managers(session, space.business_project_id)
        if not told:
            continue
        notify(
            session,
            told,
            kind="task.remind",
            title=f"Sprint {sprint.name} kết thúc {when}, còn {share}% điểm chưa xong",
            body=f"{left:g} / {total:g} điểm chưa xong",
            link="/tasks",
            tone="warn",
            ref=f"auto:sprint_risk:{sprint.id}",
        )
        log_run(
            session,
            space.business_project_id,
            "sprint_risk",
            None,
            "notify",
            users=told,
            sprint_id=sprint.id,
            sprint=sprint.name,
            left=left,
            total=total,
        )
        n += 1
    return n


def _unassigned(session: Session, space: TaskSpace, tick: Tick) -> int:
    days = tick.config["unassigned_in_sprint"]["days"]
    n = 0
    for sprint in _active_sprints(session, space.business_project_id):
        tasks = list(
            session.scalars(
                _open_tasks(session, space.business_project_id)
                .where(
                    Task.sprint_id == sprint.id,
                    Task.assignee_id.is_(None),
                    Task.created_at < tick.now - timedelta(days=days),
                )
                .order_by(Task.rank, Task.id)
            )
        )
        if not tasks:
            continue
        told_before = set(
            session.scalars(
                select(TaskRuleRun.task_id).where(
                    TaskRuleRun.rule == "unassigned_in_sprint",
                    TaskRuleRun.task_id.in_([t.id for t in tasks]),
                    TaskRuleRun.dry_run.is_(False),
                    TaskRuleRun.action == "notify",
                    TaskRuleRun.detail["sprint_id"].as_integer() == sprint.id,
                )
            )
        )
        new = [t for t in tasks if t.id not in told_before]
        if not new:
            continue
        who = _tell_managers(
            session,
            space,
            "unassigned_in_sprint",
            new,
            title_one=f"{new[0].key} chưa có người làm sau {days} ngày trong {sprint.name}",
            title_many=f"{len(new)} task chưa có người làm trong {sprint.name}",
            body=new[0].title if len(new) == 1 else None,
            ref=f"auto:unassigned_in_sprint:{sprint.id}:{tick.now.isoformat()}",
        )
        if not who:
            continue
        for task in new:
            log_run(
                session,
                space.business_project_id,
                "unassigned_in_sprint",
                task,
                "notify",
                users=who,
                sprint_id=sprint.id,
            )
        n += len(new)
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
# The events of the "Cài đặt thông báo" page (§21.2), in its order. Round 3 adds three kinds:
# unblocked (was task.alert), an MR asking me to review, a covering testcase Failed on my task.
MUTABLE_KINDS = {
    "task.assigned": "Được giao task",
    "task.mentioned": "Được nhắc tên (@)",
    "task.status": "Task mình theo dõi đổi trạng thái",
    "task.commented": "Bình luận trên task của mình",
    "task.unblocked": "Hết bị chặn",
    "task.remind": "Nhắc quá hạn / sắp tới hạn",
    "task.review": "MR chờ mình review",
    "task.test_failed": "Testcase Failed trên task của mình",
    "task.alert": "Cảnh báo khẩn (Bug Cao nhất, code critical)",
}
# Telegram where a ping is worth it; status changes and comments stay in the inbox (§21.2).
TELEGRAM_KINDS = frozenset(MUTABLE_KINDS) - {"task.status", "task.commented"}
DIGEST_KIND = "task.digest"
# The daily summary (§20.5): always delivered (its own time is the person's choice).
SUMMARY_KIND = "task.summary"
ALWAYS_NOW = frozenset({DIGEST_KIND, SUMMARY_KIND})
# Past quiet hours (weekends too) and the digest: a "highest" bug, code with open critical
# findings, ... and production incidents (§21.2); "off" still mutes them.
URGENT_KIND = "task.alert"
URGENT_KINDS = frozenset({URGENT_KIND, "task.incident"})
CHANNELS = ("inbox", "telegram", "off")
SUMMARY_KEY = "task_notify_summary:{}"  # the local day of the last summary, apart from prefs
SUMMARY_TIME = "17:30"
MAX_HELD = 300
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
# The organisation's defaults (§21.2): what a person who never saved their own gets.
ORG_KEY = "task_notify_defaults"
WEEKEND = (5, 6)  # Python weekday(): Saturday, Sunday


def default_summary() -> dict[str, Any]:
    return {"on": False, "time": SUMMARY_TIME, "channel": "inbox"}


def default_prefs() -> dict[str, Any]:
    return {
        "mode": "immediate",
        "quiet_hours": None,
        "quiet_days": [],
        "kinds": [],
        "channels": {},
        "summary": default_summary(),
    }


def _merge(value: dict[str, Any] | None) -> dict[str, Any]:
    prefs = {**default_prefs(), **(value or {})}
    prefs["summary"] = {**default_summary(), **(prefs.get("summary") or {})}
    prefs["channels"] = dict(prefs.get("channels") or {})
    prefs["quiet_days"] = [d for d in prefs.get("quiet_days") or [] if d in WEEKEND]
    return prefs


def org_defaults(session: Session) -> dict[str, Any] | None:
    row = session.get(AppSetting, ORG_KEY)
    return row.value if row is not None and isinstance(row.value, dict) else None


def prefs_of(session: Session, user_id: int) -> dict[str, Any]:
    """The person's own, else the organisation's defaults, else the built-in ones."""
    row = session.get(AppSetting, PREFS_KEY.format(user_id))
    if row is not None and row.value:
        return _merge(row.value)
    return _merge(org_defaults(session))


def customised(session: Session, user_id: int) -> bool:
    row = session.get(AppSetting, PREFS_KEY.format(user_id))
    return row is not None and bool(row.value)


def channel_of(prefs: dict[str, Any], kind: str) -> str:
    """inbox | telegram | off for one task kind ("off" is the muted list of §19.7)."""
    if kind in prefs.get("kinds", []):
        return "off"
    chosen = (prefs.get("channels") or {}).get(kind)
    return "telegram" if chosen == "telegram" and kind in TELEGRAM_KINDS else "inbox"


def prefs_out(prefs: dict[str, Any], linked: bool = False) -> dict[str, Any]:
    return {
        "mode": prefs["mode"],
        "quiet_hours": prefs["quiet_hours"],
        "quiet_days": prefs["quiet_days"],
        "kinds": prefs["kinds"],
        "kind_labels": MUTABLE_KINDS,
        "telegram_kinds": sorted(TELEGRAM_KINDS),
        "send_hour": SEND_HOUR,
        "channels": {k: channel_of(prefs, k) for k in MUTABLE_KINDS},
        "urgent_kind": URGENT_KIND,
        "summary": prefs["summary"],
        "telegram_linked": linked,
    }


def set_prefs(session: Session, user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    prefs = prefs_of(session, user_id)
    linked: bool | None = None

    def need_telegram() -> None:
        nonlocal linked
        if linked is None:
            linked = telegram_linked(session, user_id)
        if not linked:
            raise HTTPException(422, "chưa liên kết Telegram (trang Của tôi → Telegram)")

    _apply(prefs, body, need_telegram)
    key = PREFS_KEY.format(user_id)
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=prefs, set_by=user_id))
    else:
        row.value = dict(prefs)
    session.flush()
    return prefs


def reset_prefs(session: Session, user_id: int) -> dict[str, Any]:
    """Back to the organisation's defaults (the person's own row goes)."""
    row = session.get(AppSetting, PREFS_KEY.format(user_id))
    if row is not None:
        session.delete(row)
        session.flush()
    return prefs_of(session, user_id)


def set_org_defaults(session: Session, user_id: int, body: dict[str, Any]) -> dict[str, Any]:
    """Admins: the defaults of everyone who has not saved their own. Telegram may be chosen
    (it reaches those who linked an account; the others keep the inbox row)."""
    prefs = _merge(org_defaults(session))
    _apply(prefs, body, lambda: None)
    row = session.get(AppSetting, ORG_KEY)
    if row is None:
        session.add(AppSetting(key=ORG_KEY, value=prefs, set_by=user_id))
    else:
        row.value = dict(prefs)
        row.set_by = user_id
    session.flush()
    return prefs


def _apply(prefs: dict[str, Any], body: dict[str, Any], need_telegram: Callable[[], None]) -> None:
    if "quiet_days" in body:
        days = body["quiet_days"] or []
        if not isinstance(days, list) or any(d not in WEEKEND for d in days):
            raise HTTPException(422, "quiet_days chỉ gồm 5 (thứ Bảy) và 6 (Chủ nhật)")
        prefs["quiet_days"] = sorted(set(days))
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
    if body.get("channels") is not None:
        channels = body["channels"]
        if not isinstance(channels, dict):
            raise HTTPException(422, "channels là {loại: inbox | telegram | off}")
        kinds_set = set(prefs["kinds"])
        stored = dict(prefs["channels"])
        for kind, value in channels.items():
            if kind not in MUTABLE_KINDS or value not in CHANNELS:
                raise HTTPException(422, f"kênh không hợp lệ: {kind} = {value}")
            if value == "telegram":
                if kind not in TELEGRAM_KINDS:
                    raise HTTPException(422, f"{kind} chỉ nhận trong hộp thư (hoặc tắt)")
                need_telegram()
            if value == "off":
                kinds_set.add(kind)
            else:
                kinds_set.discard(kind)
                stored[kind] = value
        prefs["kinds"] = sorted(kinds_set)
        prefs["channels"] = stored
    if body.get("summary") is not None:
        summary = {**prefs["summary"], **body["summary"]}
        if not isinstance(summary.get("on"), bool):
            raise HTTPException(422, "summary.on phải là true / false")
        if not isinstance(summary.get("time"), str) or not TIME_RE.match(summary["time"]):
            raise HTTPException(422, "summary.time là HH:MM")
        if summary.get("channel") not in ("inbox", "telegram"):
            raise HTTPException(422, "summary.channel là inbox hoặc telegram")
        if summary["channel"] == "telegram" and summary["on"]:
            need_telegram()
        prefs["summary"] = {k: summary[k] for k in ("on", "time", "channel")}


def in_quiet(quiet: list[int] | None, hour: int) -> bool:
    if not quiet:
        return False
    start, end = quiet
    return start <= hour < end if start < end else hour >= start or hour < end


def quiet_now(prefs: dict[str, Any], local: datetime) -> bool:
    """Within the quiet hours, or on a quiet day (Saturday / Sunday, all day)."""
    if local.weekday() in (prefs.get("quiet_days") or []):
        return True
    return in_quiet(prefs.get("quiet_hours"), local.hour)


def should_deliver(prefs: dict[str, Any], kind: str, local: datetime) -> str:
    """now | hold | drop, for one person's preferences at their local time."""
    if not kind.startswith("task.") or kind in ALWAYS_NOW:
        return "now"
    if kind in prefs.get("kinds", []):
        return "drop"
    if kind in URGENT_KINDS:
        return "now"
    if prefs.get("mode") == "digest" or quiet_now(prefs, local):
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
) -> dict[int, str]:
    """Called by `inbox.notify` for task kinds: who gets it now, with the channel (inbox |
    telegram); the others' are held or dropped by their preferences."""
    if not user_ids or not kind.startswith("task.") or kind in ALWAYS_NOW:
        return {u: "inbox" for u in user_ids}
    keys = [PREFS_KEY.format(u) for u in user_ids]
    stored = {
        row.key: row.value
        for row in session.scalars(select(AppSetting).where(AppSetting.key.in_(keys)))
    }
    defaults = org_defaults(session) if len(stored) < len(user_ids) else None
    if not stored and not defaults:
        return {u: "inbox" for u in user_ids}
    local = datetime.now(_tz())
    now: dict[int, str] = {}
    for user_id in user_ids:
        prefs = _merge(stored.get(PREFS_KEY.format(user_id)) or defaults)
        decision = should_deliver(prefs, kind, local)
        if decision == "now":
            now[user_id] = channel_of(prefs, kind)
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
    if quiet_now(prefs, local):
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


# -- the daily summary (§20.5) --------------------------------------------------------------------


def summary_content(session: Session, user_id: int, local: datetime) -> dict[str, Any] | None:
    """Done today, still overdue, MRs waiting for my review (Việc của tôi, app/tasks/mywork.py);
    None when the person cannot see tasks or there is nothing to say."""
    from app.api.security import load_principal
    from app.config import get_settings
    from app.tasks.mywork import _visible_spaces, my_work

    try:
        principal = load_principal(session, user_id, get_settings())
    except HTTPException:
        return None
    if not principal.can("tasks.view"):
        return None
    tz = local.tzinfo if isinstance(local.tzinfo, ZoneInfo) else _tz()
    work = my_work(session, principal, tz)
    overdue = [a["task"]["key"] for a in work["attention"] if a["kind"] == "overdue"]
    reviews = [
        f"{str(a['mr']['project']).rsplit('/', 1)[-1]} !{a['mr']['iid']}"
        for a in work["attention"]
        if a["kind"] == "review"
    ]
    midnight = datetime.combine(local.date(), time.min, tz)
    spaces = _visible_spaces(session, principal)
    done = list(
        session.scalars(
            select(Task.key)
            .join(TaskStatus, TaskStatus.id == Task.status_id)
            .where(
                Task.assignee_id == user_id,
                Task.business_project_id.in_(spaces),
                TaskStatus.category == "done",
                Task.resolved_at >= midnight,
                or_(Task.resolution.is_(None), Task.resolution == "done"),
            )
            .order_by(Task.resolved_at)
        )
    )
    # §21.2 "việc ngày mai": my open tasks due tomorrow.
    tomorrow = list(
        session.scalars(
            select(Task.key)
            .join(TaskStatus, TaskStatus.id == Task.status_id)
            .where(
                Task.assignee_id == user_id,
                Task.business_project_id.in_(spaces),
                TaskStatus.category != "done",
                Task.due_date == local.date() + timedelta(days=1),
            )
            .order_by(Task.id)
        )
    )
    if not (done or overdue or reviews or tomorrow):
        return None
    return {"done": done, "overdue": overdue, "reviews": reviews, "tomorrow": tomorrow}


def _keys(items: list[str], limit: int = 6) -> str:
    return ", ".join(items[:limit]) + (" …" if len(items) > limit else "")


def send_summaries(session: Session, now: datetime | None = None) -> int:
    """Each person who turned the summary on gets it once a day, from their time (the first
    tick at or after it), in the inbox and, when chosen, on Telegram."""
    now = now or datetime.now(UTC)
    local = now.astimezone(_tz())
    today = local.date().isoformat()
    sent = 0
    people: dict[int, dict[str, Any]] = {}
    for row in list(
        session.scalars(select(AppSetting).where(AppSetting.key.like("task_notify:%")))
    ):
        people[int(row.key.rsplit(":", 1)[1])] = _merge(
            row.value if isinstance(row.value, dict) else None
        )
    defaults = org_defaults(session)
    if defaults and _merge(defaults)["summary"].get("on"):
        # §21.2: the organisation turned the summary on; everyone active who never saved their
        # own preferences gets it (those who cannot see tasks are skipped by summary_content).
        org = _merge(defaults)
        for user_id in session.scalars(
            select(User.id).where(User.active.is_not(False)).order_by(User.id)
        ):
            people.setdefault(int(user_id), org)
    for user_id, prefs in people.items():
        summary = prefs["summary"]
        match = TIME_RE.match(str(summary.get("time") or ""))
        if not summary.get("on") or match is None:
            continue
        if (local.hour, local.minute) < (int(match.group(1)), int(match.group(2))):
            continue
        if local.weekday() in (prefs.get("quiet_days") or []):
            continue  # a quiet day (weekend): no summary
        try:
            # One person's failure neither stops nor rolls back the others' (nor their marks).
            with session.begin_nested():
                sent += _summary_for(session, user_id, summary, local, today)
        except Exception:
            log.exception("task summary failed", extra={"user_id": user_id})
    session.flush()
    return sent


def _summary_for(
    session: Session, user_id: int, summary: dict[str, Any], local: datetime, today: str
) -> int:
    from app.notifications.inbox import notify

    day = session.get(AppSetting, SUMMARY_KEY.format(user_id))
    if day is not None and day.value == today:
        return 0
    if day is None:
        session.add(AppSetting(key=SUMMARY_KEY.format(user_id), value=today))
    else:
        day.value = today
    session.flush()
    content = summary_content(session, user_id, local)
    if content is None:
        return 0
    done, overdue, reviews = content["done"], content["overdue"], content["reviews"]
    tomorrow = content.get("tomorrow") or []
    parts = []
    if done:
        parts.append(f"Xong: {_keys(done)}")
    if overdue:
        parts.append(f"Quá hạn: {_keys(overdue)}")
    if reviews:
        parts.append(f"Chờ bạn review: {_keys(reviews)}")
    if tomorrow:
        parts.append(f"Ngày mai: {_keys(tomorrow)}")
    rows = notify(
        session,
        [user_id],
        kind=SUMMARY_KIND,
        title=(
            f"Tóm tắt {local:%d/%m}: {len(done)} xong, {len(overdue)} quá hạn, "
            f"{len(reviews)} MR chờ review, {len(tomorrow)} việc ngày mai"
        ),
        body=" · ".join(parts),
        link="/tasks/me",
        tone="warn" if overdue else "info",
        ref=f"summary:{today}",
    )
    if rows and summary.get("channel") == "telegram":
        queue_telegram(session, rows)
    return 1


# -- personal events detected on the tick (§21.2) --------------------------------------------------

REVIEW_KIND = "task.review"
TEST_FAILED_KIND = "task.test_failed"
# Only fresh events: switching this on never floods anyone with the backlog of old MRs / runs.
PERSONAL_LOOKBACK = timedelta(days=2)


def send_personal(session: Session, now: datetime | None = None) -> dict[str, int]:
    """MRs that ask me to review (the collector's GitLab reviewers; once per MR and person) and
    a covering testcase whose latest result is Failed on my open task (once per result). Both
    go through each person's preferences like any task kind."""
    now = now or datetime.now(UTC)
    out = {"reviews": 0, "test_failed": 0}
    try:
        with session.begin_nested():
            out["reviews"] = _review_requests(session, now)
    except Exception:
        log.exception("task review notices failed")
    try:
        with session.begin_nested():
            out["test_failed"] = _failed_on_mine(session, now)
    except Exception:
        log.exception("task test failure notices failed")
    session.flush()
    return out


def _review_requests(session: Session, now: datetime) -> int:
    from app.api.security import Principal, load_principal
    from app.config import get_settings
    from app.models import Project
    from app.notifications.inbox import notify
    from app.tasks.service import link_of

    settings = get_settings()
    principals: dict[int, Principal | None] = {}

    def principal(user_id: int) -> Principal | None:
        if user_id not in principals:
            try:
                principals[user_id] = load_principal(session, user_id, settings)
            except HTTPException:
                principals[user_id] = None
        return principals[user_id]

    sent = 0
    mrs = list(
        session.scalars(
            select(MergeRequest)
            .where(
                MergeRequest.state == "opened",
                MergeRequest.reviewer_account_ids.is_not(None),
                MergeRequest.updated_at >= now - PERSONAL_LOOKBACK,
            )
            .order_by(MergeRequest.id)
        )
    )
    for mr in mrs:
        accounts = [int(a) for a in mr.reviewer_account_ids or [] if a != mr.author_account_id]
        project = session.get(Project, mr.project_id) if mr.project_id is not None else None
        if not accounts or project is None:
            continue
        users = {
            int(u)
            for u in session.scalars(
                select(Account.user_id).where(
                    Account.id.in_(accounts),
                    Account.kind == "gitlab_user",
                    Account.user_id.is_not(None),
                )
            )
            if u is not None
        }
        author = (
            session.scalar(select(Account.user_id).where(Account.id == mr.author_account_id))
            if mr.author_account_id is not None
            else None
        )
        ref = f"review:{mr.id}"
        targets = []
        for user_id in sorted(users - {author} if author is not None else users):
            p = principal(user_id)
            if p is None or not p.can("tasks.view"):
                continue
            if not p.can_view_project(project.business_project_id):
                continue
            if not _sent(session, user_id, ref):
                targets.append(user_id)
        if not targets:
            continue
        task_key = session.scalar(
            select(Task.key)
            .join(TaskGitLink, TaskGitLink.task_id == Task.id)
            .where(
                TaskGitLink.kind == "mr",
                TaskGitLink.project_id == mr.project_id,
                TaskGitLink.mr_iid == mr.iid,
            )
            .limit(1)
        )
        repo = str(project.path_with_namespace).rsplit("/", 1)[-1]
        notify(
            session,
            targets,
            kind=REVIEW_KIND,
            title=f"MR {repo} !{mr.iid} chờ bạn review",
            body=(mr.title or "")[:300] or None,
            link=link_of(task_key) if task_key else "/tasks/me",
            tone="info",
            ref=ref,
        )
        sent += len(targets)
    return sent


def _failed_on_mine(session: Session, now: datetime) -> int:
    from app.models import TestRunTest
    from app.models.tasks import TaskTestLink
    from app.notifications.inbox import notify
    from app.tasks import quality
    from app.tasks.service import link_of
    from app.testcases import content

    since = now - PERSONAL_LOOKBACK
    recent = {
        int(c)
        for c in session.scalars(
            select(TestRunTest.case_id).where(
                TestRunTest.status == "failed",
                TestRunTest.tested_at >= since,
                TestRunTest.case_id.is_not(None),
            )
        )
        if c is not None
    }
    if not recent:
        return 0
    latest = quality.latest_tests(session, sorted(recent))
    failed = {
        case_id: test
        for case_id, test in latest.items()
        if test.status == "failed" and test.tested_at is not None and test.tested_at >= since
    }
    if not failed:
        return 0
    rows = session.execute(
        select(Task, TaskTestLink.case_id)
        .join(TaskTestLink, TaskTestLink.task_id == Task.id)
        .join(TaskStatus, TaskStatus.id == Task.status_id)
        .where(
            TaskTestLink.kind == "covers",
            TaskTestLink.case_id.in_(sorted(failed)),
            Task.assignee_id.is_not(None),
            TaskStatus.category != "done",
        )
        .order_by(Task.id)
    ).all()
    today = now.astimezone(_tz()).date().isoformat()
    sent = 0
    for task, case_id in rows:
        test = failed[int(case_id)]
        assignee = task.assignee_id
        if assignee is None:
            continue
        stamp = int(test.tested_at.timestamp()) if test.tested_at else 0
        ref = f"testfail:{task.id}:{test.id}:{stamp}"
        # The test_failed_back rule already told them today (and moved the task): not twice.
        if _sent(session, assignee, ref) or _sent(
            session, assignee, f"auto:test_failed_back:{task.id}:{today}"
        ):
            continue
        case_key = content.key("case", test.case_id) or "testcase"
        notify(
            session,
            [assignee],
            kind=TEST_FAILED_KIND,
            title=f"{task.key}: {case_key} Failed",
            body=task.title,
            link=link_of(task.key),
            tone="bad",
            ref=ref,
        )
        sent += 1
    return sent
