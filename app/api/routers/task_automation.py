"""Task automation rules and task notification preferences (docs/specs/31 §19.7).

Registered before the tasks router: its paths are never read as a task key there."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import AdminPrincipal, CurrentPrincipal, Principal
from app.models import TaskSpace, TaskStatus
from app.tasks import automation
from app.tasks import service as ts

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]


def viewer(principal: CurrentPrincipal) -> Principal:
    if not principal.can("tasks.view"):
        raise HTTPException(403, "thiếu quyền xem task")
    return principal


Viewer = Annotated[Principal, Depends(viewer)]


def _answer(principal: Principal, space: TaskSpace) -> dict[str, Any]:
    return {
        "rules": automation.out(space),
        "can_manage": ts.can_manage_space(principal, space.business_project_id),
    }


@router.get("/tasks/spaces/{bp_id}/automation")
def get_automation(bp_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    return _answer(principal, ts.space_or_404(session, principal, bp_id))


@router.put("/tasks/spaces/{bp_id}/automation")
def put_automation(
    bp_id: int, body: dict[str, dict[str, Any]], principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    space = session.get(TaskSpace, bp_id, with_for_update=True, populate_existing=True)
    if space is None:
        raise HTTPException(404, "dự án chưa bật Task")
    automation.set_config(space, body)
    audit(
        session,
        principal.user_id,
        "task.automation",
        f"business_project:{bp_id}",
        rules={k: v.get("on") for k, v in body.items()},
    )
    session.commit()
    return _answer(principal, space)


# -- the rule log and dry runs (§20.5) ------------------------------------------------------------


@router.get("/tasks/spaces/{bp_id}/automation/log")
def get_automation_log(
    bp_id: int,
    principal: Viewer,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=automation.LOG_MAX)] = 50,
) -> dict[str, Any]:
    ts.space_or_404(session, principal, bp_id)
    return {"items": automation.log_out(session, bp_id, limit)}


@router.post("/tasks/spaces/{bp_id}/automation/{rule}/dry-run")
def dry_run_rule(bp_id: int, rule: str, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """What a scheduled rule would do now, on or off: nothing is changed or sent; one log row
    marked dry_run keeps the answer."""
    space = ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    plan = automation.dry_run(session, space, rule)
    session.commit()
    return {"rule": rule, "count": len(plan), "items": plan[: automation.DRY_RUN_KEEP]}


# -- parent_done_ask: close the open sub-tasks with their parent ---------------------------------


@router.get("/tasks/{key}/open-subtasks")
def get_open_subtasks(key: str, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    task = ts.task_or_404(session, principal, key)
    space = session.get(TaskSpace, task.business_project_id)
    items = automation.open_subtasks(session, task)
    return {
        "ask": automation.is_on(space, "parent_done_ask") and bool(items),
        "items": [
            {"key": t.key, "title": t.title, "can_close": ts.may(principal, "transition", t)}
            for t in items
        ],
    }


class CloseSubtasksIn(BaseModel):
    resolution: Literal["done", "wont_do", "duplicate", "cannot_reproduce", "obsolete"] = "done"


@router.post("/tasks/{key}/close-subtasks")
def close_subtasks(
    key: str, body: CloseSubtasksIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    """Each open sub-task the person may move goes to the space's "done" status (the workflow's
    transitions are not asked: the person closes the work with its parent). One the critical
    gate keeps open is reported, not closed."""
    task = ts.task_or_404(session, principal, key)
    space = session.get(TaskSpace, task.business_project_id)
    if space is None:
        raise HTTPException(404, "dự án chưa bật Task")
    if not automation.is_on(space, "parent_done_ask"):
        raise HTTPException(409, "quy tắc đóng sub-task cùng task cha đang tắt")
    current = session.get(TaskStatus, task.status_id)
    if current is None or current.category != "done":
        raise HTTPException(409, "chỉ đóng sub-task khi task cha đã ở nhóm Xong")
    done = automation.done_status(session, space)
    if done is None:
        raise HTTPException(409, "dự án không có trạng thái nhóm Xong")
    closed: list[str] = []
    skipped: list[dict[str, str]] = []
    for child in automation.open_subtasks(session, task):
        if not ts.may(principal, "transition", child):
            skipped.append({"key": child.key, "reason": "không có quyền chuyển trạng thái"})
            continue
        try:
            with session.begin_nested():
                ts.set_status(
                    session,
                    child,
                    done,
                    principal.user_id,
                    check_transition=False,
                    resolution=body.resolution,
                )
            closed.append(child.key)
        except HTTPException as exc:
            skipped.append({"key": child.key, "reason": str(exc.detail)})
    session.commit()
    return {"closed": closed, "skipped": skipped}


# -- one's own task notification preferences -------------------------------------------------------


class SummaryIn(BaseModel):
    on: bool | None = None
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    channel: Literal["inbox", "telegram"] | None = None


class NotifyPrefsIn(BaseModel):
    mode: Literal["immediate", "digest"] | None = None
    quiet_hours: list[int] | None = Field(default=None, min_length=2, max_length=2)
    # §21.2: quiet all day on Saturday (5) and / or Sunday (6).
    quiet_days: list[int] | None = Field(default=None, max_length=2)
    kinds: list[str] | None = None
    # §20.5: a channel per kind, and the daily summary.
    channels: dict[str, Literal["inbox", "telegram", "off"]] | None = None
    summary: SummaryIn | None = None


def _sent(body: NotifyPrefsIn) -> dict[str, Any]:
    sent = body.model_dump(include=body.model_fields_set)
    if body.summary is not None:
        sent["summary"] = body.summary.model_dump(exclude_none=True)
    return sent


def _prefs(session: Session, user_id: int, prefs: dict[str, Any]) -> dict[str, Any]:
    return {
        **automation.prefs_out(prefs, automation.telegram_linked(session, user_id)),
        # §21.2: False while the person follows the organisation's defaults.
        "customised": automation.customised(session, user_id),
        "org_defaults": automation.org_defaults(session) is not None,
    }


@router.get("/me/task-notify")
def get_task_notify(principal: CurrentPrincipal, session: SessionDep) -> dict[str, Any]:
    return _prefs(session, principal.user_id, automation.prefs_of(session, principal.user_id))


@router.put("/me/task-notify")
def put_task_notify(
    body: NotifyPrefsIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    prefs = automation.set_prefs(session, principal.user_id, _sent(body))
    session.commit()
    return _prefs(session, principal.user_id, prefs)


@router.delete("/me/task-notify")
def reset_task_notify(principal: CurrentPrincipal, session: SessionDep) -> dict[str, Any]:
    """Back to the organisation's defaults (§21.2)."""
    prefs = automation.reset_prefs(session, principal.user_id)
    session.commit()
    return _prefs(session, principal.user_id, prefs)


# -- the organisation's defaults (§21.2) -----------------------------------------------------------


def _defaults_out(session: Session) -> dict[str, Any]:
    prefs = automation._merge(automation.org_defaults(session))
    return {
        **automation.prefs_out(prefs, True),
        "set": automation.org_defaults(session) is not None,
    }


@router.get("/task-notify/defaults")
def get_notify_defaults(principal: Viewer, session: SessionDep) -> dict[str, Any]:
    return _defaults_out(session)


@router.put("/task-notify/defaults")
def put_notify_defaults(
    body: NotifyPrefsIn, principal: AdminPrincipal, session: SessionDep
) -> dict[str, Any]:
    """Admins: what everyone who has not saved their own preferences gets."""
    sent = _sent(body)
    automation.set_org_defaults(session, principal.user_id, sent)
    audit(session, principal.user_id, "task.notify_defaults", "app_settings", keys=sorted(sent))
    session.commit()
    return _defaults_out(session)
