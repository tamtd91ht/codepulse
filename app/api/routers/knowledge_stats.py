"""GET /projects/{id}/knowledge/stats (docs/specs/19 §5): per-rule counters of a repo's
review knowledge over the last 30 or 90 days. Readable by anyone signed in, like the
knowledge itself and `/repos/profiles`: it is built from the repo's own reviews."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal
from app.models import Project
from app.review.knowledge_stats import MAX_DAYS, rule_stats

router = APIRouter(tags=["knowledge"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/projects/{project_id}/knowledge/stats")
def knowledge_stats(
    project_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 30,
) -> dict[str, Any]:
    project = session.get(Project, project_id)
    if project is None or not project.tracked:
        raise HTTPException(404, "repo not found")
    return {"project_id": project.id, **rule_stats(session, project.id, days)}
