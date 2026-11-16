"""Filter by a sentence (docs/specs/31 §19.8): `POST /tasks/nl-filter`. Deterministic parser
only (`used_ai` is always false): an LLM call per keystroke-pause is neither cheap nor quick enough
for a filter box, and the words a filter needs are few."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.routers.tasks import Viewer
from app.tasks import nlfilter
from app.tasks import service as ts

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]


class NlFilterIn(BaseModel):
    space: int
    text: str = Field(max_length=nlfilter.MAX_TEXT)


@router.post("/tasks/nl-filter")
def nl_filter(body: NlFilterIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    space = ts.space_or_404(session, principal, body.space)
    chips, query = nlfilter.parse(body.text, nlfilter.vocab(session, space))
    return {"chips": nlfilter.chips_out(chips), "query": query, "used_ai": False}
