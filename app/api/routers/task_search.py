"""Find anything (docs/specs/31 §21.4): `POST /tasks/search` (a condition tree), the quick
`GET /tasks/search?q=` of the command palette, CSV of a filter, and saved filters with a scope,
a Board pin and a digest subscription. Registered before `tasks`: `/tasks/search` is no key."""

import csv
import io
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.routers.tasks import Viewer
from app.api.security import Principal
from app.models import Sprint, Task, TaskField, TaskFilter, TaskStatus
from app.tasks import search as sx
from app.tasks import service as ts

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]

Sort = Literal["rank", "updated", "created", "priority", "due", "key", "title"]
Order = Literal["asc", "desc"]
Scope = Literal["me", "team", "project"]
EXPORT_MAX = 5000
CSV_COLUMNS = (
    "key",
    "title",
    "type",
    "status",
    "priority",
    "assignee",
    "sprint",
    "due",
    "points",
    "labels",
    "resolution",
)


class SearchIn(BaseModel):
    space: list[int] = Field(default_factory=list, max_length=50)
    tree: dict[str, Any] | None = None
    # A saved filter's tree instead of `tree` (both: they are ANDed).
    filter_id: int | None = None
    sort: Sort = "rank"
    order: Order = "asc"
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class Subscription(BaseModel):
    cadence: Literal["daily", "weekly"]


class SavedIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    space: int | None = None
    tree: dict[str, Any] | None = None
    query: dict[str, Any] = Field(default_factory=dict)
    shared_scope: Scope = "me"
    pinned: bool = False
    subscription: Subscription | None = None


class SavedPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    tree: dict[str, Any] | None = None
    query: dict[str, Any] | None = None
    shared_scope: Scope | None = None
    pinned: bool | None = None
    subscription: Subscription | None = None
    # true: drop the subscription (`subscription: null` cannot be told from "not sent").
    unsubscribe: bool = False


def _saved_or_404(session: Session, principal: Principal, filter_id: int) -> TaskFilter:
    row = session.get(TaskFilter, filter_id)
    if row is None or row not in sx.visible_filters(session, principal, row.business_project_id):
        raise HTTPException(404, "không thấy bộ lọc")
    return row


def _tree(session: Session, principal: Principal, body: SearchIn) -> dict[str, Any]:
    tree = sx.clean_tree(body.tree)
    if body.filter_id is not None:
        row = _saved_or_404(session, principal, body.filter_id)
        tree = {"all": [*tree["all"], sx.tree_of(row)]}
        if row.business_project_id is not None:  # a dự án's filter stays on that dự án
            tree["all"].append(
                {"field": "space", "op": "is", "value": [str(row.business_project_id)]}
            )
    return tree


def _run(
    session: Session, principal: Principal, body: SearchIn, tree: dict[str, Any]
) -> tuple[list[Task], int]:
    for bp in body.space:
        ts.space_or_404(session, principal, bp)
    return sx.search(
        session,
        principal,
        tree,
        spaces=body.space,
        sort=body.sort,
        order=body.order,
        limit=body.limit,
        offset=body.offset,
    )


@router.post("/tasks/search")
def search(body: SearchIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """Tasks matching a condition tree, paged and sorted like the List; same visibility."""
    tree = _tree(session, principal, body)
    tasks, total = _run(session, principal, body, tree)
    return {"items": ts.rows(session, tasks), "total": total, "tree": tree}


class FacetsIn(BaseModel):
    space: list[int] = Field(default_factory=list, max_length=50)
    tree: dict[str, Any] | None = None
    fields: list[str] = Field(min_length=1, max_length=len(sx.FACETS))


@router.post("/tasks/search/facets")
def facets(body: FacetsIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """The FilterBar's counts (docs/specs/31 §22.2): per field, tasks matching the tree per
    value. The caller leaves the counted field's own chips out of the tree."""
    unknown = [f for f in body.fields if f not in sx.FACETS]
    if unknown:
        raise HTTPException(422, f"không đếm được trường: {', '.join(unknown)}")
    for bp in body.space:
        ts.space_or_404(session, principal, bp)
    counts = sx.facets(
        session, principal, sx.clean_tree(body.tree), spaces=body.space, fields=body.fields
    )
    return {"counts": counts}


@router.get("/tasks/search")
def quick_search(
    principal: Viewer,
    session: SessionDep,
    q: Annotated[str, Query(max_length=200)] = "",
    space: Annotated[list[int], Query()] = [],  # noqa: B006 - FastAPI query lists
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """The command palette: by key, title, then description and comments; accents optional."""
    return {"items": sx.quick(session, principal, q, space, limit)}


@router.get("/tasks/search/fields")
def fields(principal: Viewer, session: SessionDep, space: int) -> dict[str, Any]:
    """What the chip bar offers: each field's kind and operators, plus the dự án's custom
    fields (`custom:<id>`)."""
    ts.space_or_404(session, principal, space)
    custom = [
        {
            "field": f"custom:{f.id}",
            "name": f.name,
            "kind": sx.CUSTOM_KINDS[f.kind],
            "custom_kind": f.kind,
            "options": list(f.options or []),
            "ops": list(sx.KIND_OPS[sx.CUSTOM_KINDS[f.kind]]),
        }
        for f in session.scalars(
            select(TaskField)
            .where(TaskField.business_project_id == space, TaskField.archived.is_(False))
            .order_by(TaskField.position, TaskField.id)
        )
    ]
    return {
        "fields": [
            {"field": name, "kind": kind, "ops": list(sx.KIND_OPS[kind])}
            for name, kind in sx.FIELDS.items()
        ],
        "custom": custom,
    }


# -- CSV -------------------------------------------------------------------------------------------


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    # A spreadsheet runs a cell starting with these as a formula.
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(session: Session, tasks: list[Task]) -> Response:
    statuses = {
        s.id: s.name
        for s in session.scalars(
            select(TaskStatus).where(TaskStatus.id.in_({t.status_id for t in tasks}))
        )
    }
    sprint_ids = {t.sprint_id for t in tasks if t.sprint_id}
    sprints = (
        {s.id: s.name for s in session.scalars(select(Sprint).where(Sprint.id.in_(sprint_ids)))}
        if sprint_ids
        else {}
    )
    users = ts.users_by_id(session, [t.assignee_id for t in tasks])
    prio = ts.priority_meta(session, {t.business_project_id for t in tasks})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for t in tasks:
        level = prio.get((t.business_project_id, t.priority))
        who = users.get(t.assignee_id) if t.assignee_id else None
        writer.writerow(
            [
                _cell(v)
                for v in (
                    t.key,
                    t.title,
                    t.type,
                    statuses.get(t.status_id, ""),
                    level.name if level else t.priority,
                    (who.full_name or who.primary_email) if who else "",
                    sprints.get(t.sprint_id, "") if t.sprint_id else "",
                    t.due_date.isoformat() if t.due_date else "",
                    "" if t.story_points is None else f"{t.story_points:g}",
                    ", ".join(t.labels or []),
                    t.resolution or "",
                )
            ]
        )
    return Response(
        content="﻿" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tasks.csv"'},
    )


def _export(session: Session, principal: Principal, body: SearchIn) -> Response:
    body = body.model_copy(update={"limit": EXPORT_MAX, "offset": 0})
    tree = _tree(session, principal, body)
    for bp in body.space:
        ts.space_or_404(session, principal, bp)
    tasks, _total = sx.search(
        session,
        principal,
        tree,
        spaces=body.space,
        sort=body.sort,
        order=body.order,
        limit=EXPORT_MAX,
    )
    return _csv(session, tasks)


@router.post("/tasks/search/export.csv")
def export_post(body: SearchIn, principal: Viewer, session: SessionDep) -> Response:
    """The tasks of a filter as CSV (UTF-8 with BOM, opens in Excel), at most 5000."""
    return _export(session, principal, body)


@router.get("/tasks/search/export.csv")
def export_get(
    principal: Viewer,
    session: SessionDep,
    space: Annotated[list[int], Query()] = [],  # noqa: B006
    tree: Annotated[str | None, Query(max_length=20000)] = None,
    filter: int | None = None,
    sort: Sort = "rank",
    order: Order = "asc",
) -> Response:
    """As the POST, for a download link: `tree` is the JSON of the tree."""
    try:
        parsed = json.loads(tree) if tree else None
    except ValueError:
        raise HTTPException(422, "tree phải là JSON") from None
    if parsed is not None and not isinstance(parsed, dict):
        raise HTTPException(422, "tree phải là JSON object")
    body = SearchIn(space=space, tree=parsed, filter_id=filter, sort=sort, order=order)
    return _export(session, principal, body)


# -- saved filters ---------------------------------------------------------------------------------


def _saved_out(session: Session, f: TaskFilter, principal: Principal) -> dict[str, Any]:
    mine = f.user_id == principal.user_id
    sub = f.subscription or None
    owner = ts.person(ts.users_by_id(session, [f.user_id]).get(f.user_id))
    out: dict[str, Any] = {
        "id": f.id,
        "name": f.name,
        "space": f.business_project_id,
        "query": f.query or {},
        "tree": sx.tree_of(f),
        "shared": f.shared,
        "shared_scope": sx.scope_of(f),
        "pinned": f.pinned,
        "subscription": {"cadence": sub.get("cadence")} if mine and sub else None,
        "mine": mine,
        "user_id": f.user_id,
        "owner": owner,
        "can_delete": mine,
        "can_edit": mine,
    }
    if mine and sub:
        from app.tasks.automation import telegram_linked

        out["subscription"]["channel"] = (
            "telegram" if telegram_linked(session, principal.user_id) else "inbox"
        )
    return out


@router.get("/tasks/search/filters")
def list_saved(
    principal: Viewer, session: SessionDep, space: int | None = None, pinned: bool = False
) -> list[dict[str, Any]]:
    rows = sx.visible_filters(session, principal, space)
    if pinned:
        rows = [f for f in rows if f.pinned and f.business_project_id is not None]
    return [_saved_out(session, f, principal) for f in rows]


def _check(
    session: Session, principal: Principal, space: int | None, scope: str, pinned: bool
) -> None:
    if space is not None:
        ts.space_or_404(session, principal, space)
    if pinned and space is None:
        raise HTTPException(422, "ghim lên Board cần bộ lọc của một dự án")
    if scope == "team" and not (
        principal.own_teams or principal.lead_teams or principal.managed_teams
    ):
        raise HTTPException(422, "bạn chưa ở team nào để chia sẻ")


@router.post("/tasks/search/filters", status_code=201)
def create_saved(body: SavedIn, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    _check(session, principal, body.space, body.shared_scope, body.pinned)
    tree = sx.clean_tree(body.tree) if body.tree is not None else None
    row = TaskFilter(
        user_id=principal.user_id,
        business_project_id=body.space,
        name=body.name.strip(),
        query=body.query,
        tree=tree,
        shared_scope=body.shared_scope,
        shared=body.shared_scope == "project",
        pinned=body.pinned,
        subscription=body.subscription.model_dump() if body.subscription else None,
    )
    session.add(row)
    session.commit()
    return _saved_out(session, row, principal)


@router.patch("/tasks/search/filters/{filter_id}")
def update_saved(
    filter_id: int, body: SavedPatch, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    row = session.get(TaskFilter, filter_id)
    if row is None or row.user_id != principal.user_id:
        raise HTTPException(404, "không thấy bộ lọc")
    scope = body.shared_scope or sx.scope_of(row)
    pinned = row.pinned if body.pinned is None else body.pinned
    _check(session, principal, row.business_project_id, scope, pinned)
    if body.name is not None:
        row.name = body.name.strip()
    if body.tree is not None:
        row.tree = sx.clean_tree(body.tree)
    if body.query is not None:
        row.query = body.query
    row.shared_scope = scope
    row.shared = scope == "project"
    row.pinned = pinned
    if body.unsubscribe:
        row.subscription = None
    elif body.subscription is not None:
        last = (row.subscription or {}).get("last_sent")
        row.subscription = {**body.subscription.model_dump(), "last_sent": last}
    session.commit()
    return _saved_out(session, row, principal)
