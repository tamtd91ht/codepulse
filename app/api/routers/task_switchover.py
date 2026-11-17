"""Chuyển đổi (docs/specs/31 §21.6): old Jira keys still in use, "Ngừng dùng Jira", the Lead
checklist and demo data. Everything here needs tasks.manage on the dự án.

Included before the tasks router (its paths all live under /tasks/spaces/{id}/...)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.models import TaskSpace, User
from app.tasks import service as ts
from app.tasks import switchover as sw

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]


def viewer(principal: CurrentPrincipal) -> Principal:
    if not principal.can("tasks.view"):
        raise HTTPException(403, "thiếu quyền xem task")
    return principal


Viewer = Annotated[Principal, Depends(viewer)]


class RetireIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _managed(session: Session, principal: Principal, bp_id: int) -> TaskSpace:
    space = ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    return space


def _state(session: Session, space: TaskSpace) -> dict[str, Any]:
    by = session.get(User, space.jira_retired_by) if space.jira_retired_by else None
    bp_id = space.business_project_id
    return {
        "space": bp_id,
        "jira_retired_at": space.jira_retired_at.isoformat() if space.jira_retired_at else None,
        "jira_retired_by": ts.person(by),
        "readiness": sw.readiness(session, space),
        "uses": sw.uses(session, bp_id),
        "checklist": sw.checklist(session, space),
        "demo_tasks": sw.has_demo(session, bp_id),
    }


@router.get("/tasks/spaces/{bp_id}/switchover")
def switchover_state(bp_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """Everything the Chuyển đổi card shows: Jira state and readiness, old keys used in the
    last 7 days, the Lead checklist, demo tasks present."""
    return _state(session, _managed(session, principal, bp_id))


@router.post("/tasks/spaces/{bp_id}/switchover/retire-jira")
def retire_jira(
    bp_id: int, body: RetireIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    """ "Ngừng dùng Jira". Not ready (issues not imported, old keys used lately): 409 unless a
    reason is given; the reason and the readiness go into the audit log."""
    space = _managed(session, principal, bp_id)
    session.get(TaskSpace, bp_id, with_for_update=True, populate_existing=True)
    out = sw.retire(session, space, principal.user_id, body.reason)
    audit(
        session,
        principal.user_id,
        "task.jira.retire",
        f"business_project:{bp_id}",
        business_project_id=bp_id,
        override=out["override"],
        reason=out["reason"],
        not_imported=out["not_imported"],
        alias_uses=out["alias_uses"],
    )
    session.commit()
    return _state(session, space)


@router.delete("/tasks/spaces/{bp_id}/switchover/retire-jira")
def restore_jira(bp_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """Undo "Ngừng dùng Jira" (Jira can be chosen for bugs again)."""
    space = _managed(session, principal, bp_id)
    sw.restore(session, space)
    audit(
        session,
        principal.user_id,
        "task.jira.restore",
        f"business_project:{bp_id}",
        business_project_id=bp_id,
    )
    session.commit()
    return _state(session, space)


@router.post("/tasks/spaces/{bp_id}/switchover/recommended-rules")
def recommended_rules(bp_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """Turn on the five automation rules the checklist recommends (others keep their switch)."""
    space = _managed(session, principal, bp_id)
    sw.turn_on_recommended(space)
    audit(
        session,
        principal.user_id,
        "task.automation.update",
        f"business_project:{bp_id}",
        business_project_id=bp_id,
        rules=list(sw.RECOMMENDED_RULES),
    )
    session.commit()
    return _state(session, space)


@router.post("/tasks/spaces/{bp_id}/demo", status_code=201)
def create_demo(bp_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """ "Tạo dữ liệu demo" for a training session (label mau-demo, titles "[Mẫu] …")."""
    space = _managed(session, principal, bp_id)
    session.get(TaskSpace, bp_id, with_for_update=True, populate_existing=True)
    out = sw.create_demo(session, space, principal.user_id)
    audit(
        session,
        principal.user_id,
        "task.demo.create",
        f"business_project:{bp_id}",
        business_project_id=bp_id,
        created=out["created"],
    )
    session.commit()
    return out


@router.delete("/tasks/spaces/{bp_id}/demo")
def delete_demo(bp_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """ "Xoá dữ liệu demo": every task labelled mau-demo in the dự án (and the demo sprint /
    version when empty); never another task."""
    from app.api.routers.tasks import _remove_files

    _managed(session, principal, bp_id)
    out = sw.delete_demo(session, bp_id)
    files = out.pop("files")
    audit(
        session,
        principal.user_id,
        "task.demo.delete",
        f"business_project:{bp_id}",
        business_project_id=bp_id,
        **out,
    )
    session.commit()
    _remove_files(files)
    return out
