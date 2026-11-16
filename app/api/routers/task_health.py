"""Sprint health of a dự án and the multi-project overview (docs/specs/31 §19.5): read-only.

Included before the tasks router so /tasks/overview is never read as a task key."""

from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.config import Settings, get_settings
from app.models import Sprint
from app.tasks import health, sprint_review
from app.tasks import service as ts

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def viewer(principal: CurrentPrincipal) -> Principal:
    if not principal.can("tasks.view"):
        raise HTTPException(403, "thiếu quyền xem task")
    return principal


Viewer = Annotated[Principal, Depends(viewer)]


@router.get("/tasks/overview")
def tasks_overview(
    principal: Viewer, session: SessionDep, settings: SettingsDep
) -> list[dict[str, Any]]:
    return health.overview(session, principal, ZoneInfo(settings.report_tz))


@router.get("/tasks/spaces/{bp_id}/health")
def space_health(
    bp_id: int, principal: Viewer, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    ts.space_or_404(session, principal, bp_id)
    return health.sprint_health(session, bp_id, ZoneInfo(settings.report_tz))


# -- sprint review (docs/specs/31 §20.6) ----------------------------------------------------------


def _sprint_or_404(session: Session, principal: Principal, sprint_id: int) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None or not ts.can_view_space(principal, sprint.business_project_id):
        raise HTTPException(404, "không thấy sprint")
    return sprint


@router.get("/tasks/sprints/{sprint_id}/review")
def sprint_review_of(
    sprint_id: int, principal: Viewer, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """What the sprint delivered, carried over, took on after it started, and its bugs and
    findings; `markdown` is the retro page the UI writes through POST /docs."""
    sprint = _sprint_or_404(session, principal, sprint_id)
    return sprint_review.review(session, sprint, ZoneInfo(settings.report_tz), principal)


@router.get("/tasks/sprints/{sprint_id}/scope-change")
def sprint_scope_change(
    sprint_id: int, principal: Viewer, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """Tasks added to / removed from the sprint after it started (the Backlog header note)."""
    sprint = _sprint_or_404(session, principal, sprint_id)
    return sprint_review.scope_change(session, sprint, ZoneInfo(settings.report_tz))


@router.get("/tasks/spaces/{bp_id}/review/latest")
def latest_review(
    bp_id: int, principal: Viewer, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """No sprint running: the last closed sprint's review, or in Kanban mode the 14-day flow."""
    ts.space_or_404(session, principal, bp_id)
    return sprint_review.latest(session, bp_id, ZoneInfo(settings.report_tz), principal)
