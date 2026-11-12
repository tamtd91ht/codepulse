"""Tổng hợp từ link (docs/specs/29 §12, user 2026-11-12): ask the LLM to write a document of a dự
án from links (a new one, or the current one with the links' facts added / upgraded to the
standard), follow its progress, review the draft, apply it as a version or drop it.

Who: whoever may edit the dự án's documents (`docs.edit`, app/docs/service.py); the drafts of a
dự án are seen by its editors. Limits: DRAFTS_PER_DAY a person (retries count), ACTIVE_PER_USER
at once, and the month's LLM budget.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.orm import Session, undefer
from starlette.concurrency import run_in_threadpool

from app.admin_settings import monthly_budget_usd
from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.routers.project_docs import (
    Category,
    DocDetail,
    Person,
    _convert,
    _detail,
    _file_name,
    _iso,
    _next_position,
    _people,
    _read_body,
)
from app.api.security import CurrentPrincipal, Principal
from app.assistant import importers
from app.config import Settings, get_settings
from app.docs import service as docs
from app.docs import synthesis
from app.models import AuditLog, ProjectDocument, ProjectDocumentDraft, User
from app.workers.knowledge_jobs import month_spend_usd
from app.workers.queues import review_queue

router = APIRouter(tags=["project-docs"])
SessionDep = Annotated[Session, Depends(get_session)]
QueueDep = Annotated[Queue, Depends(get_queue)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
REQUEST_ACTION = "doc.synthesize"
# Applied drafts leave the list after this long (the versions keep the facts); dropped ones at once.
LIST_DAYS = 14


class FileIn(BaseModel):
    """An uploaded file's text, as POST /docs/drafts/extract returned it."""

    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(max_length=10)
    size: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=synthesis.SOURCE_CHARS)
    truncated: bool = False


class FileOut(BaseModel):
    name: str
    kind: str
    size: int
    chars: int  # of the whole file
    truncated: bool  # longer than what one source may send (SOURCE_CHARS)
    text: str
    pages: int | None = None


class DraftIn(BaseModel):
    business_project_id: int
    mode: Literal["new", "merge", "upgrade"]
    document_id: int | None = None
    category: Category | None = None  # "new" only (merge / upgrade keep the document's)
    urls: list[str] = Field(default_factory=list, max_length=synthesis.MAX_LINKS * 2)
    files: list[FileIn] = Field(default_factory=list, max_length=synthesis.MAX_LINKS)
    instruction: str | None = Field(default=None, max_length=synthesis.INSTRUCTION_MAX)


class ApplyIn(BaseModel):
    # What the reviewer kept (edited or not); the draft's own text when left out.
    body: str | None = Field(default=None, min_length=1, max_length=docs.MAX_BODY_CHARS)
    title: str | None = Field(default=None, min_length=1, max_length=docs.TITLE_MAX)
    summary: str | None = Field(default=None, max_length=300)
    category: Category | None = None  # "new" only
    note: str | None = Field(default=None, max_length=docs.NOTE_MAX)


class DraftOut(BaseModel):
    id: int
    business_project_id: int
    document_id: int | None
    document_title: str | None
    mode: str
    category: str
    urls: list[str]
    instruction: str | None
    status: str
    stale: bool  # given up: its job died (it may be asked again)
    error: str | None
    base_version: int | None
    title: str | None
    summary: str | None
    result: dict[str, Any] | None
    sources: list[dict[str, Any]] | None
    files: list[dict[str, Any]]  # name, kind, size, chars (not the text)
    model: str | None
    cost_usd: float | None
    applied_version: int | None
    created_at: str | None
    created_by: Person | None
    finished_at: str | None
    body: str | None = None  # the detail only


class DraftList(BaseModel):
    drafts: list[DraftOut]
    max_links: int
    per_day: int
    used_today: int
    url_hosts: bool  # internal hosts are listed (ASSISTANT_URL_HOSTS)
    file_kinds: list[str]
    max_file_mb: int
    source_chars: int


def _out(
    session: Session, draft: ProjectDocumentDraft, *, body: bool = False, now: datetime
) -> DraftOut:
    people = _people(session, {draft.created_by})
    doc = session.get(ProjectDocument, draft.document_id) if draft.document_id else None
    stale = draft.status in synthesis.ACTIVE and not synthesis.running(draft, now)
    return DraftOut(
        id=draft.id,
        business_project_id=draft.business_project_id,
        document_id=draft.document_id,
        document_title=doc.title if doc else None,
        mode=draft.mode,
        category=draft.category,
        urls=list(draft.urls),
        instruction=draft.instruction,
        status="failed" if stale else draft.status,
        stale=stale,
        error="Quá lâu không xong (tiến trình nền dừng giữa chừng); thử lại."
        if stale
        else draft.error,
        base_version=draft.base_version,
        title=draft.title,
        summary=draft.summary,
        result=draft.result,
        sources=draft.sources,
        files=[
            {
                "name": f.get("name"),
                "kind": f.get("kind"),
                "size": f.get("size"),
                "chars": len(str(f.get("text") or "")),
            }
            for f in (draft.files or [])
        ],
        model=draft.model,
        cost_usd=draft.cost_usd,
        applied_version=draft.applied_version,
        created_at=_iso(draft.created_at),
        created_by=people.get(draft.created_by) if draft.created_by else None,
        finished_at=_iso(draft.finished_at),
        body=draft.body if body else None,
    )


def _draft(
    session: Session, principal: Principal, draft_id: int, *, lock: bool = False
) -> ProjectDocumentDraft:
    query = select(ProjectDocumentDraft).where(ProjectDocumentDraft.id == draft_id)
    if lock:
        query = query.with_for_update()
    draft = session.scalars(query.options(undefer(ProjectDocumentDraft.body))).first()
    if draft is None or not docs.may_view(principal, draft.business_project_id):
        raise HTTPException(404, "draft not found")
    if not docs.may_edit(principal, draft.business_project_id):
        raise HTTPException(403, "bạn không có quyền sửa tài liệu của dự án này")
    return draft


def _used_today(session: Session, user_id: int, now: datetime) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == REQUEST_ACTION,
                AuditLog.actor_user_id == user_id,
                AuditLog.at >= now - timedelta(days=1),
            )
        )
        or 0
    )


def _check_limits(
    session: Session, principal: Principal, settings: Settings, now: datetime
) -> None:
    # One person's requests are counted under a lock on their user row (two tabs at once).
    session.get(User, principal.user_id, with_for_update=True)
    if _used_today(session, principal.user_id, now) >= synthesis.DRAFTS_PER_DAY:
        raise HTTPException(
            429, f"đã dùng {synthesis.DRAFTS_PER_DAY} lần tổng hợp trong 24 giờ; thử lại sau"
        )
    mine = session.scalars(
        select(ProjectDocumentDraft).where(
            ProjectDocumentDraft.created_by == principal.user_id,
            ProjectDocumentDraft.status.in_(synthesis.ACTIVE),
        )
    ).all()
    if sum(1 for d in mine if synthesis.running(d, now)) >= synthesis.ACTIVE_PER_USER:
        raise HTTPException(
            429, f"đang có {synthesis.ACTIVE_PER_USER} bản nháp đang viết; đợi xong rồi thử tiếp"
        )
    budget = monthly_budget_usd(session, settings)
    if budget > 0 and month_spend_usd(session) >= budget:
        raise HTTPException(409, "đã dùng hết ngân sách LLM của tháng")


def _enqueue(session: Session, queue: Queue, draft: ProjectDocumentDraft) -> None:
    try:
        # Someone waits on the screen: served before automatic reviews (app/workers/queues.py).
        review_queue(queue, asked=True).enqueue(
            "app.docs.synthesis.synthesize_job",
            draft.id,
            draft.requested_at.isoformat(),
            job_id=f"doc-draft-{draft.id}-{int(draft.requested_at.timestamp() * 1000)}",
            job_timeout=synthesis.JOB_TIMEOUT_S,
        )
    except Exception:
        draft.status = "failed"
        draft.error = "Không xếp hàng được; thử lại."
        draft.finished_at = datetime.now(UTC)
        session.commit()
        raise HTTPException(503, "could not queue the synthesis") from None


@router.get("/docs/drafts", response_model=DraftList)
def list_drafts(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    business_project_id: int,
    document_id: Annotated[int | None, Query()] = None,
) -> DraftList:
    """The dự án's drafts (editors only): being written, ready, failed; applied or dropped ones
    for LIST_DAYS."""
    bp = docs.project_for(session, principal, business_project_id, edit=True)
    now = datetime.now(UTC)
    query = (
        select(ProjectDocumentDraft)
        .where(
            ProjectDocumentDraft.business_project_id == bp.id,
            (ProjectDocumentDraft.status.in_(("queued", "fetching", "writing", "ready", "failed")))
            | (
                (ProjectDocumentDraft.status == "applied")
                & (ProjectDocumentDraft.created_at >= now - timedelta(days=LIST_DAYS))
            ),
        )
        .order_by(ProjectDocumentDraft.created_at.desc())
        .limit(30)
    )
    if document_id is not None:
        query = query.where(ProjectDocumentDraft.document_id == document_id)
    return DraftList(
        drafts=[_out(session, d, now=now) for d in session.scalars(query)],
        max_links=synthesis.MAX_LINKS,
        per_day=synthesis.DRAFTS_PER_DAY,
        used_today=_used_today(session, principal.user_id, now),
        url_hosts=bool(importers.hosts_of(settings.assistant_url_hosts)),
        file_kinds=list(importers.DOCUMENT_KINDS),
        max_file_mb=docs.MAX_FILE_MB,
        source_chars=synthesis.SOURCE_CHARS,
    )


@router.post("/docs/drafts/extract", response_model=FileOut)
async def extract_file(
    request: Request,
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> FileOut:
    """The text of one uploaded file (raw bytes as the body), to be sent with POST /docs/drafts
    as a source. Nothing is stored: the file is read and dropped."""
    name, kind = _file_name(filename)
    await run_in_threadpool(docs.project_for, session, principal, business_project_id, True)
    data = await _read_body(request, session)

    def convert() -> FileOut:
        text, meta = _convert(name, kind, data)
        text = text.replace("\r\n", "\n").strip()
        if not text:
            raise HTTPException(422, f"{name} không có nội dung chữ")
        return FileOut(
            name=name,
            kind=kind,
            size=len(data),
            chars=len(text),
            truncated=len(text) > synthesis.SOURCE_CHARS,
            text=text[: synthesis.SOURCE_CHARS],
            pages=meta.get("pages"),
        )

    return await run_in_threadpool(convert)


@router.post("/docs/drafts", response_model=DraftOut, status_code=201)
def create_draft(
    body: DraftIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
) -> DraftOut:
    bp = docs.project_for(session, principal, body.business_project_id, edit=True)
    try:
        urls = synthesis.clean_urls(body.urls, files=len(body.files))
    except synthesis.LinkError as exc:
        raise HTTPException(422, str(exc)) from None
    doc = None
    if body.mode == "new":
        category: str = body.category or "other"
    else:
        if body.document_id is None:
            raise HTTPException(422, "chọn tài liệu cần bổ sung / nâng cấp")
        doc = docs.document_for(session, principal, body.document_id, edit=True)
        if doc.business_project_id != bp.id:
            raise HTTPException(422, "tài liệu không thuộc dự án này")
        if doc.archived_at is not None:
            raise HTTPException(409, "tài liệu đã lưu trữ: đưa trở lại trước khi bổ sung")
        category = doc.category
    now = datetime.now(UTC)
    _check_limits(session, principal, settings, now)
    draft = ProjectDocumentDraft(
        business_project_id=bp.id,
        document_id=doc.id if doc else None,
        mode=body.mode,
        category=category,
        urls=urls,
        files=[f.model_dump() for f in body.files] or None,
        instruction=(body.instruction or "").strip() or None,
        status="queued",
        base_version=doc.version_count if doc else None,
        created_by=principal.user_id,
        created_at=now,
        requested_at=now,
        requested_by=principal.user_id,
    )
    session.add(draft)
    session.flush()
    audit(
        session,
        principal.user_id,
        REQUEST_ACTION,
        f"doc_draft:{draft.id}",
        business_project=bp.id,
        mode=body.mode,
        document=draft.document_id,
        links=len(urls),
        files=[f.name for f in body.files],
    )
    session.commit()
    _enqueue(session, queue, draft)
    return _out(session, draft, now=now)


@router.get("/docs/drafts/{draft_id}", response_model=DraftOut)
def get_draft(draft_id: int, principal: CurrentPrincipal, session: SessionDep) -> DraftOut:
    return _out(session, _draft(session, principal, draft_id), body=True, now=datetime.now(UTC))


@router.post("/docs/drafts/{draft_id}/retry", response_model=DraftOut)
def retry_draft(
    draft_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
) -> DraftOut:
    """Write the draft again from the same links (after a failure, or for another take)."""
    draft = _draft(session, principal, draft_id, lock=True)
    now = datetime.now(UTC)
    if synthesis.running(draft, now):
        raise HTTPException(409, "bản nháp đang được viết")
    if draft.status in ("applied", "discarded"):
        raise HTTPException(409, "bản nháp đã dùng hoặc đã bỏ: tạo yêu cầu mới")
    doc = session.get(ProjectDocument, draft.document_id) if draft.document_id else None
    if draft.mode != "new" and doc is None:
        raise HTTPException(409, "tài liệu không còn")
    _check_limits(session, principal, settings, now)
    draft.status = "queued"
    draft.error = draft.title = draft.summary = draft.body = draft.model = None
    draft.result = None
    draft.sources = None
    draft.cost_usd = None
    draft.finished_at = None
    draft.base_version = doc.version_count if doc else None
    draft.requested_at = now
    draft.requested_by = principal.user_id
    draft.started_at = None
    audit(
        session,
        principal.user_id,
        REQUEST_ACTION,
        f"doc_draft:{draft.id}",
        business_project=draft.business_project_id,
        mode=draft.mode,
        retry=True,
    )
    session.commit()
    _enqueue(session, queue, draft)
    return _out(session, draft, now=now)


@router.post("/docs/drafts/{draft_id}/apply", response_model=DocDetail)
def apply_draft(
    draft_id: int, body: ApplyIn, principal: CurrentPrincipal, session: SessionDep
) -> DocDetail:
    """The reviewed draft becomes a version: a new document ("new"), or the next version of the
    document it was written from. 409 when that document got a newer version meanwhile (the
    draft would undo it): write the draft again."""
    draft = _draft(session, principal, draft_id, lock=True)
    if draft.status != "ready" or not draft.body:
        raise HTTPException(409, "bản nháp chưa sẵn sàng hoặc đã dùng")
    text = body.body if body.body is not None else draft.body
    used = [s for s in (draft.sources or []) if not s.get("error")]
    links = [
        {"url": importers.safe_source(s["url"]), "title": s.get("title"), "via": s.get("via")}
        for s in used
        if s.get("url")
    ]
    files = [{"name": s.get("name"), "via": "file"} for s in used if s.get("name")]
    meta = {
        "links": links,
        "files": files,
        "draft": draft.id,
        "mode": draft.mode,
        "model": draft.model,
    }
    what = " và ".join(
        x
        for x in (f"{len(links)} link" if links else "", f"{len(files)} file" if files else "")
        if x
    )
    note = body.note or f"{synthesis.MODE_LABEL[draft.mode]} từ {what or 'nguồn'}"
    if draft.mode == "new":
        bp = docs.project_for(session, principal, draft.business_project_id, edit=True)
        doc = ProjectDocument(
            business_project_id=bp.id,
            title=docs.clean_title(body.title or draft.title or "Tài liệu tổng hợp"),
            category=body.category or draft.category,
            summary=((body.summary if body.summary is not None else draft.summary) or "").strip()
            or None,
            position=_next_position(session, bp.id),
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(doc)
        session.flush()
        draft.document_id = doc.id
    else:
        if draft.document_id is None:
            raise HTTPException(409, "tài liệu không còn")
        doc = docs.document_for(session, principal, draft.document_id, edit=True)
        session.refresh(doc, with_for_update=True)
        if doc.archived_at is not None:
            raise HTTPException(409, "tài liệu đã lưu trữ: đưa trở lại trước khi lưu bản nháp")
        if doc.version_count != draft.base_version:
            raise HTTPException(
                409,
                f"tài liệu đã có phiên bản {doc.version_count} sau khi bản nháp được viết: "
                "tạo lại bản nháp để không ghi đè thay đổi đó",
            )
        if body.title and body.title.strip() and body.title.strip() != doc.title:
            doc.title = docs.clean_title(body.title)
        if body.summary is not None:
            doc.summary = body.summary.strip() or None
    version = docs.add_version(
        session, doc, text, source="link", user_id=principal.user_id, note=note, meta=meta
    )
    draft.status = "applied"
    draft.applied_version = version.number
    audit(
        session,
        principal.user_id,
        "doc.synthesize.apply",
        f"doc:{doc.id}",
        draft=draft.id,
        version=version.number,
        edited=body.body is not None and body.body.strip() != (draft.body or "").strip(),
    )
    session.commit()
    return _detail(session, principal, doc)


@router.delete("/docs/drafts/{draft_id}", status_code=204)
def discard_draft(draft_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    """Drop a draft (its text goes; the row stays as a record of the request)."""
    draft = _draft(session, principal, draft_id, lock=True)
    if draft.status == "applied":
        raise HTTPException(409, "bản nháp đã được dùng")
    draft.status = "discarded"
    draft.body = None
    draft.files = None  # the uploaded text goes with the draft
    draft.finished_at = draft.finished_at or datetime.now(UTC)
    audit(session, principal.user_id, "doc.synthesize.discard", f"doc_draft:{draft.id}")
    session.commit()
