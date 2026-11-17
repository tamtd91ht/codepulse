"""Anonymous usage telemetry (docs/specs/31 §21.2).

What is kept: (role, event, surface, at) in `usage_events`. Never a user id, a task, a key or
any text: the role is the person's highest automatic role (member < lead < manager <
project_manager < manage_all < admin), "system" for git and automation rules.

Where events come from:

- the dashboard's beacon (`POST /api/v1/usage`, batched, fire-and-forget): pages opened, the
  Ctrl+K palette (opened, a task found, a page reached, a command run) and single-key shortcuts;
- the server, where things happen: a status change (`status_changed`, from
  `service.set_status`) and a task created (`task_created`, from `service.create_task`). Their
  surface is read from the request (`UsageContext`, an ASGI middleware): `/move` is a drag on
  the board / backlog, `/transition` the task detail (or the palette, when the dashboard set
  the short-lived `cp_surface=palette` cookie as a palette command ran), `/tasks/bulk` a bulk
  edit, `/tasks/batch` the AI breakdown, `/import/jira` the Jira import, `/testcases/…/bug` a
  bug from a failed test; git and automation rules come from the call itself;
- the task-automation tick: rule actions a person undid within 24 h (`detect_undone`) and the
  retention (`cleanup`, KEEP_DAYS).

Recording never fails the caller's action: a bad event is dropped. Rows are added to the
caller's transaction, so a rolled-back action (or a dry run) keeps none.
"""

import logging
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import AppSetting, ProjectGrant, TeamMember, User
from app.models.usage import UsageEvent

log = logging.getLogger(__name__)

KEEP_DAYS = 180
ROLES = ("member", "lead", "manager", "project_manager", "manage_all", "admin")
SYSTEM = "system"
# event -> the surfaces it may carry (anything else is dropped).
EVENTS: dict[str, frozenset[str]] = {
    "page_open": frozenset(
        {
            "my_work",
            "board",
            "backlog",
            "timeline",
            "list",
            "reports",
            "task_detail",
            "health",
            "versions",
            "overview",
        }
    ),
    "status_change": frozenset(
        {"drag", "palette", "detail", "bulk", "git", "rule", "mcp", "ai", "telegram", "web"}
    ),
    "task_create": frozenset(
        {"button", "ai_breakdown", "failed_test", "jira_import", "mcp", "web"}
    ),
    "palette": frozenset({"open", "search_task", "navigate", "command"}),
    "shortcut": frozenset({"key"}),
    # surface: the rule template key (checked against app.tasks.automation.BY_KEY)
    "rule_undone": frozenset(),
}
# What the dashboard may send; the rest is recorded by the server where it happens.
CLIENT_EVENTS = frozenset({"page_open", "palette", "shortcut"})
AUTOMATED = frozenset({"git", "rule"})
TARGET_AUTOMATED = 0.6
BEACON_MAX = 50

# -- the request a server-side event happens in ---------------------------------------------------

# (method, path, surface hint from the cp_surface cookie)
_REQUEST: ContextVar[tuple[str, str, str | None] | None] = ContextVar("usage_request", default=None)
# Set by code that knows its surface (the Telegram "Bắt đầu" button).
_SURFACE: ContextVar[str | None] = ContextVar("usage_surface", default=None)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def _hint(headers: list[tuple[bytes, bytes]]) -> str | None:
    for name, value in headers:
        if name.lower() != b"cookie":
            continue
        for part in value.decode("latin-1").split(";"):
            key, _, val = part.strip().partition("=")
            if key == "cp_surface" and val in ("palette",):
                return val
    return None


class UsageContext:
    """ASGI middleware: the method, path and surface hint of the request, for the events the
    server records while serving it (sync endpoints run in a thread with a copy of the
    context, so they see it)."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        token = _REQUEST.set(
            (str(scope.get("method", "")), str(scope.get("path", "")), _hint(scope["headers"]))
        )
        try:
            await self.app(scope, receive, send)
        finally:
            _REQUEST.reset(token)


@contextmanager
def surface(name: str) -> Iterator[None]:
    token = _SURFACE.set(name)
    try:
        yield
    finally:
        _SURFACE.reset(token)


# -- roles ----------------------------------------------------------------------------------------


def role_of(principal: Any) -> str:
    """The highest automatic role of a loaded principal (app.api.security.Principal)."""
    if principal.is_admin:
        return "admin"
    if principal.manages_all:
        return "manage_all"
    if "manage" in principal.project_roles.values():
        return "project_manager"
    if principal.managed_teams:
        return "manager"
    if principal.lead_teams:
        return "lead"
    return "member"


def role_for(session: Session, user_id: int | None) -> str:
    """The same from the database (no full principal per status change); cached per session."""
    if user_id is None:
        return SYSTEM
    cache: dict[int, str] = session.info.setdefault("usage_roles", {})
    if user_id in cache:
        return cache[user_id]
    user = session.get(User, user_id)
    role = "member"
    if user is None:
        role = SYSTEM
    else:
        from app.config import get_settings

        admins = {e.lower() for e in get_settings().admin_emails}
        today = date.today()
        team_roles = {
            (m.role or "").lower()
            for m in session.scalars(select(TeamMember).where(TeamMember.user_id == user_id))
            if m.joined_at <= today and (m.left_at is None or m.left_at > today)
        }
        if user.primary_email and user.primary_email.lower() in admins:
            role = "admin"
        elif user.manage_all:
            role = "manage_all"
        elif session.scalar(
            select(ProjectGrant.user_id)
            .where(ProjectGrant.user_id == user_id, ProjectGrant.role == "manage")
            .limit(1)
        ):
            role = "project_manager"
        elif "manager" in team_roles:
            role = "manager"
        elif "lead" in team_roles:
            role = "lead"
    cache[user_id] = role
    return role


# -- recording ------------------------------------------------------------------------------------


def valid(event: str, where: str) -> bool:
    if event == "rule_undone":
        from app.tasks.automation import BY_KEY

        return where in BY_KEY
    allowed = EVENTS.get(event)
    return allowed is not None and where in allowed


def record(session: Session, role: str, event: str, where: str) -> None:
    """One event in the caller's transaction; an unknown event / surface / role is dropped."""
    if role not in ROLES and role != SYSTEM:
        return
    if not valid(event, where):
        log.debug("usage event dropped", extra={"event": event, "surface": where})
        return
    session.add(UsageEvent(role=role, event=event, surface=where, at=datetime.now(UTC)))


def _web_status_surface() -> str:
    request = _REQUEST.get()
    if request is None:
        return "web"
    _method, path, hint = request
    if path.endswith("/transition"):
        return "palette" if hint == "palette" else "detail"
    if path.endswith("/move"):
        return "drag"
    if path.endswith("/tasks/bulk"):
        return "bulk"
    if path.endswith("/close-subtasks"):
        return "detail"
    if hint == "palette":
        return "palette"
    return "detail" if "/tasks/" in path else "web"


def status_changed(session: Session, actor_id: int | None, via: str) -> None:
    """From `service.set_status`, once a task has moved."""
    try:
        from app.tasks.automation import _ACTIVE

        if _ACTIVE.get():
            where, role = "rule", SYSTEM
        elif via in ("git", "smart_commit"):
            where, role = "git", SYSTEM if actor_id is None else role_for(session, actor_id)
        elif via in ("mcp", "ai"):
            where, role = via, role_for(session, actor_id)
        else:
            where = _SURFACE.get() or _web_status_surface()
            role = role_for(session, actor_id)
        record(session, role, "status_change", where)
    except Exception:  # never the person's action
        log.warning("usage: status change not recorded", exc_info=True)


def _create_surface() -> str:
    request = _REQUEST.get()
    if request is None:
        return "web"
    path = request[1]
    if path.endswith("/tasks/batch"):
        return "ai_breakdown"
    if "/import/jira" in path:
        return "jira_import"
    if path.startswith("/api/v1/testcases/") and "/bug" in path:
        return "failed_test"
    if path.rstrip("/").endswith("/api/v1/tasks"):
        return "button"
    return "web"


def task_created(session: Session, actor_id: int | None, via: str) -> None:
    """From `service.create_task`."""
    try:
        where = "mcp" if via == "mcp" else (_SURFACE.get() or _create_surface())
        record(session, role_for(session, actor_id), "task_create", where)
    except Exception:
        log.warning("usage: task creation not recorded", exc_info=True)


def record_beacon(session: Session, role: str, items: list[dict[str, Any]]) -> int:
    """The dashboard's batch: only client events, at most BEACON_MAX; the rest ignored."""
    n = 0
    for item in items[:BEACON_MAX]:
        event, where = str(item.get("event") or ""), str(item.get("surface") or "")
        if event in CLIENT_EVENTS and valid(event, where):
            record(session, role, event, where)
            n += 1
    return n


# -- the tick: undone rule actions, retention -----------------------------------------------------

UNDO_CURSOR = "usage_undo_cursor"  # app_settings: the last task_rule_runs id looked at
UNDO_WINDOW = timedelta(hours=24)
UNDO_FIELD = {"move": "status", "assign": "assignee", "set_version": "version"}
UNDO_BATCH = 500


def detect_undone(session: Session, now: datetime | None = None) -> int:
    """Rule actions (task_rule_runs move / assign / set_version) whose 24 h have passed: a person
    who set the field back to what it was before the rule (the rule's own history row) within
    24 h undid it; one `rule_undone` event (surface: the rule) each. A cursor keeps the rows
    already looked at."""
    from app.models import TaskHistory
    from app.models.tasks import TaskRuleRun

    now = now or datetime.now(UTC)
    row = session.get(AppSetting, UNDO_CURSOR)
    cursor = int(row.value) if row is not None and isinstance(row.value, int) else 0
    if row is None:
        # First run: start from now (rows from before the telemetry are not judged).
        last = session.scalar(select(func.max(TaskRuleRun.id))) or 0
        session.add(AppSetting(key=UNDO_CURSOR, value=int(last)))
        session.flush()
        return 0
    runs = list(
        session.scalars(
            select(TaskRuleRun)
            .where(
                TaskRuleRun.id > cursor,
                TaskRuleRun.at <= now - UNDO_WINDOW,
            )
            .order_by(TaskRuleRun.id)
            .limit(UNDO_BATCH)
        )
    )
    found = 0
    for run in runs:
        field = UNDO_FIELD.get(run.action)
        if field is None or run.dry_run or run.task_id is None:
            continue
        # The rule's own row: no person, the same field, written with the log row.
        auto = session.scalar(
            select(TaskHistory)
            .where(
                TaskHistory.task_id == run.task_id,
                TaskHistory.field == field,
                TaskHistory.user_id.is_(None),
                TaskHistory.at <= run.at + timedelta(minutes=1),
                TaskHistory.at >= run.at - timedelta(minutes=1),
            )
            .order_by(TaskHistory.id.desc())
            .limit(1)
        )
        if auto is None:
            continue
        back = session.scalar(
            select(TaskHistory)
            .where(
                TaskHistory.task_id == run.task_id,
                TaskHistory.field == field,
                TaskHistory.user_id.is_not(None),
                TaskHistory.id > auto.id,
                TaskHistory.at <= auto.at + UNDO_WINDOW,
                TaskHistory.new.is_not_distinct_from(auto.old),
            )
            .order_by(TaskHistory.id)
            .limit(1)
        )
        if back is not None:
            record(session, role_for(session, back.user_id), "rule_undone", run.rule)
            found += 1
    if runs:
        row.value = int(runs[-1].id)
    session.flush()
    return found


def cleanup(session: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    result = session.execute(
        delete(UsageEvent).where(UsageEvent.at < now - timedelta(days=KEEP_DAYS))
    )
    return int(getattr(result, "rowcount", 0) or 0)


# -- the admin dashboard --------------------------------------------------------------------------


def _week(at: datetime) -> str:
    day = at.astimezone(UTC).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def summary(session: Session, weeks: int = 8, now: datetime | None = None) -> dict[str, Any]:
    """Counts per ISO week (Monday, UTC) by event / surface and by role, the share of status
    changes made by git and rules (target 60 %), and the share of keyboard use (palette
    actions and shortcuts among them plus pages opened)."""
    now = now or datetime.now(UTC)
    today = now.astimezone(UTC).date()
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    since = datetime(start.year, start.month, start.day, tzinfo=UTC)
    week = func.date_trunc("week", func.timezone("UTC", UsageEvent.at))
    rows = session.execute(
        select(week, UsageEvent.event, UsageEvent.surface, UsageEvent.role, func.count())
        .where(UsageEvent.at >= since)
        .group_by(week, UsageEvent.event, UsageEvent.surface, UsageEvent.role)
    ).all()
    labels = [(start + timedelta(weeks=i)).isoformat() for i in range(weeks)]
    by_surface: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_role: dict[str, Counter[str]] = defaultdict(Counter)
    status: Counter[str] = Counter()
    keyboard = pages = 0
    for at, event, where, role, n in rows:
        label = (at.date() if isinstance(at, datetime) else at).isoformat()
        count = int(n)
        by_surface[(str(event), str(where))][label] += count
        by_role[str(role)][label] += count
        if event == "status_change":
            status[str(where)] += count
        elif (event == "palette" and where != "open") or event == "shortcut":
            keyboard += count
        elif event == "page_open":
            pages += count
    total_status = sum(status.values())
    automated = sum(n for k, n in status.items() if k in AUTOMATED)
    return {
        "weeks": labels,
        "events": [
            {
                "event": event,
                "surface": where,
                "counts": [counts.get(w, 0) for w in labels],
                "total": sum(counts.values()),
            }
            for (event, where), counts in sorted(by_surface.items())
        ],
        "roles": [
            {
                "role": role,
                "counts": [counts.get(w, 0) for w in labels],
                "total": sum(counts.values()),
            }
            for role, counts in sorted(by_role.items(), key=lambda kv: -sum(kv[1].values()))
        ],
        "automation": {
            "total": total_status,
            "automated": automated,
            "share": round(automated / total_status, 3) if total_status else None,
            "target": TARGET_AUTOMATED,
        },
        "keyboard": {
            "total": keyboard + pages,
            "keyboard": keyboard,
            "share": round(keyboard / (keyboard + pages), 3) if keyboard + pages else None,
        },
        "keep_days": KEEP_DAYS,
    }
