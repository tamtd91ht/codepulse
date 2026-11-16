"""One timeline of a task (docs/specs/31 §20.1): read-only, needs tasks.view on its dự án.

Included before the tasks router, like task_health."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.tasks import service as ts
from app.tasks import timeline as tl

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]


def viewer(principal: CurrentPrincipal) -> Principal:
    if not principal.can("tasks.view"):
        raise HTTPException(403, "thiếu quyền xem task")
    return principal


Viewer = Annotated[Principal, Depends(viewer)]


@router.get("/tasks/{key}/timeline")
def task_timeline(
    key: str,
    principal: Viewer,
    session: SessionDep,
    types: str | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=tl.MAX_LIMIT)] = 50,
) -> list[dict[str, Any]]:
    try:
        wanted = tl.parse_types(types)
    except ValueError as e:
        raise HTTPException(422, f"loại không hợp lệ: {e}") from e
    task = ts.task_or_404(session, principal, key)
    return tl.timeline(session, principal, task, wanted, before, limit)
