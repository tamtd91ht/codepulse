"""The testcase audit trail: list and export (docs/specs/30 §15.14, §15.16, G12).

`GET /testcases/audit`: the trail newest first, filtered (dự án, person, entity, action, target,
text, dates), paged by `before_id`. `GET /testcases/audit/facets`: what the filters can choose.
`GET /testcases/audit/export?format=xlsx|csv`: the same filters, at most 50 000 rows.

Who: `tests.manage` on the dự án. An admin may leave the dự án out and see every row, the global
ones included (permission sets, tokens, the older admin actions); anyone else without a dự án
sees the dự án they manage. Rows written by `app/audit_trail.py` carry `{entity, id, op,
changes}`; older hand-written rows show `changes: []` and their details as they are.
"""

import csv
import io
import json
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from tempfile import SpooledTemporaryFile
from typing import IO, Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Row, String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.config import get_settings
from app.models import AuditLog, User
from app.testcases import service as tc

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]

PAGE_MAX = 200
EXPORT_MAX = 50_000
EXPORT_CHUNK = 2_000  # rows read from the database at a time
SPOOL_MAX = 8 * 1024 * 1024  # an export bigger than this goes to a temporary file on disk
STREAM_BLOCK = 64 * 1024
CELL_MAX = 32_000  # an Excel cell holds 32 767 characters
ENTITY = re.compile(r"^[a-z_]{1,40}$")

ProjectId = Annotated[int | None, Query()]
ActorId = Annotated[int | None, Query()]
EntityQ = Annotated[str | None, Query(max_length=40)]
ActionQ = Annotated[str | None, Query(max_length=120)]
TargetQ = Annotated[str | None, Query(max_length=200)]
TextQ = Annotated[str | None, Query(max_length=200)]
DateFrom = Annotated[date | None, Query(alias="from")]
DateTo = Annotated[date | None, Query(alias="to")]


class Change(BaseModel):
    field: str
    before: Any = None
    after: Any = None


class AuditRow(BaseModel):
    id: int
    at: datetime
    actor_id: int | None
    actor_name: str | None
    action: str
    entity: str | None
    entity_id: Any = None
    target: str | None
    business_project_id: int | None
    changes: list[Change]
    details: dict[str, Any] | None
    request_id: str | None


class AuditPage(BaseModel):
    items: list[AuditRow]
    # Pass as `before_id` for the next (older) page; null: no more rows.
    next_before_id: int | None


class Actor(BaseModel):
    id: int
    name: str | None


class AuditFacets(BaseModel):
    actions: list[str]
    entities: list[str]
    actors: list[Actor]


# -- filters --------------------------------------------------------------------------------------


def _scope(session: Session, principal: Principal, bp_id: int | None) -> list[ColumnElement[bool]]:
    if bp_id is not None:
        tc.project_for(session, principal, bp_id, manage=True)
        return [AuditLog.business_project_id == bp_id]
    if principal.is_admin and principal.token_project is None:
        return []
    managed = [
        bp.id for bp in tc.viewable_projects(session, principal) if tc.may_manage(principal, bp.id)
    ]
    if not managed:
        raise HTTPException(403, "bạn không có quyền xem nhật ký thay đổi testcase")
    return [AuditLog.business_project_id.in_(managed)]


def _like(text: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", text) + "%"


def _filters(
    session: Session,
    principal: Principal,
    bp_id: int | None,
    actor_id: int | None,
    entity: str | None,
    action: str | None,
    target: str | None,
    q: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[ColumnElement[bool]]:
    conds = _scope(session, principal, bp_id)
    if actor_id is not None:
        conds.append(AuditLog.actor_user_id == actor_id)
    if entity:
        if not ENTITY.match(entity):
            raise HTTPException(422, "đối tượng không hợp lệ")
        conds.append(AuditLog.target.like(f"{entity}:%"))
    if action:
        conds.append(AuditLog.action == action.strip())
    if target:
        conds.append(AuditLog.target == target.strip())
    if q and q.strip():
        pattern = _like(q.strip())
        conds.append(
            or_(
                AuditLog.action.ilike(pattern),
                AuditLog.target.ilike(pattern),
                cast(AuditLog.details, String).ilike(pattern),
            )
        )
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "ngày bắt đầu sau ngày kết thúc")
    tz = ZoneInfo(get_settings().report_tz)
    if date_from:
        conds.append(AuditLog.at >= datetime.combine(date_from, time(), tz).astimezone(UTC))
    if date_to:
        end = datetime.combine(date_to + timedelta(days=1), time(), tz).astimezone(UTC)
        conds.append(AuditLog.at < end)
    return conds


def _rows(
    session: Session, conds: list[ColumnElement[bool]], limit: int, before_id: int | None = None
) -> list[AuditRow]:
    query = (
        select(AuditLog, User.full_name, User.primary_email)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(*conds)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    return [_out(row, name or email) for row, name, email in session.execute(query)]


_COLUMNS = (
    AuditLog.id,
    AuditLog.at,
    AuditLog.actor_user_id,
    AuditLog.action,
    AuditLog.target,
    AuditLog.business_project_id,
    AuditLog.details,
    AuditLog.request_id,
)


def _chunks(
    session: Session, conds: list[ColumnElement[bool]], total: int
) -> Iterator[list[AuditRow]]:
    """At most `total` rows, newest first, `EXPORT_CHUNK` at a time (paged by id, plain columns:
    nothing piles up in the session's identity map)."""
    before_id: int | None = None
    left = total
    while left > 0:
        size = min(EXPORT_CHUNK, left)
        query = (
            select(*_COLUMNS, User.full_name, User.primary_email)
            .outerjoin(User, User.id == AuditLog.actor_user_id)
            .where(*conds)
            .order_by(AuditLog.id.desc())
            .limit(size)
        )
        if before_id is not None:
            query = query.where(AuditLog.id < before_id)
        chunk = [_out(row, row.full_name or row.primary_email) for row in session.execute(query)]
        if not chunk:
            return
        yield chunk
        if len(chunk) < size:
            return
        left -= len(chunk)
        before_id = chunk[-1].id


def _out(row: AuditLog | Row[Any], actor_name: str | None) -> AuditRow:
    details = row.details if isinstance(row.details, dict) else None
    entity, entity_id = None, None
    if row.target and ":" in row.target:
        entity, entity_id = row.target.split(":", 1)
    changes: list[Change] = []
    if details is not None and isinstance(details.get("changes"), list) and "entity" in details:
        # A row of the audit trail (app/audit_trail.py).
        entity, entity_id = str(details["entity"]), details.get("id")
        changes = [
            Change(field=str(c.get("field")), before=c.get("before"), after=c.get("after"))
            for c in details["changes"]
            if isinstance(c, dict)
        ]
        details = {k: v for k, v in details.items() if k not in ("entity", "id", "changes")}
    return AuditRow(
        id=row.id,
        at=row.at,
        actor_id=row.actor_user_id,
        actor_name=actor_name,
        action=row.action,
        entity=entity,
        entity_id=entity_id,
        target=row.target,
        business_project_id=row.business_project_id,
        changes=changes,
        details=details,
        request_id=row.request_id,
    )


# -- endpoints ------------------------------------------------------------------------------------


@router.get("/testcases/audit")
def audit_list(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: ProjectId = None,
    actor_id: ActorId = None,
    entity: EntityQ = None,
    action: ActionQ = None,
    target: TargetQ = None,
    q: TextQ = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=PAGE_MAX)] = 50,
) -> AuditPage:
    conds = _filters(
        session,
        principal,
        business_project_id,
        actor_id,
        entity,
        action,
        target,
        q,
        date_from,
        date_to,
    )
    items = _rows(session, conds, limit + 1, before_id)
    more = len(items) > limit
    items = items[:limit]
    return AuditPage(items=items, next_before_id=items[-1].id if more and items else None)


@router.get("/testcases/audit/facets")
def audit_facets(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: ProjectId = None,
) -> AuditFacets:
    conds = _scope(session, principal, business_project_id)
    actions = session.scalars(
        select(AuditLog.action).where(*conds).distinct().order_by(AuditLog.action)
    ).all()
    kind = func.split_part(AuditLog.target, ":", 1)
    entities = session.scalars(
        select(kind).where(*conds, AuditLog.target.like("%:%")).distinct().order_by(kind)
    ).all()
    people = session.execute(
        select(User.id, User.full_name, User.primary_email)
        .where(User.id.in_(select(AuditLog.actor_user_id).where(*conds).distinct()))
        .order_by(User.full_name, User.id)
    )
    return AuditFacets(
        actions=[str(a) for a in actions],
        entities=[str(e) for e in entities if e],
        actors=[Actor(id=uid, name=name or email) for uid, name, email in people],
    )


HEADERS = (
    "ID",
    "Thời gian",
    "Người làm",
    "Hành động",
    "Đối tượng",
    "Mã đối tượng",
    "Target",
    "Dự án",
    "Thay đổi (trường: trước -> sau)",
    "Chi tiết",
    "Request ID",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _cells(row: AuditRow, tz: ZoneInfo) -> list[str]:
    changes = "\n".join(f"{c.field}: {_text(c.before)} -> {_text(c.after)}" for c in row.changes)
    cells = [
        str(row.id),
        row.at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
        row.actor_name or "",
        row.action,
        row.entity or "",
        _text(row.entity_id),
        row.target or "",
        _text(row.business_project_id),
        changes,
        _text(row.details) if row.details else "",
        row.request_id or "",
    ]
    return [tc.CONTROL.sub("", c)[:CELL_MAX] for c in cells]


def _safe(text: str) -> str:
    """A CSV cell a spreadsheet will not run as a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _xlsx(rows: Iterable[list[str]], out: IO[bytes]) -> None:
    """A write-only workbook (rows go to a temporary file as they come) saved into `out`."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.cell.cell import TYPE_STRING
    from openpyxl.styles import Font

    book = Workbook(write_only=True)
    ws = book.create_sheet("Nhật ký")
    for i, width in enumerate((8, 20, 22, 26, 16, 14, 22, 8, 60, 40, 18), start=1):
        ws.column_dimensions[chr(64 + i)].width = width
    ws.freeze_panes = "A2"

    def line(values: list[str], bold: bool = False) -> list[Any]:
        out = []
        for value in values:
            cell = WriteOnlyCell(ws, value=value)
            cell.data_type = TYPE_STRING  # text, never a formula
            if bold:
                cell.font = Font(bold=True)
            out.append(cell)
        return out

    ws.append(line(list(HEADERS), bold=True))
    for values in rows:
        ws.append(line(values))
    book.save(out)


def _csv(rows: Iterable[list[str]], out: IO[bytes]) -> None:
    text = io.TextIOWrapper(out, encoding="utf-8-sig", newline="")  # BOM: Excel reads UTF-8
    w = csv.writer(text)
    w.writerow(HEADERS)
    for values in rows:
        w.writerow([_safe(v) for v in values])
    text.flush()
    text.detach()  # `out` stays open


def _stream(out: IO[bytes]) -> Iterator[bytes]:
    try:
        out.seek(0)
        while data := out.read(STREAM_BLOCK):
            yield data
    finally:
        out.close()


@router.get("/testcases/audit/export")
def audit_export(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: ProjectId = None,
    actor_id: ActorId = None,
    entity: EntityQ = None,
    action: ActionQ = None,
    target: TargetQ = None,
    q: TextQ = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    format: Literal["xlsx", "csv"] = "xlsx",
) -> StreamingResponse:
    """The filtered trail, newest first, at most 50 000 rows; every cell is text. Read from the
    database a chunk at a time and written to a spooled temporary file (memory up to
    SPOOL_MAX, then disk), which the response streams."""
    conds = _filters(
        session,
        principal,
        business_project_id,
        actor_id,
        entity,
        action,
        target,
        q,
        date_from,
        date_to,
    )
    tz = ZoneInfo(get_settings().report_tz)
    rows = (_cells(r, tz) for chunk in _chunks(session, conds, EXPORT_MAX) for r in chunk)
    stamp = datetime.now(UTC).astimezone(tz).strftime("%Y%m%d-%H%M")
    out = SpooledTemporaryFile(max_size=SPOOL_MAX, mode="w+b")  # noqa: SIM115 (closed by _stream)
    try:
        if format == "csv":
            _csv(rows, out)
            media = "text/csv; charset=utf-8"
        else:
            _xlsx(rows, out)
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except BaseException:
        out.close()
        raise
    return StreamingResponse(
        _stream(out),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.{format}"'},
    )
