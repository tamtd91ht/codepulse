"""GET /projects/{id}/knowledge/stats (docs/specs/19 §5): per-rule counters of a repo's
review knowledge over the last 30 or 90 days. Readable by anyone who sees the repo's dự án,
like the knowledge itself and `/repos/profiles`: it is built from the repo's own reviews."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.access import visible_repo
from app.api.deps import get_session
from app.api.security import CurrentPrincipal
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
    project = visible_repo(session, principal, project_id)
    return {"project_id": project.id, **rule_stats(session, project.id, days)}
