"""Timeline by epic and sprint planning (docs/specs/31 §21.3).

Included before the tasks router: /tasks/timeline is not a task key."""

from datetime import date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.config import Settings, get_settings
from app.models import Sprint
from app.tasks import planning, roadmap
from app.tasks import service as ts

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def viewer(principal: CurrentPrincipal) -> Principal:
    if not principal.can("tasks.view"):
        raise HTTPException(403, "thiếu quyền xem task")
    return principal


Viewer = Annotated[Principal, Depends(viewer)]


def _window(frm: date | None, to: date | None, tz: ZoneInfo) -> tuple[date, date, date]:
    today = datetime.now(tz).date()
    try:
        lo, hi = roadmap.window(frm, to, today)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return lo, hi, today


@router.get("/tasks/timeline")
def many_timeline(
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    space: Annotated[list[int] | None, Query()] = None,
    frm: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> dict[str, Any]:
    """Epics of several dự án, read-only (the Manager view of /tasks/overview)."""
    tz = ZoneInfo(settings.report_tz)
    lo, hi, today = _window(frm, to, tz)
    return roadmap.many_timeline(session, principal, space or [], lo, hi, tz, today)


@router.get("/tasks/spaces/{bp_id}/timeline")
def space_timeline(
    bp_id: int,
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    frm: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> dict[str, Any]:
    ts.space_or_404(session, principal, bp_id)
    tz = ZoneInfo(settings.report_tz)
    lo, hi, today = _window(frm, to, tz)
    return roadmap.space_timeline(session, principal, bp_id, lo, hi, tz, today)


def _sprint_or_404(session: Session, principal: Principal, sprint_id: int) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None or not ts.can_view_space(principal, sprint.business_project_id):
        raise HTTPException(404, "không thấy sprint")
    return sprint


@router.get("/tasks/sprints/{sprint_id}/plan")
def sprint_plan(sprint_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """Points against capacity and the load per person (the Backlog's sprint header)."""
    sprint = _sprint_or_404(session, principal, sprint_id)
    return planning.plan(session, sprint, ts.can_sprint(principal, sprint.business_project_id))


class CapacityIn(BaseModel):
    # A number: manual capacity; null: back to the mean of the last 3 closed sprints.
    capacity_points: float | None = Field(default=None, ge=0, le=100000)


@router.patch("/tasks/sprints/{sprint_id}/capacity")
def sprint_capacity(
    sprint_id: int, body: CapacityIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    sprint = _sprint_or_404(session, principal, sprint_id)
    ts.require_sprint(principal, sprint.business_project_id)
    if sprint.state == "closed":
        raise HTTPException(409, "sprint đã đóng")
    planning.set_capacity(sprint, body.capacity_points)
    audit(
        session,
        principal.user_id,
        "task.sprint.capacity",
        f"sprint:{sprint.id}",
        capacity=body.capacity_points,
    )
    session.commit()
    return planning.plan(session, sprint, True)


@router.get("/tasks/sprints/{sprint_id}/preflight")
def sprint_preflight(sprint_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """Warnings shown before starting the sprint; starting stays allowed."""
    sprint = _sprint_or_404(session, principal, sprint_id)
    return planning.preflight(session, sprint)
