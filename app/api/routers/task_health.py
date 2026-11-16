"""Sprint health of a dự án and the multi-project overview (docs/specs/31 §19.5): read-only.

Included before the tasks router so /tasks/overview is never read as a task key."""

from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.config import Settings, get_settings
from app.tasks import health
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
