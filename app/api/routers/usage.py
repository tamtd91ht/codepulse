"""Anonymous usage telemetry (docs/specs/31 §21.2): the dashboard's beacon and the admins'
summary. Only (role, event, surface, at) is kept; see app/usage."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import usage
from app.api.deps import get_session
from app.api.security import AdminPrincipal, CurrentPrincipal

router = APIRouter(tags=["usage"])
SessionDep = Annotated[Session, Depends(get_session)]


class UsageItem(BaseModel):
    event: str = Field(max_length=40)
    surface: str = Field(max_length=40)


class UsageIn(BaseModel):
    events: list[UsageItem] = Field(default_factory=list, max_length=usage.BEACON_MAX)


@router.post("/usage", status_code=204)
def post_usage(body: UsageIn, principal: CurrentPrincipal, session: SessionDep) -> Response:
    """Fire-and-forget from the dashboard: unknown events are ignored, never an error."""
    if usage.record_beacon(
        session, usage.role_of(principal), [i.model_dump() for i in body.events]
    ):
        session.commit()
    return Response(status_code=204)


@router.get("/usage/summary")
def get_usage_summary(
    principal: AdminPrincipal,
    session: SessionDep,
    weeks: Annotated[int, Query(ge=1, le=26)] = 8,
) -> dict[str, Any]:
    return usage.summary(session, weeks)
