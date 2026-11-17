"""What the enforced Definition of Done still misses on the open tasks of a dự án (docs/specs/31
§21.1): the Board shows it as the last card badge.

- GET /tasks/spaces/{bp}/dod-gaps → {"enforced": bool, "gaps": {key: [label, …]}}: only when the
  dự án enforces its DoD, only for tasks in a "doing" status (a task not started yet misses all of
  it by nature), at most 300 of them, most recently updated first. A task meeting its DoD is left
  out.

Included before the tasks router (its /tasks/spaces/{bp} paths)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.models import Task
from app.models.tasks import TaskStatus
from app.tasks import quality
from app.tasks import service as ts

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]

GAPS_MAX = 300


def viewer(principal: CurrentPrincipal) -> Principal:
    if not principal.can("tasks.view"):
        raise HTTPException(403, "thiếu quyền xem task")
    return principal


Viewer = Annotated[Principal, Depends(viewer)]


@router.get("/tasks/spaces/{bp}/dod-gaps")
def dod_gaps(bp: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    space = ts.space_or_404(session, principal, bp)
    conf = quality.dod_settings(space)
    if not conf["enforce"] or not conf["checks"]:
        return {"enforced": False, "gaps": {}}
    tasks = session.scalars(
        select(Task)
        .join(TaskStatus, TaskStatus.id == Task.status_id)
        .where(Task.business_project_id == bp, TaskStatus.category == "doing")
        .order_by(Task.updated_at.desc(), Task.id.desc())
        .limit(GAPS_MAX)
    )
    gaps: dict[str, list[str]] = {}
    for task in tasks:
        missing = [str(c["label"]) for c in quality.dod(session, task, space) if not c["ok"]]
        if missing:
            gaps[task.key] = missing
    return {"enforced": True, "gaps": gaps}
