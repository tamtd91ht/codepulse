"""Acceptance criteria of a task with stable ids (docs/specs/31 §20.3).

- GET /tasks/{key}/criteria: the criteria in order; with tests.view, each with the testcases
  linked to it (`covers` links carrying its id) and their coverage status.
- POST /tasks/{key}/criteria {text}: appended at the end (tasks.edit).
- PATCH /tasks/{key}/criteria/{id} {text?, done?, position?}: edit, tick, move (tasks.edit).
- DELETE /tasks/{key}/criteria/{id}: its linked cases keep covering the task (criterion SET NULL).

Included before the tasks router, like task_health."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.models import Task
from app.models.tasks import TaskCriterion
from app.tasks import quality
from app.tasks import service as ts

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]

CRITERIA_MAX = 50
TEXT_MAX = 500


def viewer(principal: CurrentPrincipal) -> Principal:
    if not principal.can("tasks.view"):
        raise HTTPException(403, "thiếu quyền xem task")
    return principal


Viewer = Annotated[Principal, Depends(viewer)]


class CriterionIn(BaseModel):
    text: str = Field(min_length=1, max_length=TEXT_MAX)


class CriterionPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX)
    done: bool | None = None
    # The new 0-based place in the list; the others shift.
    position: int | None = Field(default=None, ge=0, le=CRITERIA_MAX)


def _clean(text: str) -> str:
    out = " ".join(text.split())[:TEXT_MAX]
    if not out:
        raise HTTPException(422, "tiêu chí không được để trống")
    return out


def listing(session: Session, principal: Principal, task: Task) -> list[dict[str, Any]]:
    """The criteria in order; their cases and status only for those who see testcases."""
    if principal.can("tests.view"):
        return list(quality.coverage(session, task.id)["criteria"])
    return [
        {
            "id": c.id,
            "position": c.position,
            "text": c.text,
            "done": bool(c.done),
            "cases": [],
            "status": None,
        }
        for c in quality.criteria_of(session, task.id)
    ]


def _criterion(session: Session, task: Task, criterion_id: int) -> TaskCriterion:
    row = session.get(TaskCriterion, criterion_id, with_for_update=True)
    if row is None or row.task_id != task.id:
        raise HTTPException(404, "không thấy tiêu chí")
    return row


def checked_criterion(session: Session, task: Task, criterion_id: int | None) -> int | None:
    """A criterion id sent with a testcase link (POST /tasks/{key}/tests): one of this task's,
    else 422."""
    if criterion_id is None:
        return None
    row = session.get(TaskCriterion, criterion_id)
    if row is None or row.task_id != task.id:
        raise HTTPException(422, "tiêu chí không thuộc task này")
    return row.id


def _renumber(rows: list[TaskCriterion]) -> None:
    for i, row in enumerate(rows):
        if row.position != i:
            row.position = i


@router.get("/tasks/{key}/criteria")
def list_criteria(key: str, principal: Viewer, session: SessionDep) -> list[dict[str, Any]]:
    task = ts.task_or_404(session, principal, key)
    return listing(session, principal, task)


@router.post("/tasks/{key}/criteria", status_code=201)
def add_criterion(
    key: str, body: CriterionIn, principal: Viewer, session: SessionDep
) -> list[dict[str, Any]]:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "edit", task)
    # Lock the task so two adds never take the same position.
    session.get(Task, task.id, with_for_update=True)
    count = int(session.scalar(select(func.count()).where(TaskCriterion.task_id == task.id)) or 0)
    if count >= CRITERIA_MAX:
        raise HTTPException(422, f"tối đa {CRITERIA_MAX} tiêu chí mỗi task")
    text = _clean(body.text)
    session.add(
        TaskCriterion(task_id=task.id, position=count, text=text, created_by=principal.user_id)
    )
    ts.log(session, task, principal.user_id, "criterion", None, text)
    session.commit()
    return listing(session, principal, task)


@router.patch("/tasks/{key}/criteria/{criterion_id}")
def edit_criterion(
    key: str, criterion_id: int, body: CriterionPatch, principal: Viewer, session: SessionDep
) -> list[dict[str, Any]]:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "edit", task)
    session.get(Task, task.id, with_for_update=True)
    row = _criterion(session, task, criterion_id)
    if body.text is not None:
        text = _clean(body.text)
        if text != row.text:
            ts.log(session, task, principal.user_id, "criterion", row.text, text)
            row.text = text
    if body.done is not None and body.done != row.done:
        row.done = body.done
    if body.position is not None:
        rows = [c for c in quality.criteria_of(session, task.id) if c.id != row.id]
        rows.insert(min(body.position, len(rows)), row)
        _renumber(rows)
    session.commit()
    return listing(session, principal, task)


@router.delete("/tasks/{key}/criteria/{criterion_id}", status_code=204)
def delete_criterion(key: str, criterion_id: int, principal: Viewer, session: SessionDep) -> None:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "edit", task)
    session.get(Task, task.id, with_for_update=True)
    row = _criterion(session, task, criterion_id)
    ts.log(session, task, principal.user_id, "criterion", row.text, None)
    session.delete(row)
    session.flush()
    _renumber(quality.criteria_of(session, task.id))
    session.commit()
