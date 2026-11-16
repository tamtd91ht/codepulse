"""Task (docs/specs/31): CodePulse's own task tracker, tied to git and code review.

Static paths come first: /tasks/spaces, /tasks/people, /tasks/linked… are never a task key."""

import mimetypes
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from rq import Queue
from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.security import CurrentPrincipal, Principal
from app.config import Settings, get_settings
from app.models import (
    BusinessProject,
    Project,
    Sprint,
    Task,
    TaskAiRun,
    TaskAttachment,
    TaskComment,
    TaskField,
    TaskFilter,
    TaskGitLink,
    TaskHistory,
    TaskLink,
    TaskPriority,
    TaskSpace,
    TaskStatus,
    TaskVersion,
    TaskWatcher,
    TaskWorklog,
)
from app.models.tasks import FIELD_KINDS, LINK_KINDS, PRIORITY_COLORS
from app.tasks import ai as task_ai
from app.tasks import git as task_git
from app.tasks import reports
from app.tasks import service as ts
from app.workers.queues import review_queue

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
QueueDep = Annotated[Queue, Depends(get_queue)]

MAX_FILE_BYTES = 20 * 1024 * 1024
SPACE_FILES_BYTES = 2 * 1024 * 1024 * 1024
# Images the browser may show in place (a preview, an image in a description or comment).
# SVG is left out: it can carry script.
INLINE_IMAGES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"})


def _content_type(declared: str | None, name: str) -> str | None:
    """The header the browser sent, else a guess from the file name (a generic one is no help)."""
    value = (declared or "").split(";", 1)[0].strip().lower()
    if not value or value == "application/octet-stream":
        value = (mimetypes.guess_type(name)[0] or "").lower()
    return value[:120] or None


TaskType = Literal["epic", "story", "task", "bug", "subtask"]
# A priority key of the dự án (docs/specs/31 §18); checked against its priorities.
Priority = Annotated[str, Field(min_length=1, max_length=30)]
Relation = Literal["fixes", "refs"]
Resolution = Literal["done", "wont_do", "duplicate", "cannot_reproduce"]


def viewer(principal: CurrentPrincipal) -> Principal:
    if not principal.can("tasks.view"):
        raise HTTPException(403, "thiếu quyền xem task")
    return principal


Viewer = Annotated[Principal, Depends(viewer)]


def files_dir() -> Path:
    root = Path(os.environ.get("TASK_FILES_DIR") or "data/task-files")
    root.mkdir(parents=True, exist_ok=True)
    return root


# -- bodies ----------------------------------------------------------------------------------------


class SpaceIn(BaseModel):
    key: str = Field(min_length=2, max_length=10)
    types: list[TaskType] | None = None
    rules: dict[str, Any] | None = None
    # §19.6: a template only when Task is first turned on (422 otherwise); board_mode any time.
    template: Literal["scrum", "kanban", "qa"] | None = None
    board_mode: Literal["scrum", "kanban"] | None = None


class StatusIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=60)
    category: Literal["todo", "doing", "done"]
    transitions: list[int] | None = None


class StatusesIn(BaseModel):
    statuses: list[StatusIn] = Field(min_length=1, max_length=30)
    move: dict[int, int] = Field(default_factory=dict)


class TaskFieldsIn(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=100_000)
    type: TaskType | None = None
    priority: Priority | None = None
    assignee_id: int | None = None
    parent_key: str | None = None
    # Why it was closed (only while in a "done" status), docs/specs/31 §19.1.
    resolution: Resolution | None = None
    labels: list[str] | None = None
    components: list[str] | None = None
    story_points: Decimal | None = Field(default=None, ge=0, le=999)
    estimate_min: int | None = Field(default=None, ge=0, le=1_000_000)
    spent_min: int | None = Field(default=None, ge=0, le=10_000_000)
    due_date: date | None = None
    sprint_id: int | None = None
    version_id: int | None = None
    status_id: int | None = None
    custom: dict[str, Any] | None = None


class TaskIn(TaskFieldsIn):
    space: int
    title: str = Field(min_length=1, max_length=300)
    type: TaskType = "task"


class BatchItem(BaseModel):
    type: TaskType = "task"
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    story_points: Decimal | None = Field(default=None, ge=0, le=999)


class BatchIn(BaseModel):
    space: int
    parent_key: str | None = None
    items: list[BatchItem] = Field(min_length=1, max_length=50)


class TransitionIn(BaseModel):
    status_id: int
    comment: str | None = Field(default=None, max_length=20_000)
    # Into a "done" status: why (default "done"), docs/specs/31 §19.1.
    resolution: Resolution | None = None


class MoveIn(BaseModel):
    status_id: int | None = None
    sprint_id: int | None = None
    before_key: str | None = None
    after_key: str | None = None
    # Distinguishes "to the backlog" (null) from "sprint untouched" (absent).
    model_config = {"extra": "ignore"}


class BulkSet(BaseModel):
    status_id: int | None = None
    assignee_id: int | None = None
    priority: Priority | None = None
    sprint_id: int | None = None
    version_id: int | None = None
    labels_add: list[str] | None = None
    labels_remove: list[str] | None = None


class BulkIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=500)
    set: BulkSet = Field(default_factory=BulkSet)
    delete: bool = False


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)


class LinkIn(BaseModel):
    key: str
    kind: Literal["blocks", "relates", "duplicates", "clones"]


class WatchIn(BaseModel):
    watching: bool


class WorklogIn(BaseModel):
    minutes: int = Field(gt=0, le=24 * 60 * 7)
    day: date | None = None
    note: str | None = Field(default=None, max_length=500)


class GitIn(BaseModel):
    kind: Literal["commit", "mr"]
    sha: str | None = Field(default=None, min_length=4, max_length=64)
    project_id: int | None = None
    mr_iid: int | None = None
    relation: Relation = "refs"


class BranchIn(BaseModel):
    project_id: int
    from_ref: str | None = Field(default=None, max_length=255)


class SprintIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    goal: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    end_date: date | None = None


class StartIn(BaseModel):
    start_date: date
    end_date: date


class CompleteIn(BaseModel):
    move_to: int | None = None


class VersionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    release_date: date | None = None


class FilterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    space: int | None = None
    query: dict[str, Any] = Field(default_factory=dict)
    shared: bool = False


class FieldIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "number", "date", "select", "user"]
    options: list[str] = Field(default_factory=list)
    required: bool = False
    archived: bool = False


class FieldsIn(BaseModel):
    fields: list[FieldIn] = Field(max_length=50)


class AiIn(BaseModel):
    kind: Literal["breakdown", "summary"]
    hint: str | None = Field(default=None, max_length=2000)


# -- spaces ----------------------------------------------------------------------------------------


@router.get("/tasks/spaces")
def list_spaces(principal: Viewer, session: SessionDep) -> list[dict[str, Any]]:
    return ts.spaces(session, principal)


@router.put("/tasks/spaces/{bp_id}")
def put_space(bp_id: int, body: SpaceIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    bp = session.get(BusinessProject, bp_id)
    if bp is None or not principal.can_view_project(bp_id):
        raise HTTPException(404, "dự án not found")
    ts.require_manage(principal, bp_id)
    types: list[str] | None = list(body.types) if body.types is not None else None
    try:
        space = ts.enable_space(
            session,
            bp_id,
            body.key,
            types,
            body.rules,
            principal.user_id,
            template=body.template,
            board_mode=body.board_mode,
        )
        audit(session, principal.user_id, "task.space", f"business_project:{bp_id}", key=space.key)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "mã đã dùng hoặc dự án vừa được bật Task; tải lại") from None
    return ts.space_out(session, principal, bp, space)


@router.put("/tasks/spaces/{bp_id}/statuses")
def put_statuses(
    bp_id: int, body: StatusesIn, principal: Viewer, session: SessionDep
) -> list[dict[str, Any]]:
    ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    out = ts.set_statuses(session, bp_id, [s.model_dump() for s in body.statuses], dict(body.move))
    audit(session, principal.user_id, "task.statuses", f"business_project:{bp_id}", n=len(out))
    _reproject(session, select(Task.id).where(Task.business_project_id == bp_id))
    session.commit()
    return [ts.status_out(s) for s in ts.statuses_of(session, bp_id)]


class PriorityIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=40)
    level: Literal["highest", "high", "medium", "low", "lowest"]
    color: str
    is_default: bool = False


class PrioritiesIn(BaseModel):
    priorities: list[PriorityIn] = Field(min_length=1, max_length=12)
    move: dict[int, int] = Field(default_factory=dict)


@router.get("/tasks/spaces/{bp_id}/priorities")
def get_priorities(bp_id: int, principal: Viewer, session: SessionDep) -> list[dict[str, Any]]:
    ts.space_or_404(session, principal, bp_id)
    return [ts.priority_out(x) for x in ts.priorities_of(session, bp_id)]


@router.put("/tasks/spaces/{bp_id}/priorities")
def put_priorities(
    bp_id: int, body: PrioritiesIn, principal: Viewer, session: SessionDep
) -> list[dict[str, Any]]:
    """The dự án's priorities, as Jira's priority scheme (docs/specs/31 §18)."""
    ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    if bad := [x.color for x in body.priorities if x.color not in PRIORITY_COLORS]:
        raise HTTPException(422, f"màu không hợp lệ: {bad}")
    out = ts.set_priorities(
        session, bp_id, [x.model_dump() for x in body.priorities], dict(body.move)
    )
    audit(session, principal.user_id, "task.priorities", f"business_project:{bp_id}", n=len(out))
    _reproject(session, select(Task.id).where(Task.business_project_id == bp_id))
    session.commit()
    return [ts.priority_out(x) for x in ts.priorities_of(session, bp_id)]


@router.get("/tasks/spaces/{bp_id}/fields")
def get_fields(bp_id: int, principal: Viewer, session: SessionDep) -> list[dict[str, Any]]:
    ts.space_or_404(session, principal, bp_id)
    return [_field_out(f) for f in _fields(session, bp_id)]


@router.put("/tasks/spaces/{bp_id}/fields")
def put_fields(
    bp_id: int, body: FieldsIn, principal: Viewer, session: SessionDep
) -> list[dict[str, Any]]:
    ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    current = {f.id: f for f in _fields(session, bp_id, archived=True)}
    kept: set[int] = set()
    for i, item in enumerate(body.fields):
        if item.kind not in FIELD_KINDS:
            raise HTTPException(422, "loại trường không hợp lệ")
        row = current.get(item.id) if item.id is not None else None
        if item.id is not None and row is None:
            raise HTTPException(422, f"trường {item.id} không thuộc dự án")
        if row is None:
            row = TaskField(business_project_id=bp_id, name=item.name, kind=item.kind)
            session.add(row)
        row.name, row.kind = item.name.strip(), item.kind
        row.options = [o.strip()[:80] for o in item.options if o.strip()][:50]
        row.required, row.archived, row.position = item.required, item.archived, i
        session.flush()
        kept.add(row.id)
    for ident, row in current.items():
        if ident not in kept:
            row.archived = True  # values stay in tasks.custom; the field just hides
    audit(session, principal.user_id, "task.fields", f"business_project:{bp_id}")
    session.commit()
    return [_field_out(f) for f in _fields(session, bp_id)]


def _reproject(session: Session, ids: Any) -> None:
    """docs/specs/31 §15: bulk changes (statuses, sprint dates) bypass the per-task hook; the
    DPI rows of these tasks are rebuilt when DPI reads CodePulse tasks."""
    from app.tasks import dpi

    if dpi.enabled(session):
        dpi.project(session, list(session.scalars(ids)))


def _fields(session: Session, bp_id: int, archived: bool = False) -> list[TaskField]:
    query = select(TaskField).where(TaskField.business_project_id == bp_id)
    if not archived:
        query = query.where(TaskField.archived.is_(False))
    return list(session.scalars(query.order_by(TaskField.position, TaskField.id)))


def _field_out(f: TaskField) -> dict[str, Any]:
    return {
        "id": f.id,
        "name": f.name,
        "kind": f.kind,
        "options": list(f.options or []),
        "required": f.required,
        "archived": f.archived,
        "position": f.position,
    }


@router.get("/tasks/people")
def task_people(principal: Viewer, session: SessionDep, space: int) -> list[dict[str, Any]]:
    ts.space_or_404(session, principal, space)
    return [p for p in (ts.person(u) for u in ts.people(session, space)) if p]


# -- listing ---------------------------------------------------------------------------------------


def _list_query(
    session: Session,
    principal: Principal,
    *,
    space: list[int],
    q: str | None,
    status: list[int],
    category: list[str],
    type_: list[str],
    assignee: list[str],
    reporter: list[str],
    priority: list[str],
    label: list[str],
    component: list[str],
    sprint: str | None,
    version: int | None,
    parent: str | None,
    due: str | None,
    updated_from: date | None,
) -> Select[Any]:
    visible = principal.visible_projects()
    enabled = select(TaskSpace.business_project_id)
    query = select(Task).where(Task.business_project_id.in_(enabled))
    if visible is not None:
        query = query.where(Task.business_project_id.in_(visible))
    if space:
        query = query.where(Task.business_project_id.in_(space))
    if q:
        text = q.strip()
        like = f"%{text}%"
        query = query.where(
            or_(Task.key == text.upper(), Task.title.ilike(like), Task.description.ilike(like))
        )
    if status:
        query = query.where(Task.status_id.in_(status))
    if category:
        query = query.where(
            Task.status_id.in_(select(TaskStatus.id).where(TaskStatus.category.in_(category)))
        )
    if type_:
        query = query.where(Task.type.in_(type_))

    def people(column: Any, values: list[str]) -> Any:
        ids = [int(v) for v in values if v.isdigit()]
        if "me" in values:
            ids.append(principal.user_id)
        parts = [column.in_(ids)] if ids else []
        if "none" in values:
            parts.append(column.is_(None))
        return or_(*parts) if parts else None

    if assignee and (cond := people(Task.assignee_id, assignee)) is not None:
        query = query.where(cond)
    if reporter and (cond := people(Task.reporter_id, reporter)) is not None:
        query = query.where(cond)
    if priority:
        query = query.where(Task.priority.in_(priority))
    for one in label:
        query = query.where(Task.labels.contains([one]))
    for one in component:
        query = query.where(Task.components.contains([one]))
    if sprint == "backlog":
        query = query.where(Task.sprint_id.is_(None))
    elif sprint == "active":
        query = query.where(Task.sprint_id.in_(select(Sprint.id).where(Sprint.state == "active")))
    elif sprint and sprint.isdigit():
        query = query.where(Task.sprint_id == int(sprint))
    if version is not None:
        query = query.where(Task.version_id == version)
    if parent:
        parent_id = select(Task.id).where(Task.key == parent.upper()).scalar_subquery()
        query = query.where(Task.parent_id == parent_id)
    if due in ("overdue", "week"):
        today = datetime.now(UTC).date()
        not_done = Task.status_id.in_(select(TaskStatus.id).where(TaskStatus.category != "done"))
        if due == "overdue":
            query = query.where(Task.due_date < today, not_done)
        else:
            query = query.where(Task.due_date >= today, Task.due_date <= today + timedelta(days=7))
    if updated_from is not None:
        query = query.where(
            Task.updated_at >= datetime.combine(updated_from, datetime.min.time(), UTC)
        )
    return query


SORTS: dict[str, Any] = {
    "rank": Task.rank,
    "updated": Task.updated_at,
    "created": Task.created_at,
    "due": Task.due_date,
    "key": Task.number,
    "priority": Task.priority,
}


@router.get("/tasks")
def list_tasks(
    principal: Viewer,
    session: SessionDep,
    space: Annotated[list[int], Query()] = [],  # noqa: B006 - FastAPI query lists
    q: str | None = None,
    status: Annotated[list[int], Query()] = [],  # noqa: B006
    category: Annotated[list[str], Query()] = [],  # noqa: B006
    type: Annotated[list[str], Query()] = [],  # noqa: B006
    assignee: Annotated[list[str], Query()] = [],  # noqa: B006
    reporter: Annotated[list[str], Query()] = [],  # noqa: B006
    priority: Annotated[list[str], Query()] = [],  # noqa: B006
    label: Annotated[list[str], Query()] = [],  # noqa: B006
    component: Annotated[list[str], Query()] = [],  # noqa: B006
    sprint: str | None = None,
    version: int | None = None,
    parent: str | None = None,
    due: str | None = None,
    updated_from: date | None = None,
    sort: Literal["rank", "updated", "created", "priority", "due", "key"] = "rank",
    order: Literal["asc", "desc"] = "asc",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    query = _list_query(
        session,
        principal,
        space=space,
        q=q,
        status=status,
        category=category,
        type_=type,
        assignee=assignee,
        reporter=reporter,
        priority=priority,
        label=label,
        component=component,
        sprint=sprint,
        version=version,
        parent=parent,
        due=due,
        updated_from=updated_from,
    )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    if sort == "priority":  # the dự án's own order: first = most urgent
        column: Any = (
            select(TaskPriority.position)
            .where(
                TaskPriority.business_project_id == Task.business_project_id,
                TaskPriority.key == Task.priority,
            )
            .correlate(Task)
            .scalar_subquery()
        )
    else:
        column = SORTS[sort]
    ordered = column.desc().nulls_last() if order == "desc" else column.asc().nulls_last()
    tasks: list[Task] = list(
        session.scalars(query.order_by(ordered, Task.id).limit(limit).offset(offset))
    )
    return {"items": ts.rows(session, tasks), "total": int(total)}


@router.get("/tasks/linked")
def linked_tasks(
    principal: Viewer,
    session: SessionDep,
    project_id: int,
    sha: str | None = None,
    mr_iid: int | None = None,
) -> list[dict[str, Any]]:
    """Tasks tied to a commit or an MR (the strip on a review page)."""
    query = select(TaskGitLink.task_id)
    if sha:
        query = query.where(TaskGitLink.kind == "commit", TaskGitLink.sha == sha)
    elif mr_iid is not None:
        query = query.where(
            TaskGitLink.kind == "mr",
            TaskGitLink.project_id == project_id,
            TaskGitLink.mr_iid == mr_iid,
        )
    else:
        raise HTTPException(422, "cần sha hoặc mr_iid")
    tasks = [
        t
        for t in session.scalars(select(Task).where(Task.id.in_(query)).order_by(Task.id))
        if ts.can_view_space(principal, t.business_project_id)
    ]
    return ts.rows(session, tasks)


# -- create / bulk ---------------------------------------------------------------------------------


@router.post("/tasks", status_code=201)
def create(body: TaskIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    space = ts.space_or_404(session, principal, body.space)
    if not ts.can_create(principal, body.space):
        raise HTTPException(403, "bạn không tạo được task ở dự án này")
    _check_new_assignee(principal, body.space, body.assignee_id)
    task = ts.create_task(session, space, body.model_dump(exclude_unset=True), principal.user_id)
    audit(session, principal.user_id, "task.create", f"task:{task.key}")
    session.commit()
    return detail(session, principal, task)


def _check_new_assignee(principal: Principal, bp_id: int, assignee_id: int | None) -> None:
    other = assignee_id is not None and assignee_id != principal.user_id
    if other and not ts.can_assign_new(principal, bp_id):
        raise HTTPException(403, "bạn không có quyền giao task cho người khác (tasks.assign)")


@router.post("/tasks/batch", status_code=201)
def create_batch(body: BatchIn, principal: Viewer, session: SessionDep) -> list[dict[str, Any]]:
    space = ts.space_or_404(session, principal, body.space)
    if not ts.can_create(principal, body.space):
        raise HTTPException(403, "bạn không tạo được task ở dự án này")
    for item in body.items:
        _check_new_assignee(principal, body.space, getattr(item, "assignee_id", None))
    made = [
        ts.create_task(
            session,
            space,
            {**item.model_dump(), "parent_key": body.parent_key},
            principal.user_id,
        )
        for item in body.items
    ]
    audit(
        session, principal.user_id, "task.create", f"tasks:{len(made)}", keys=[t.key for t in made]
    )
    session.commit()
    return ts.rows(session, made)


@router.post("/tasks/bulk")
def bulk(body: BulkIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    tasks = [ts.task_or_404(session, principal, k) for k in dict.fromkeys(body.keys)]
    if body.delete:
        for task in tasks:
            ts.require(principal, "delete", task)
        files = _files_of(session, [t.id for t in tasks])
        for task in tasks:
            session.delete(task)
        audit(session, principal.user_id, "task.delete", f"tasks:{len(tasks)}", keys=body.keys)
        session.commit()
        _remove_files(files)
        return {"updated": 0, "deleted": len(tasks)}
    wanted = body.set.model_dump(exclude_unset=True)
    needs = {
        "assign" if k == "assignee_id" else "transition" if k == "status_id" else "edit"
        for k in wanted
    }
    for task in tasks:
        for right in sorted(needs):
            ts.require(principal, right, task)
    for task in tasks:
        changes = {
            k: v
            for k, v in wanted.items()
            if k in ("assignee_id", "priority", "sprint_id", "version_id")
            # A sub-task follows its parent's sprint and version (§19.1): skipped here.
            and not (task.type == "subtask" and k in ("sprint_id", "version_id"))
        }
        if "labels_add" in wanted or "labels_remove" in wanted:
            labels = [x for x in task.labels or [] if x not in (wanted.get("labels_remove") or [])]
            labels += [x for x in wanted.get("labels_add") or [] if x not in labels]
            changes["labels"] = labels
        if changes:
            ts.update_task(session, task, changes, principal.user_id)
        if wanted.get("status_id") is not None:
            status = session.get(TaskStatus, wanted["status_id"])
            if status is None or status.business_project_id != task.business_project_id:
                raise HTTPException(422, f"trạng thái không thuộc dự án của {task.key}")
            ts.set_status(session, task, status, principal.user_id)
    audit(session, principal.user_id, "task.bulk", f"tasks:{len(tasks)}", set=list(wanted))
    session.commit()
    return {"updated": len(tasks), "deleted": 0}


def _files_of(session: Session, task_ids: list[int]) -> list[str]:
    """Stored files of these tasks and their sub-tasks (removed with them by the cascade)."""
    ids = set(task_ids)
    frontier = set(task_ids)
    while frontier:
        frontier = set(session.scalars(select(Task.id).where(Task.parent_id.in_(frontier)))) - ids
        ids |= frontier
    return list(session.scalars(select(TaskAttachment.path).where(TaskAttachment.task_id.in_(ids))))


def _remove_files(paths: list[str]) -> None:
    root = files_dir()
    for rel in paths:
        (root / rel).unlink(missing_ok=True)


# -- filters ---------------------------------------------------------------------------------------


@router.get("/tasks/filters")
def list_filters(principal: Viewer, session: SessionDep) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(TaskFilter)
        .where(or_(TaskFilter.user_id == principal.user_id, TaskFilter.shared.is_(True)))
        .order_by(TaskFilter.name)
    )
    return [
        _filter_out(f, principal)
        for f in rows
        if f.business_project_id is None or principal.can_view_project(f.business_project_id)
    ]


def _filter_out(f: TaskFilter, principal: Principal) -> dict[str, Any]:
    return {
        "id": f.id,
        "name": f.name,
        "space": f.business_project_id,
        "query": f.query,
        "shared": f.shared,
        "mine": f.user_id == principal.user_id,
        "user_id": f.user_id,
        "can_delete": f.user_id == principal.user_id,
    }


@router.post("/tasks/filters", status_code=201)
def create_filter(body: FilterIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    if body.space is not None:
        ts.space_or_404(session, principal, body.space)
    row = TaskFilter(
        user_id=principal.user_id,
        business_project_id=body.space,
        name=body.name.strip(),
        query=body.query,
        shared=body.shared,
    )
    session.add(row)
    session.commit()
    return _filter_out(row, principal)


@router.delete("/tasks/filters/{filter_id}", status_code=204)
def delete_filter(filter_id: int, principal: Viewer, session: SessionDep) -> None:
    row = session.get(TaskFilter, filter_id)
    if row is None or row.user_id != principal.user_id:
        raise HTTPException(404, "không thấy bộ lọc")
    session.delete(row)
    session.commit()


# -- attachments (download by id) ------------------------------------------------------------------


@router.get("/tasks/attachments/{attachment_id}")
def download(
    attachment_id: int, principal: Viewer, session: SessionDep, inline: bool = False
) -> FileResponse:
    row = session.get(TaskAttachment, attachment_id)
    task = session.get(Task, row.task_id) if row else None
    if row is None or task is None or not ts.can_view_space(principal, task.business_project_id):
        raise HTTPException(404, "không thấy file")
    path = files_dir() / row.path
    if not path.is_file():
        raise HTTPException(404, "file không còn trên máy chủ")
    # Only plain images open in the page; anything else downloads, sniffing off, so an
    # uploaded HTML or SVG never runs as the dashboard.
    show = inline and row.content_type in INLINE_IMAGES
    return FileResponse(
        path,
        media_type=(row.content_type or "") if show else "application/octet-stream",
        filename=row.name,
        content_disposition_type="inline" if show else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=86400",
        },
    )


def _may_drop_file(principal: Principal, task: Task, row: TaskAttachment) -> bool:
    own = row.uploaded_by is not None and row.uploaded_by == principal.user_id
    return (own and ts.may(principal, "attach", task)) or ts.may(principal, "moderate", task)


@router.delete("/tasks/attachments/{attachment_id}", status_code=204)
def delete_attachment(attachment_id: int, principal: Viewer, session: SessionDep) -> None:
    row = session.get(TaskAttachment, attachment_id)
    task = session.get(Task, row.task_id) if row else None
    if row is None or task is None or not ts.can_view_space(principal, task.business_project_id):
        raise HTTPException(404, "không thấy file")
    if not _may_drop_file(principal, task, row):
        raise HTTPException(403, "chỉ người tải lên (hoặc người có quyền kiểm duyệt) xoá được")
    ts.log(session, task, principal.user_id, "attachment", row.name, None)
    path = files_dir() / row.path
    session.delete(row)
    session.commit()
    path.unlink(missing_ok=True)


# -- sprints ---------------------------------------------------------------------------------------


def _sprint(session: Session, principal: Principal, sprint_id: int, manage: bool = True) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None or not ts.can_view_space(principal, sprint.business_project_id):
        raise HTTPException(404, "không thấy sprint")
    if manage:
        ts.require_sprint(principal, sprint.business_project_id)
    return sprint


def _sprint_out(session: Session, sprint: Sprint) -> dict[str, Any]:
    done_ids = set(
        session.scalars(
            select(TaskStatus.id).where(
                TaskStatus.business_project_id == sprint.business_project_id,
                TaskStatus.category == "done",
            )
        )
    )
    held = sorted(reports.sprint_tasks(session, sprint.id), key=lambda t: (t.rank, t.id))
    points = sum(reports.points(t) for t in held)
    return {
        "id": sprint.id,
        "space": sprint.business_project_id,
        "name": sprint.name,
        "goal": sprint.goal,
        "state": sprint.state,
        "start_date": sprint.start_date.isoformat() if sprint.start_date else None,
        "end_date": sprint.end_date.isoformat() if sprint.end_date else None,
        "closed_at": sprint.closed_at.isoformat() if sprint.closed_at else None,
        "committed_points": float(sprint.committed_points)
        if sprint.committed_points is not None
        else None,
        # The sprint's tasks as TaskRow (the Backlog tab lists them), and their counts.
        "tasks": ts.rows(session, held),
        "task_count": len(held),
        "done_tasks": sum(1 for t in held if t.status_id in done_ids),
        "points": round(points, 1),
        "done_points": round(sum(reports.points(t) for t in held if t.status_id in done_ids), 1),
    }


@router.get("/tasks/spaces/{bp_id}/sprints")
def list_sprints(
    bp_id: int, principal: Viewer, session: SessionDep, closed: bool = False
) -> list[dict[str, Any]]:
    ts.space_or_404(session, principal, bp_id)
    query = select(Sprint).where(Sprint.business_project_id == bp_id)
    if not closed:
        query = query.where(Sprint.state != "closed")
    order = {"active": 0, "planned": 1, "closed": 2}
    sprints = sorted(
        session.scalars(query),
        key=lambda s: (order[s.state], s.start_date or date.max, s.id),
    )
    return [_sprint_out(session, s) for s in sprints]


@router.post("/tasks/spaces/{bp_id}/sprints", status_code=201)
def create_sprint(
    bp_id: int, body: SprintIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    space = ts.space_or_404(session, principal, bp_id)
    ts.require_sprint(principal, bp_id)
    count = session.scalar(
        select(func.count()).select_from(Sprint).where(Sprint.business_project_id == bp_id)
    )
    sprint = Sprint(
        business_project_id=bp_id,
        name=(body.name or "").strip() or f"{space.key} Sprint {int(count or 0) + 1}",
        goal=body.goal,
        start_date=body.start_date,
        end_date=body.end_date,
    )
    session.add(sprint)
    audit(session, principal.user_id, "task.sprint.create", f"business_project:{bp_id}")
    session.commit()
    return _sprint_out(session, sprint)


@router.patch("/tasks/sprints/{sprint_id}")
def update_sprint(
    sprint_id: int, body: SprintIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    sprint = _sprint(session, principal, sprint_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "name" and not (value or "").strip():
            raise HTTPException(422, "tên sprint trống")
        setattr(sprint, field, value.strip() if field == "name" else value)
    _reproject(session, select(Task.id).where(Task.sprint_id == sprint.id))
    session.commit()
    return _sprint_out(session, sprint)


@router.delete("/tasks/sprints/{sprint_id}", status_code=204)
def delete_sprint(sprint_id: int, principal: Viewer, session: SessionDep) -> None:
    sprint = _sprint(session, principal, sprint_id)
    if sprint.state != "planned":
        raise HTTPException(409, "chỉ xoá được sprint chưa bắt đầu")
    session.execute(update(Task).where(Task.sprint_id == sprint.id).values(sprint_id=None))
    session.delete(sprint)
    session.commit()


@router.post("/tasks/sprints/{sprint_id}/start")
def start_sprint(
    sprint_id: int, body: StartIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    sprint = _sprint(session, principal, sprint_id)
    if sprint.state != "planned":
        raise HTTPException(409, "sprint đã bắt đầu hoặc đã đóng")
    if body.end_date < body.start_date:
        raise HTTPException(422, "ngày kết thúc trước ngày bắt đầu")
    active = session.scalars(
        select(Sprint).where(
            Sprint.business_project_id == sprint.business_project_id, Sprint.state == "active"
        )
    ).first()
    if active is not None:
        raise HTTPException(409, f"dự án đang chạy {active.name}: kết thúc nó trước")
    sprint.state, sprint.start_date, sprint.end_date = "active", body.start_date, body.end_date
    sprint.committed_points = Decimal(str(reports.committed(session, sprint)))
    audit(session, principal.user_id, "task.sprint.start", f"sprint:{sprint.id}")
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "dự án vừa có sprint khác bắt đầu") from None
    return _sprint_out(session, sprint)


@router.post("/tasks/sprints/{sprint_id}/complete")
def complete_sprint(
    sprint_id: int, body: CompleteIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    sprint = _sprint(session, principal, sprint_id)
    if sprint.state != "active":
        raise HTTPException(409, "sprint không đang chạy")
    if body.move_to is not None:
        target = session.get(Sprint, body.move_to)
        if (
            target is None
            or target.business_project_id != sprint.business_project_id
            or target.state == "closed"
            or target.id == sprint.id
        ):
            raise HTTPException(422, "sprint đích không hợp lệ")
    not_done = select(TaskStatus.id).where(
        TaskStatus.business_project_id == sprint.business_project_id,
        TaskStatus.category != "done",
    )
    # A sub-task goes where its parent goes (docs/specs/31 §19.1), whatever its own status: an
    # unfinished parent takes its done sub-tasks along, a done parent keeps its open ones.
    unfinished = list(
        session.scalars(
            select(Task).where(
                Task.sprint_id == sprint.id,
                Task.status_id.in_(not_done),
                Task.type != "subtask",
            )
        )
    )
    orphans = list(
        session.scalars(
            select(Task).where(
                Task.sprint_id == sprint.id,
                Task.status_id.in_(not_done),
                Task.type == "subtask",
                Task.parent_id.is_(None),
            )
        )
    )
    moving = unfinished + orphans
    if unfinished:
        moving += list(
            session.scalars(
                select(Task).where(
                    Task.parent_id.in_([t.id for t in unfinished]), Task.type == "subtask"
                )
            )
        )
    for task in moving:
        task.sprint_id = body.move_to
    moved = len(unfinished) + len(orphans)
    sprint.state, sprint.closed_at = "closed", datetime.now(UTC)
    audit(session, principal.user_id, "task.sprint.complete", f"sprint:{sprint.id}", moved=moved)
    session.commit()
    return {**_sprint_out(session, sprint), "moved": int(moved or 0)}


@router.get("/tasks/sprints/{sprint_id}/burndown")
def burndown(
    sprint_id: int, principal: Viewer, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    sprint = _sprint(session, principal, sprint_id, manage=False)
    return reports.burndown(session, sprint, ZoneInfo(settings.report_tz))


@router.get("/tasks/spaces/{bp_id}/reports")
def space_reports(
    bp_id: int,
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    ts.space_or_404(session, principal, bp_id)
    tz = ZoneInfo(settings.report_tz)
    last = date_to or datetime.now(tz).date()
    first = date_from or last - timedelta(days=29)
    if first > last or (last - first).days > 366:
        raise HTTPException(422, "khoảng ngày không hợp lệ (tối đa 366 ngày)")
    return reports.report(session, bp_id, first, last, tz)


# -- versions --------------------------------------------------------------------------------------


def _version(session: Session, principal: Principal, version_id: int) -> TaskVersion:
    version = session.get(TaskVersion, version_id)
    if version is None or not ts.can_view_space(principal, version.business_project_id):
        raise HTTPException(404, "không thấy phiên bản")
    return version


def _version_out(session: Session, v: TaskVersion) -> dict[str, Any]:
    done = select(TaskStatus.id).where(
        TaskStatus.business_project_id == v.business_project_id, TaskStatus.category == "done"
    )
    total = session.scalar(select(func.count()).select_from(Task).where(Task.version_id == v.id))
    finished = session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.version_id == v.id, Task.status_id.in_(done))
    )
    return {
        "id": v.id,
        "space": v.business_project_id,
        "name": v.name,
        "description": v.description,
        "release_date": v.release_date.isoformat() if v.release_date else None,
        "released_at": v.released_at.isoformat() if v.released_at else None,
        "tasks": int(total or 0),
        "done": int(finished or 0),
        "done_tasks": int(finished or 0),
    }


class JiraImportIn(BaseModel):
    project_key: str = Field(min_length=2, max_length=10)
    dry_run: bool = True
    rewrite_defects: bool = True


@router.get("/tasks/spaces/{bp_id}/import/jira")
def jira_import_sources(bp_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """The Jira projects synced for this dự án and how much of each is imported (§19.2)."""
    from app.tasks import importer

    ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    return {
        "sources": importer.sources(session, bp_id),
        "imported": importer.imported_count(session, bp_id),
    }


@router.post("/tasks/spaces/{bp_id}/import/jira")
def jira_import(
    bp_id: int, body: JiraImportIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    """Preview (dry_run) or import a Jira project's synced issues as tasks (§19.2)."""
    from app.tasks import importer

    space = ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    out = importer.run(
        session,
        space,
        body.project_key,
        principal.user_id,
        dry_run=body.dry_run,
        rewrite_defects=body.rewrite_defects,
    )
    if not body.dry_run:
        audit(
            session,
            principal.user_id,
            "task.import.jira",
            f"business_project:{bp_id}",
            project_key=out["project_key"],
            created=out["created"],
            defects=out["defects_rewritten"],
        )
        session.commit()
    return out


@router.get("/tasks/spaces/{bp_id}/epics")
def list_epics(bp_id: int, principal: Viewer, session: SessionDep) -> list[dict[str, Any]]:
    """The dự án's epics with their children's progress (the Backlog's Epic bar, §19.1)."""
    ts.space_or_404(session, principal, bp_id)
    return reports.epics(session, bp_id)


@router.get("/tasks/spaces/{bp_id}/versions")
def list_versions(bp_id: int, principal: Viewer, session: SessionDep) -> list[dict[str, Any]]:
    ts.space_or_404(session, principal, bp_id)
    rows = session.scalars(
        select(TaskVersion)
        .where(TaskVersion.business_project_id == bp_id)
        .order_by(
            TaskVersion.released_at.desc().nulls_first(), TaskVersion.release_date, TaskVersion.id
        )
    )
    return [_version_out(session, v) for v in rows]


@router.post("/tasks/spaces/{bp_id}/versions", status_code=201)
def create_version(
    bp_id: int, body: VersionIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    if session.scalars(
        select(TaskVersion).where(
            TaskVersion.business_project_id == bp_id, TaskVersion.name == body.name.strip()
        )
    ).first():
        raise HTTPException(409, "đã có phiên bản cùng tên")
    v = TaskVersion(
        business_project_id=bp_id,
        name=body.name.strip(),
        description=body.description,
        release_date=body.release_date,
    )
    session.add(v)
    session.commit()
    return _version_out(session, v)


@router.patch("/tasks/versions/{version_id}")
def update_version(
    version_id: int, body: VersionIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    v = _version(session, principal, version_id)
    ts.require_manage(principal, v.business_project_id)
    v.name, v.description, v.release_date = body.name.strip(), body.description, body.release_date
    session.commit()
    return _version_out(session, v)


@router.delete("/tasks/versions/{version_id}", status_code=204)
def delete_version(version_id: int, principal: Viewer, session: SessionDep) -> None:
    v = _version(session, principal, version_id)
    ts.require_manage(principal, v.business_project_id)
    session.delete(v)
    session.commit()


@router.post("/tasks/versions/{version_id}/release")
def release_version(version_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    v = _version(session, principal, version_id)
    ts.require_manage(principal, v.business_project_id)
    v.released_at = None if v.released_at else datetime.now(UTC)
    audit(session, principal.user_id, "task.version.release", f"version:{v.id}")
    session.commit()
    return _version_out(session, v)


@router.get("/tasks/versions/{version_id}/notes")
def version_notes(version_id: int, principal: Viewer, session: SessionDep) -> dict[str, str]:
    v = _version(session, principal, version_id)
    return {"markdown": reports.release_notes(session, v)}


# -- AI runs ---------------------------------------------------------------------------------------


@router.get("/tasks/ai/{run_id}")
def ai_run(run_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    row = session.get(TaskAiRun, run_id)
    task = session.get(Task, row.task_id) if row else None
    if row is None or task is None or not ts.can_view_space(principal, task.business_project_id):
        raise HTTPException(404, "không thấy lượt AI")
    return {
        "id": row.id,
        "task": task.key,
        "kind": row.kind,
        "status": row.status,
        "output": row.output,
        "error": row.error,
        "model": row.model,
        "cost_usd": row.cost_usd,
    }


# -- one task (after every static path) ------------------------------------------------------------


def detail(session: Session, principal: Principal, task: Task) -> dict[str, Any]:
    row = ts.rows(session, [task])[0]
    users = ts.users_by_id
    links = list(
        session.scalars(
            select(TaskLink).where(
                or_(TaskLink.from_task_id == task.id, TaskLink.to_task_id == task.id)
            )
        )
    )
    others = (
        {
            t.id: t
            for t in session.scalars(
                select(Task).where(
                    Task.id.in_(
                        {
                            x.to_task_id if x.from_task_id == task.id else x.from_task_id
                            for x in links
                        }
                    )
                )
            )
        }
        if links
        else {}
    )
    others = {
        i: t for i, t in others.items() if ts.can_view_space(principal, t.business_project_id)
    }
    other_rows = {r["id"]: r for r in ts.rows(session, list(others.values()))}
    subtasks = list(
        session.scalars(select(Task).where(Task.parent_id == task.id).order_by(Task.rank, Task.id))
    )
    comments = list(
        session.scalars(
            select(TaskComment)
            .where(TaskComment.task_id == task.id)
            .order_by(TaskComment.created_at, TaskComment.id)
        )
    )
    history = list(
        session.scalars(
            select(TaskHistory)
            .where(TaskHistory.task_id == task.id)
            .order_by(TaskHistory.at.desc(), TaskHistory.id.desc())
            .limit(300)
        )
    )
    attachments = list(
        session.scalars(
            select(TaskAttachment)
            .where(TaskAttachment.task_id == task.id)
            .order_by(TaskAttachment.id)
        )
    )
    worklogs = list(
        session.scalars(
            select(TaskWorklog)
            .where(TaskWorklog.task_id == task.id)
            .order_by(TaskWorklog.day.desc(), TaskWorklog.id.desc())
        )
    )
    watching = ts.watchers(session, task)
    people = users(
        session,
        [c.author_id for c in comments]
        + [h.user_id for h in history]
        + [a.uploaded_by for a in attachments]
        + [w.user_id for w in worklogs]
        + list(watching),
    )
    statuses = ts.statuses_of(session, task.business_project_id)
    current = next((s for s in statuses if s.id == task.status_id), None)
    allowed = [
        ts.status_out(s)
        for s in statuses
        if current is None
        or current.transitions is None
        or s.id in current.transitions
        or s.id == current.id
    ]
    can = ts.rights(principal, task)
    manage = ts.can_manage_space(principal, task.business_project_id)
    return {
        **row,
        "description": task.description,
        "components": list(task.components or []),
        "estimate_min": task.estimate_min,
        "spent_min": task.spent_min,
        "custom": task.custom or {},
        "watchers": [ts.person(people.get(u)) for u in sorted(watching) if u in people],
        "watching": principal.user_id in watching,
        "links": [
            {
                "id": x.id,
                "kind": x.kind,
                "direction": "out" if x.from_task_id == task.id else "in",
                "task": other_rows.get(
                    x.to_task_id if x.from_task_id == task.id else x.from_task_id
                ),
            }
            for x in links
            if (x.to_task_id if x.from_task_id == task.id else x.from_task_id) in others
        ],
        "subtasks": ts.rows(session, subtasks),
        "comments": [
            {
                "id": c.id,
                "author": ts.person(people.get(c.author_id)) if c.author_id else None,
                "body": c.body,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "edited_at": c.edited_at.isoformat() if c.edited_at else None,
                "can_edit": c.author_id == principal.user_id or can["moderate"],
            }
            for c in comments
        ],
        "history": [
            {
                "id": h.id,
                "user": ts.person(people.get(h.user_id)) if h.user_id else None,
                "field": h.field,
                "old": h.old,
                "new": h.new,
                "via": h.via,
                "at": h.at.isoformat() if h.at else None,
            }
            for h in history
        ],
        "attachments": [
            {
                "id": a.id,
                "name": a.name,
                "size": a.size,
                "content_type": a.content_type,
                "uploaded_by": ts.person(people.get(a.uploaded_by)) if a.uploaded_by else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "url": f"/api/v1/tasks/attachments/{a.id}",
                "can_delete": _may_drop_file(principal, task, a),
            }
            for a in attachments
        ],
        "worklogs": [
            {
                "id": w.id,
                "user": ts.person(people.get(w.user_id)) if w.user_id else None,
                "minutes": w.minutes,
                "day": w.day.isoformat(),
                "note": w.note,
                "via": w.via,
            }
            for w in worklogs
        ],
        "git": ts.git_detail(session, task),
        "transitions": allowed,
        "can": {**can, "manage": manage},
    }


@router.get("/tasks/{key}")
def get_task(key: str, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    return detail(session, principal, ts.task_or_404(session, principal, key))


@router.patch("/tasks/{key}")
def patch_task(
    key: str, body: TaskFieldsIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    task = ts.task_or_404(session, principal, key)
    changes = body.model_dump(exclude_unset=True)
    if "assignee_id" in changes and changes["assignee_id"] != task.assignee_id:
        ts.require(principal, "assign", task)
    if changes.get("status_id") is not None and changes["status_id"] != task.status_id:
        ts.require(principal, "transition", task)
    if any(k not in ("assignee_id", "status_id") for k in changes):
        ts.require_edit(principal, task)
    ts.update_task(session, task, changes, principal.user_id)
    session.commit()
    return detail(session, principal, task)


@router.delete("/tasks/{key}", status_code=204)
def delete_task(key: str, principal: Viewer, session: SessionDep) -> None:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "delete", task)
    audit(session, principal.user_id, "task.delete", f"task:{task.key}", title=task.title)
    files = _files_of(session, [task.id])
    session.delete(task)
    session.commit()
    _remove_files(files)


@router.post("/tasks/{key}/transition")
def transition(
    key: str, body: TransitionIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "transition", task)
    if body.comment and body.comment.strip():
        ts.require(principal, "comment", task)
    status = session.get(TaskStatus, body.status_id)
    if status is None or status.business_project_id != task.business_project_id:
        raise HTTPException(422, "trạng thái không thuộc dự án")
    ts.set_status(session, task, status, principal.user_id, resolution=body.resolution)
    if body.comment and body.comment.strip():
        _add_comment(session, principal, task, body.comment)
    session.commit()
    return detail(session, principal, task)


@router.post("/tasks/{key}/move")
def move_task(key: str, body: MoveIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    task = ts.task_or_404(session, principal, key)
    sent = body.model_fields_set
    if "sprint_id" in sent and body.sprint_id != task.sprint_id:
        ts.require_edit(principal, task)
    if body.status_id is not None and body.status_id != task.status_id:
        ts.require(principal, "transition", task)
    if not (ts.may(principal, "edit", task) or ts.may(principal, "transition", task)):
        raise HTTPException(403, "bạn không sắp xếp được task này")
    if "sprint_id" in sent and body.sprint_id != task.sprint_id:
        ts.update_task(session, task, {"sprint_id": body.sprint_id}, principal.user_id)
    if body.status_id is not None and body.status_id != task.status_id:
        status = session.get(TaskStatus, body.status_id)
        if status is None or status.business_project_id != task.business_project_id:
            raise HTTPException(422, "trạng thái không thuộc dự án")
        ts.set_status(session, task, status, principal.user_id)

    def near(k: str | None) -> Task | None:
        if not k:
            return None
        other = ts.task_or_404(session, principal, k)
        if other.business_project_id != task.business_project_id:
            raise HTTPException(422, "task khác dự án")
        return other

    before, after = near(body.before_key), near(body.after_key)
    if before is not None or after is not None:
        ts.move(session, task, before=before, after=after)
    session.commit()
    return ts.rows(session, [task])[0]


MENTION_MAX = 20


def _add_comment(session: Session, principal: Principal, task: Task, body: str) -> TaskComment:
    comment = TaskComment(task_id=task.id, author_id=principal.user_id, body=body.strip())
    session.add(comment)
    session.flush()
    ts.watch(session, task, principal.user_id)
    who = ts.mentioned(session, task.business_project_id, body)
    if who:
        ts.tell(
            session,
            list(who)[:MENTION_MAX],
            task,
            "task.mentioned",
            f"Bạn được nhắc ở {task.key}",
            body[:300],
            actor_id=principal.user_id,
        )
    others = (
        (ts.watchers(session, task) | {task.assignee_id or 0, task.reporter_id or 0}) - who - {0}
    )
    ts.tell(
        session,
        others,
        task,
        "task.commented",
        f"Bình luận mới ở {task.key}",
        body[:300],
        actor_id=principal.user_id,
    )
    task.updated_at = datetime.now(UTC)
    return comment


@router.post("/tasks/{key}/comments", status_code=201)
def add_comment(
    key: str, body: CommentIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "comment", task)
    comment = _add_comment(session, principal, task, body.body)
    session.commit()
    return {"id": comment.id}


def _comment(
    session: Session, principal: Principal, key: str, comment_id: int
) -> tuple[Task, TaskComment]:
    task = ts.task_or_404(session, principal, key)
    comment = session.get(TaskComment, comment_id)
    if comment is None or comment.task_id != task.id:
        raise HTTPException(404, "không thấy bình luận")
    if comment.author_id != principal.user_id and not ts.may(principal, "moderate", task):
        raise HTTPException(403, "chỉ người viết (hoặc người có quyền kiểm duyệt) sửa / xoá được")
    return task, comment


@router.patch("/tasks/{key}/comments/{comment_id}")
def edit_comment(
    key: str, comment_id: int, body: CommentIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    _, comment = _comment(session, principal, key, comment_id)
    comment.body, comment.edited_at = body.body.strip(), datetime.now(UTC)
    session.commit()
    return {"id": comment.id}


@router.delete("/tasks/{key}/comments/{comment_id}", status_code=204)
def delete_comment(key: str, comment_id: int, principal: Viewer, session: SessionDep) -> None:
    _, comment = _comment(session, principal, key, comment_id)
    session.delete(comment)
    session.commit()


@router.post("/tasks/{key}/links", status_code=201)
def add_link(key: str, body: LinkIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "link", task)
    other = ts.task_or_404(session, principal, body.key)
    if other.id == task.id:
        raise HTTPException(422, "không liên kết task với chính nó")
    if body.kind not in LINK_KINDS:
        raise HTTPException(422, "loại liên kết không hợp lệ")
    exists = session.scalars(
        select(TaskLink).where(
            TaskLink.from_task_id == task.id,
            TaskLink.to_task_id == other.id,
            TaskLink.kind == body.kind,
        )
    ).first()
    if exists is None:
        exists = TaskLink(
            from_task_id=task.id, to_task_id=other.id, kind=body.kind, created_by=principal.user_id
        )
        session.add(exists)
        ts.log(session, task, principal.user_id, "link", None, f"{body.kind} {other.key}")
    session.commit()
    return {"id": exists.id}


@router.delete("/tasks/{key}/links/{link_id}", status_code=204)
def delete_link(key: str, link_id: int, principal: Viewer, session: SessionDep) -> None:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "link", task)
    row = session.get(TaskLink, link_id)
    if row is None or task.id not in (row.from_task_id, row.to_task_id):
        raise HTTPException(404, "không thấy liên kết")
    ts.log(session, task, principal.user_id, "link", row.kind, None)
    session.delete(row)
    session.commit()


@router.put("/tasks/{key}/watch")
def set_watch(key: str, body: WatchIn, principal: Viewer, session: SessionDep) -> dict[str, bool]:
    task = ts.task_or_404(session, principal, key)
    row = session.get(TaskWatcher, (task.id, principal.user_id))
    if body.watching and row is None:
        session.add(TaskWatcher(task_id=task.id, user_id=principal.user_id))
    elif not body.watching and row is not None:
        session.delete(row)
    session.commit()
    return {"watching": body.watching}


@router.post("/tasks/{key}/worklogs", status_code=201)
def add_worklog(
    key: str, body: WorklogIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "worklog", task)
    row = TaskWorklog(
        task_id=task.id,
        user_id=principal.user_id,
        minutes=body.minutes,
        day=body.day or datetime.now(UTC).date(),
        note=body.note,
    )
    session.add(row)
    task.spent_min = (task.spent_min or 0) + body.minutes
    ts.log(session, task, principal.user_id, "worklog", None, f"{body.minutes} phút")
    session.commit()
    return {"id": row.id, "spent_min": task.spent_min}


@router.delete("/tasks/{key}/worklogs/{worklog_id}", status_code=204)
def delete_worklog(key: str, worklog_id: int, principal: Viewer, session: SessionDep) -> None:
    task = ts.task_or_404(session, principal, key)
    row = session.get(TaskWorklog, worklog_id)
    if row is None or row.task_id != task.id:
        raise HTTPException(404, "không thấy giờ làm")
    if row.user_id != principal.user_id and not ts.may(principal, "moderate", task):
        raise HTTPException(403, "chỉ người ghi (hoặc người có quyền kiểm duyệt) xoá được")
    task.spent_min = max((task.spent_min or 0) - row.minutes, 0)
    session.delete(row)
    session.commit()


@router.post("/tasks/{key}/attachments", status_code=201)
async def upload(
    key: str,
    request: Request,
    principal: Viewer,
    session: SessionDep,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict[str, Any]:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise HTTPException(413, "file lớn hơn 20 MB")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, "file lớn hơn 20 MB")

    def store() -> dict[str, Any]:
        task = ts.task_or_404(session, principal, key)
        ts.require(principal, "attach", task)
        used = session.scalar(
            select(func.coalesce(func.sum(TaskAttachment.size), 0))
            .join(Task, Task.id == TaskAttachment.task_id)
            .where(Task.business_project_id == task.business_project_id)
        )
        if int(used or 0) + len(data) > SPACE_FILES_BYTES:
            raise HTTPException(413, "dự án đã dùng hết 2 GB đính kèm")
        name = Path(filename).name.strip()[:255] or "file"
        rel = f"{task.business_project_id}/{uuid.uuid4().hex}"
        path = files_dir() / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(data))
        row = TaskAttachment(
            task_id=task.id,
            name=name,
            content_type=_content_type(request.headers.get("content-type"), name),
            size=len(data),
            path=rel,
            uploaded_by=principal.user_id,
        )
        session.add(row)
        ts.log(session, task, principal.user_id, "attachment", None, name)
        session.commit()
        return {
            "id": row.id,
            "name": row.name,
            "size": row.size,
            "content_type": row.content_type,
            "url": f"/api/v1/tasks/attachments/{row.id}",
        }

    return await run_in_threadpool(store)


@router.get("/tasks/{key}/commit-candidates")
def commit_candidates(
    key: str, principal: Viewer, session: SessionDep, q: str | None = None
) -> list[dict[str, Any]]:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "link", task)
    return task_git.candidates(session, task, q)


@router.post("/tasks/{key}/git", status_code=201)
def add_git(key: str, body: GitIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "link", task)
    if body.kind == "commit":
        if not body.sha:
            raise HTTPException(422, "cần sha")
        row = task_git.link_commit_by_hand(
            session, task, body.sha, body.relation, principal.user_id
        )
    else:
        if body.project_id is None or body.mr_iid is None:
            raise HTTPException(422, "cần project_id và mr_iid")
        row = task_git.link_mr_by_hand(
            session, task, body.project_id, body.mr_iid, body.relation, principal.user_id
        )
    audit(session, principal.user_id, "task.git.link", f"task:{task.key}", kind=body.kind)
    session.commit()
    return {"id": row.id}


@router.delete("/tasks/{key}/git/{link_id}", status_code=204)
def delete_git(key: str, link_id: int, principal: Viewer, session: SessionDep) -> None:
    task = ts.task_or_404(session, principal, key)
    row = session.get(TaskGitLink, link_id)
    if row is None or row.task_id != task.id:
        raise HTTPException(404, "không thấy liên kết")
    manage = ts.can_manage_space(principal, task.business_project_id)
    if not manage and not (row.manual and ts.may(principal, "link", task)):
        raise HTTPException(403, "chỉ gỡ được liên kết tay (hoặc cần quyền quản lý Task)")
    ts.log(
        session, task, principal.user_id, row.kind, row.sha or row.branch or f"!{row.mr_iid}", None
    )
    session.delete(row)
    session.commit()


@router.post("/tasks/{key}/branch", status_code=201)
def create_branch(
    key: str, body: BranchIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    task = ts.task_or_404(session, principal, key)
    ts.require(principal, "link", task)
    project = session.get(Project, body.project_id)
    if project is None:
        raise HTTPException(404, "không thấy repo")
    row = task_git.create_branch(session, task, project, body.from_ref, principal.user_id)
    audit(session, principal.user_id, "task.branch", f"task:{task.key}", branch=row.branch)
    session.commit()
    return {"id": row.id, "branch": row.branch}


@router.post("/tasks/{key}/ai", status_code=202)
def start_ai(
    key: str,
    body: AiIn,
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
) -> dict[str, Any]:
    from app.admin_settings import monthly_budget_usd
    from app.workers.knowledge_jobs import month_spend_usd

    task = ts.task_or_404(session, principal, key)
    ts.require_edit(principal, task)
    busy = session.scalar(
        select(func.count())
        .select_from(TaskAiRun)
        .where(
            TaskAiRun.requested_by == principal.user_id,
            TaskAiRun.status.in_(("queued", "running")),
            TaskAiRun.requested_at > datetime.now(UTC) - timedelta(minutes=15),
        )
    )
    if busy and busy >= 2:
        raise HTTPException(429, "đang có 2 lượt AI chạy; đợi xong rồi thử tiếp")
    budget = monthly_budget_usd(session, settings)
    if budget > 0 and month_spend_usd(session) >= budget:
        raise HTTPException(409, "đã dùng hết ngân sách LLM của tháng")
    row = TaskAiRun(
        task_id=task.id, kind=body.kind, input={"hint": body.hint}, requested_by=principal.user_id
    )
    session.add(row)
    session.commit()
    try:
        review_queue(queue, asked=True, bp_id=task.business_project_id).enqueue(
            "app.tasks.ai.task_ai_job",
            row.id,
            job_id=f"task-ai-{row.id}",
            job_timeout=task_ai.JOB_TIMEOUT_S,
        )
    except Exception:
        row.status, row.error = "failed", "Không xếp hàng được; thử lại."
        session.commit()
        raise HTTPException(503, "could not queue the AI run") from None
    return {"run_id": row.id}
