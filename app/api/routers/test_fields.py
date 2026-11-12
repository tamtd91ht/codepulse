"""Custom fields of cases and results (docs/specs/30 §15.8, §15.11, G5).

- `GET /testcases/fields?business_project_id=&entity=case|result&archived=false` (tests.view):
  the fields in order; `archived=true` lists the archived ones too.
- `POST /testcases/fields` (tests.manage) -> 201; `PATCH /testcases/fields/{id}` (anything but
  key, entity and type); `DELETE /testcases/fields/{id}` archives (204, values kept);
  `POST /testcases/fields/{id}/restore`; `PUT /testcases/fields/order {business_project_id,
  entity, ids}`.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.models import TestField, TestSuite
from app.testcases import fields as tf
from app.testcases import service as tc

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
Entity = Literal["case", "result"]
FieldType = Literal[
    "text", "textarea", "number", "date", "dropdown", "multiselect", "checkbox", "user", "url"
]
Template = Literal["text", "steps"]


class FieldOut(BaseModel):
    id: int
    key: str
    name: str
    entity: str
    type: str
    options: list[str]
    required: bool
    default: Any
    suite_ids: list[int]
    templates: list[str]
    position: int
    archived_at: str | None


class FieldIn(BaseModel):
    business_project_id: int
    entity: Entity
    name: str = Field(min_length=1, max_length=tf.NAME_MAX)
    type: FieldType
    options: list[str] = Field(default_factory=list, max_length=tf.OPTIONS_MAX)
    required: bool = False
    default: Any = None
    suite_ids: list[int] = Field(default_factory=list, max_length=200)
    templates: list[Template] = Field(default_factory=list, max_length=2)


class FieldPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=tf.NAME_MAX)
    options: list[str] | None = Field(default=None, max_length=tf.OPTIONS_MAX)
    required: bool | None = None
    default: Any = None  # given (even null): set
    suite_ids: list[int] | None = Field(default=None, max_length=200)
    templates: list[Template] | None = Field(default=None, max_length=2)
    position: int | None = Field(default=None, ge=0, le=10_000)


class OrderIn(BaseModel):
    business_project_id: int
    entity: Entity
    ids: list[int] = Field(max_length=tf.FIELDS_MAX)


def _out(row: TestField) -> FieldOut:
    return FieldOut(
        id=row.id,
        key=row.key,
        name=row.name,
        entity=row.entity,
        type=row.type,
        options=list(row.options or []),
        required=bool(row.required),
        default=row.default,
        suite_ids=list(row.suite_ids or []),
        templates=list(row.templates or []),
        position=row.position,
        archived_at=row.archived_at.isoformat() if row.archived_at else None,
    )


def _field_for(session: Session, principal: Principal, field_id: int) -> TestField:
    row = session.get(TestField, field_id)
    if row is None or not tc.may_view(principal, row.business_project_id):
        raise HTTPException(404, "trường không tồn tại")
    if not tc.may_manage(principal, row.business_project_id):
        raise HTTPException(403, "bạn không có quyền quản lý trường tuỳ biến của dự án này")
    return row


def _suite_ids(session: Session, bp_id: int, ids: list[int]) -> list[int]:
    """Suites of the dự án (made there: a shared suite uses its home dự án's fields)."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    found = set(
        session.scalars(
            select(TestSuite.id).where(
                TestSuite.id.in_(wanted), TestSuite.business_project_id == bp_id
            )
        )
    )
    if len(found) != len(wanted):
        raise HTTPException(422, "bộ testcase không thuộc dự án này")
    return wanted


def _default(session: Session, row: TestField, raw: Any) -> Any:
    try:
        return tf.value_of(session, row, raw)
    except tc.FieldError as exc:
        raise HTTPException(422, f"giá trị mặc định: {exc}") from None


def _name_taken(session: Session, row: TestField, name: str) -> bool:
    return any(
        f.id != row.id and f.name.casefold() == name.casefold()
        for f in tf.rows(session, row.business_project_id, row.entity, archived=None)
    )


@router.get("/testcases/fields", response_model=list[FieldOut])
def list_fields(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    entity: Entity | None = None,
    archived: bool = False,
) -> list[FieldOut]:
    """Live fields in order; `archived=true`: archived ones too."""
    bp = tc.project_for(session, principal, business_project_id)
    out: list[TestField] = []
    for kind in (entity,) if entity else ("case", "result"):
        out += tf.rows(session, bp.id, kind, archived=None if archived else False)
    return [_out(r) for r in out]


@router.post("/testcases/fields", response_model=FieldOut, status_code=201)
def create_field(body: FieldIn, principal: CurrentPrincipal, session: SessionDep) -> FieldOut:
    bp = tc.project_for(session, principal, body.business_project_id, manage=True)
    existing = tf.rows(session, bp.id, body.entity, archived=None)
    if len(existing) >= tf.FIELDS_MAX:
        raise HTTPException(422, f"một dự án có tối đa {tf.FIELDS_MAX} trường cho mỗi loại")
    try:
        name = tf.clean_name(body.name)
        options = tf.clean_options(body.type, body.options)
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None
    row = TestField(
        business_project_id=bp.id,
        entity=body.entity,
        key=tf.unique_key(session, bp.id, body.entity, name),
        name=name,
        type=body.type,
        options=options,
        required=body.required,
        suite_ids=_suite_ids(session, bp.id, body.suite_ids),
        templates=list(dict.fromkeys(body.templates)),
        position=max((f.position for f in existing), default=0) + 1,
        created_by=principal.user_id,
    )
    if _name_taken(session, row, name):
        raise HTTPException(409, f"đã có trường tên “{name}”")
    row.default = _default(session, row, body.default)
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "trường vừa được tạo trùng mã; thử lại") from None
    audit(
        session,
        principal.user_id,
        "testfield.create",
        f"test_field:{row.id}",
        key=row.key,
        entity=row.entity,
        type=row.type,
    )
    session.commit()
    return _out(row)


@router.patch("/testcases/fields/{field_id:int}", response_model=FieldOut)
def update_field(
    field_id: int, body: FieldPatch, principal: CurrentPrincipal, session: SessionDep
) -> FieldOut:
    row = _field_for(session, principal, field_id)
    try:
        if body.name is not None:
            name = tf.clean_name(body.name)
            if _name_taken(session, row, name):
                raise HTTPException(409, f"đã có trường tên “{name}”")
            row.name = name
        if body.options is not None:
            row.options = tf.clean_options(row.type, body.options)
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.required is not None:
        row.required = body.required
    if body.suite_ids is not None:
        row.suite_ids = _suite_ids(session, row.business_project_id, body.suite_ids)
    if body.templates is not None:
        row.templates = list(dict.fromkeys(body.templates))
    if body.position is not None:
        row.position = body.position
    if "default" in body.model_fields_set:
        row.default = _default(session, row, body.default)
    elif body.options is not None and row.default is not None:
        # Options changed: a default no longer among them is dropped.
        try:
            row.default = tf.value_of(session, row, row.default)
        except tc.FieldError:
            row.default = None
    changed = body.model_dump(exclude_unset=True)
    audit(
        session,
        principal.user_id,
        "testfield.update",
        f"test_field:{row.id}",
        fields=sorted(changed),
    )
    session.commit()
    return _out(row)


@router.delete("/testcases/fields/{field_id:int}", status_code=204)
def archive_field(field_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    """Archive: hidden from forms, filters and columns; the values stay."""
    row = _field_for(session, principal, field_id)
    if row.archived_at is None:
        row.archived_at = datetime.now(UTC)
        audit(session, principal.user_id, "testfield.archive", f"test_field:{row.id}", key=row.key)
    session.commit()


@router.post("/testcases/fields/{field_id:int}/restore", response_model=FieldOut)
def restore_field(field_id: int, principal: CurrentPrincipal, session: SessionDep) -> FieldOut:
    row = _field_for(session, principal, field_id)
    if row.archived_at is not None:
        row.archived_at = None
        audit(session, principal.user_id, "testfield.restore", f"test_field:{row.id}", key=row.key)
    session.commit()
    return _out(row)


@router.put("/testcases/fields/order", response_model=list[FieldOut])
def order_fields(body: OrderIn, principal: CurrentPrincipal, session: SessionDep) -> list[FieldOut]:
    """`ids` first in that order, the fields not listed after them as they were."""
    bp = tc.project_for(session, principal, body.business_project_id, manage=True)
    rows = tf.rows(session, bp.id, body.entity, archived=None)
    by_id = {r.id: r for r in rows}
    wanted = list(dict.fromkeys(body.ids))
    if any(i not in by_id for i in wanted):
        raise HTTPException(422, "có trường không thuộc dự án này")
    rest = [r for r in rows if r.id not in set(wanted)]
    for position, row in enumerate([*(by_id[i] for i in wanted), *rest], start=1):
        row.position = position
    audit(
        session,
        principal.user_id,
        "testfield.order",
        f"business_project:{bp.id}",
        entity=body.entity,
        ids=wanted,
    )
    session.commit()
    return [_out(r) for r in tf.rows(session, bp.id, body.entity, archived=None)]
