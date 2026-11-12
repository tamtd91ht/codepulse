"""Result statuses of a dự án and files attached to results (docs/specs/30 §15 G6).

- `GET /testcases/statuses?business_project_id=` (tests.view): built-in and custom statuses in
  the order shown. `POST /testcases/statuses`, `PATCH|DELETE /testcases/statuses/{id}`
  (tests.manage): a custom status in use is archived, never deleted (409).
- `POST /testcases/attachments?business_project_id=&filename=` (raw body, at most 10 MB): an
  image (PNG, JPEG, WebP, GIF, by its signature), or a PDF / text / log / zip file (by its
  extension and its first bytes); the same bytes are kept once per dự án. Whoever may record a
  result in the dự án uploads, at most ATTACH_RATE_PER_MINUTE uploads a minute per person (429)
  and ATTACH_PROJECT_MAX_GB stored per dự án (413). `GET /testcases/attachments/{id}`
  (tests.view on its dự án) serves it with `nosniff`, a sandboxing CSP, and as a download
  unless it is an image. Files no result uses are dropped after a day
  (`statuses.gc_attachments`).
"""

import hashlib
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api import security
from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.models import TestAttachment, TestResult, TestRun, TestRunTest, TestStatus
from app.testcases import service as tc
from app.testcases import statuses as tst

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
ColorToken = Annotated[str, Field(pattern=f"^({'|'.join(tst.COLORS)})$")]
ATTACH_MAX_MB = 10
ATTACH_PROJECT_MAX_GB = 2  # every file of a dự án together
ATTACH_PROJECT_MAX_BYTES = ATTACH_PROJECT_MAX_GB * 1024**3
ATTACH_RATE_PER_MINUTE = 60  # uploads per person (or token) per minute
TEXT_EXT = {"txt": "text/plain", "log": "text/plain"}
NAME_MAX = 120


# -- statuses --------------------------------------------------------------------------------------


class StatusOut(BaseModel):
    id: int | None
    key: str
    name: str
    color: str
    done: bool
    builtin: bool
    position: int
    archived: bool


class StatusesOut(BaseModel):
    items: list[StatusOut]
    can_edit: bool


class StatusIn(BaseModel):
    business_project_id: int
    name: str = Field(min_length=1, max_length=tst.NAME_MAX)
    key: str | None = Field(default=None, max_length=tst.KEY_MAX)
    color: ColorToken = "info"
    done: bool = True


class StatusPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=tst.NAME_MAX)
    color: ColorToken | None = None
    done: bool | None = None
    position: int | None = Field(default=None, ge=0, le=10_000)
    archived: bool | None = None


def _outs(session: Session, bp_id: int) -> list[StatusOut]:
    return [StatusOut(**s.out()) for s in tst.statuses(session, bp_id)]


def _name(value: str) -> str:
    out = " ".join(value.split())[: tst.NAME_MAX]
    if not out:
        raise HTTPException(422, "tên không được trống")
    return out


def _status_for(session: Session, principal: Principal, status_id: int) -> TestStatus:
    row = session.get(TestStatus, status_id)
    if row is None or not tc.may_view(principal, row.business_project_id):
        raise HTTPException(404, "trạng thái không tồn tại")
    if not tc.may_manage(principal, row.business_project_id):
        raise HTTPException(403, "bạn không có quyền quản lý test run của dự án này")
    return row


def _used(session: Session, row: TestStatus) -> bool:
    """A test or a result of the dự án carries this status."""
    runs = select(TestRun.id).where(TestRun.business_project_id == row.business_project_id)
    tests = select(TestRunTest.id).where(TestRunTest.run_id.in_(runs))
    return bool(
        session.scalar(
            select(
                or_(
                    exists().where(TestRunTest.run_id.in_(runs), TestRunTest.status == row.key),
                    exists().where(TestResult.test_id.in_(tests), TestResult.status == row.key),
                )
            )
        )
    )


@router.get("/testcases/statuses", response_model=StatusesOut)
def list_statuses(
    principal: CurrentPrincipal, session: SessionDep, business_project_id: int
) -> StatusesOut:
    bp = tc.project_for(session, principal, business_project_id)
    return StatusesOut(items=_outs(session, bp.id), can_edit=tc.may_manage(principal, bp.id))


@router.post("/testcases/statuses", response_model=StatusesOut, status_code=201)
def create_status(body: StatusIn, principal: CurrentPrincipal, session: SessionDep) -> StatusesOut:
    bp = tc.project_for(session, principal, body.business_project_id, manage=True)
    name = _name(body.name)
    key = (body.key or "").strip().lower() or tst.slug(name)
    if not key or not tst.KEY_RE.match(key):
        raise HTTPException(422, "mã trạng thái chỉ gồm chữ thường không dấu, số và “_”")
    if key in tst.RESERVED:
        raise HTTPException(409, f"“{key}” là trạng thái có sẵn")
    rows = tst.custom_rows(session, bp.id)
    if any(r.key == key for r in rows):
        raise HTTPException(409, f"đã có trạng thái mã “{key}”")
    if len(rows) >= tst.CUSTOM_MAX:
        raise HTTPException(422, f"một dự án có tối đa {tst.CUSTOM_MAX} trạng thái tuỳ biến")
    top = max((r.position for r in rows), default=0)
    row = TestStatus(
        business_project_id=bp.id,
        key=key,
        name=name,
        color=body.color,
        done=body.done,
        position=top + 1,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, f"đã có trạng thái mã “{key}”") from None
    audit(session, principal.user_id, "teststatus.create", f"test_status:{row.id}", key=key)
    session.commit()
    return StatusesOut(items=_outs(session, bp.id), can_edit=True)


@router.patch("/testcases/statuses/{status_id:int}", response_model=StatusesOut)
def update_status(
    status_id: int, body: StatusPatch, principal: CurrentPrincipal, session: SessionDep
) -> StatusesOut:
    row = _status_for(session, principal, status_id)
    if body.name is not None:
        row.name = _name(body.name)
    if body.color is not None:
        row.color = body.color
    if body.done is not None:
        row.done = body.done
    if body.position is not None:
        row.position = body.position
    if body.archived is not None:
        row.archived_at = datetime.now(UTC) if body.archived else None
    audit(
        session,
        principal.user_id,
        "teststatus.update",
        f"test_status:{row.id}",
        **body.model_dump(exclude_unset=True),
    )
    session.commit()
    return StatusesOut(items=_outs(session, row.business_project_id), can_edit=True)


@router.delete("/testcases/statuses/{status_id:int}", status_code=204)
def delete_status(status_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    row = _status_for(session, principal, status_id)
    if _used(session, row):
        raise HTTPException(409, f"“{row.name}” đã dùng trong kết quả: lưu trữ thay vì xoá")
    audit(session, principal.user_id, "teststatus.delete", f"test_status:{row.id}", key=row.key)
    session.delete(row)
    session.commit()


# -- attachments -----------------------------------------------------------------------------------


class AttachmentOut(BaseModel):
    id: int
    name: str
    mime: str
    size: int
    url: str


def _may_attach(session: Session, principal: Principal, bp_id: int) -> bool:
    """Whoever may record a result somewhere in the dự án: testers, managers, or a person a
    test (or a run) of an open run is assigned to."""
    if tst.may_record_any(principal, bp_id, tst.assigned_only(session, bp_id)):
        return True
    if not tc.may_view(principal, bp_id) or principal.user_id is None:
        return False
    me = principal.user_id
    return bool(
        session.scalar(
            select(func.count())
            .select_from(TestRunTest)
            .join(TestRun, TestRun.id == TestRunTest.run_id)
            .where(
                TestRun.business_project_id == bp_id,
                TestRun.completed_at.is_(None),
                or_(TestRunTest.assigned_to == me, TestRun.assigned_to == me),
            )
            .limit(1)
        )
    )


def kind_of(raw: bytes, filename: str) -> str | None:
    """The media type of an attachment, or None when it is not accepted: an image by its
    signature; a PDF, zip, text or log file by its extension and its first bytes."""
    from app.docs.synthesis import image_mime

    image = image_mime(raw)
    if image is not None:
        return image
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf" and raw.startswith(b"%PDF-"):
        return "application/pdf"
    if ext == "zip" and raw[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "application/zip"
    if ext in TEXT_EXT and b"\x00" not in raw:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return TEXT_EXT[ext]
    return None


def _clean_name(filename: str, mime: str) -> str:
    base = unquote(filename).replace("\\", "/").rsplit("/", 1)[-1]
    name = " ".join("".join(ch for ch in base if ch.isprintable()).split())
    name = name.replace('"', "'")[:NAME_MAX]
    if not name:
        name = "anh." + mime.rsplit("/", 1)[-1] if mime.startswith("image/") else "tep"
    return name


def _save(
    session: Session, principal: Principal, bp_id: int, raw: bytes, filename: str
) -> AttachmentOut:
    mime = kind_of(raw, filename)
    if mime is None:
        raise HTTPException(
            422, "chỉ nhận ảnh PNG, JPEG, WebP, GIF hoặc tệp .pdf, .txt, .log, .zip đúng định dạng"
        )
    digest = hashlib.sha256(raw).hexdigest()
    name = _clean_name(filename, mime)

    def find() -> TestAttachment | None:
        return session.scalar(
            select(TestAttachment).where(
                TestAttachment.business_project_id == bp_id, TestAttachment.sha256 == digest
            )
        )

    row = find()
    if row is None:
        stored = session.scalar(
            select(func.coalesce(func.sum(TestAttachment.size), 0)).where(
                TestAttachment.business_project_id == bp_id
            )
        )
        if int(stored or 0) + len(raw) > ATTACH_PROJECT_MAX_BYTES:
            raise HTTPException(
                413, f"dự án đã dùng hết {ATTACH_PROJECT_MAX_GB} GB cho tệp đính kèm"
            )
        row = TestAttachment(
            business_project_id=bp_id,
            name=name,
            mime=mime,
            size=len(raw),
            sha256=digest,
            data=raw,
            uploaded_by=principal.user_id,
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:  # the same file uploaded twice at once
            session.rollback()
            row = find()
            if row is None:
                raise
    return AttachmentOut(
        id=row.id,
        name=row.name,
        mime=row.mime,
        size=row.size,
        url=f"/api/v1/testcases/attachments/{row.id}",
    )


@router.post("/testcases/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    request: Request,
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    filename: Annotated[str, Query(max_length=255)] = "",
) -> AttachmentOut:
    # Per person: a person's tokens share the count with the person.
    who = f"u{principal.user_id}" if principal.user_id is not None else f"t{principal.token_id}"
    wait = security.token_rate.hit(f"cp:attach:rate:{who}", ATTACH_RATE_PER_MINUTE)
    if wait is not None:
        raise HTTPException(
            429,
            f"quá {ATTACH_RATE_PER_MINUTE} lần tải tệp mỗi phút: chờ {wait} giây",
            headers={"Retry-After": str(wait)},
        )

    def allowed() -> None:
        tc.project_for(session, principal, business_project_id)
        if not _may_attach(session, principal, business_project_id):
            raise HTTPException(403, "bạn không ghi kết quả được trong dự án này")
        session.commit()  # nothing held while the body arrives

    await run_in_threadpool(allowed)
    limit = ATTACH_MAX_MB * 1024 * 1024
    too_big = f"tệp lớn hơn {ATTACH_MAX_MB} MB"
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, too_big)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, too_big)
    if not data:
        raise HTTPException(422, "tệp trống")
    return await run_in_threadpool(
        _save, session, principal, business_project_id, bytes(data), filename
    )


@router.get("/testcases/attachments/{attachment_id:int}")
def get_attachment(
    attachment_id: int, principal: CurrentPrincipal, session: SessionDep
) -> Response:
    row = session.get(TestAttachment, attachment_id)
    if row is None or not tc.may_view(principal, row.business_project_id):
        raise HTTPException(404, "tệp không tồn tại")
    data = session.scalar(select(TestAttachment.data).where(TestAttachment.id == row.id))
    inline = row.mime.startswith("image/")
    ascii_name = "".join(ch for ch in row.name if (ch.isascii() and ch.isalnum()) or ch in "._-")
    disposition = (
        f'{"inline" if inline else "attachment"}; filename="{ascii_name or "file"}"; '
        f"filename*=UTF-8''{quote(row.name)}"
    )
    return Response(
        content=data or b"",
        media_type=row.mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": disposition,
            "Cache-Control": "private, max-age=86400, immutable",
        },
    )
