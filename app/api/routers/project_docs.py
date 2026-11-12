"""Tài liệu dự án (docs/specs/29, user 2026-11-11): the documents of each dự án, uploaded as files
(.md, .txt, .doc, .docx, .xlsx, .pdf) or written on the screen (Markdown, from a template or
blank), rendered on the web, versioned, searchable.

Reading: `docs.view` and the dự án in one's view. Writing: `docs.edit` (app/docs/service.py).
An upload keeps the original file with the version (downloaded as is; a PDF opens in the
browser's viewer) and its text converted to Markdown, which is what is rendered and indexed.
"""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, undefer
from starlette.concurrency import run_in_threadpool

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.assistant import importers
from app.config import Settings, get_settings
from app.docs import service as docs
from app.docs.templates import BY_KEY, TEMPLATES
from app.models import (
    BusinessProject,
    ProjectDocument,
    ProjectDocumentAsset,
    ProjectDocumentLink,
    ProjectDocumentVersion,
    User,
)
from app.models.project_docs import DOC_CATEGORIES

router = APIRouter(tags=["project-docs"])
SessionDep = Annotated[Session, Depends(get_session)]
Category = Literal[
    "overview",
    "architecture",
    "setup",
    "api",
    "deploy",
    "runbook",
    "conventions",
    "decision",
    "other",
]
MIME = {
    ".pdf": "application/pdf",
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


# -- shapes ----------------------------------------------------------------------------------------


class Person(BaseModel):
    id: int
    name: str


class DocItem(BaseModel):
    id: int
    business_project_id: int
    title: str
    category: str
    summary: str | None
    position: int
    pinned: bool
    archived: bool
    version: int
    chars: int
    file_kind: str | None
    updated_at: str | None
    updated_by: Person | None
    stale: bool
    # Shared (§17): how many dự án it belongs to; when listed in a dự án it is shared with,
    # the name of its home.
    projects: int = 1
    shared_from: str | None = None


class CategoryCount(BaseModel):
    key: str
    label: str
    count: int
    core: bool


class DocListOut(BaseModel):
    business_project_id: int
    business_project: str
    can_edit: bool
    docs: list[DocItem]
    categories: list[CategoryCount]
    coverage: float  # share of CORE_CATEGORIES with at least one active document
    stale_days: int


class ProjectRow(BaseModel):
    id: int
    name: str
    docs: int
    can_edit: bool


class VersionOut(BaseModel):
    number: int
    source: str
    note: str | None
    chars: int
    file_name: str | None
    file_kind: str | None
    file_size: int | None
    has_original: bool
    created_at: str | None
    created_by: Person | None
    meta: dict[str, Any] | None = None


class DocSource(BaseModel):
    """A link or an uploaded file a version was written from (docs/specs/29 §12)."""

    url: str | None  # None for a file
    name: str | None = None  # a file's name
    title: str | None
    via: str
    version: int
    at: str | None


class DocProject(BaseModel):
    id: int
    name: str
    home: bool
    can_edit: bool  # the reader may edit documents of this dự án


class DocDetail(DocItem):
    business_project: str
    body: str
    current: VersionOut
    can_edit: bool
    can_delete: bool = False  # only its home's editors (§17)
    # The dự án it belongs to that the reader sees (home first), and how many others.
    shared: list[DocProject] = []
    hidden_projects: int = 0
    created_at: str | None
    created_by: Person | None
    # Every link the document was written from, across its versions (newest first).
    sources: list[DocSource] = []


class SearchHit(BaseModel):
    document_id: int
    title: str
    category: str
    business_project_id: int
    business_project: str
    heading: str | None
    snippet: str


class DocCreateIn(BaseModel):
    business_project_id: int
    title: str = Field(min_length=1, max_length=docs.TITLE_MAX)
    category: Category = "other"
    summary: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=docs.MAX_BODY_CHARS)
    template: str | None = None
    note: str | None = Field(default=None, max_length=docs.NOTE_MAX)


class DocContentIn(BaseModel):
    body: str = Field(min_length=1, max_length=docs.MAX_BODY_CHARS)
    note: str | None = Field(default=None, max_length=docs.NOTE_MAX)
    # The version the edit started from: someone else saved meanwhile -> 409, nothing lost.
    base_version: int = Field(ge=0)


class DocPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=docs.TITLE_MAX)
    category: Category | None = None
    summary: str | None = Field(default=None, max_length=300)
    pinned: bool | None = None
    position: int | None = Field(default=None, ge=0, le=10_000)
    archived: bool | None = None


class TemplateOut(BaseModel):
    key: str
    title: str
    category: str
    summary: str
    body: str


class LimitsOut(BaseModel):
    kinds: list[str]
    max_mb: int
    categories: list[CategoryCount]
    templates: list[TemplateOut]


# -- helpers ---------------------------------------------------------------------------------------


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at else None


def _people(session: Session, ids: set[int | None]) -> dict[int, Person]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = session.execute(select(User.id, User.full_name).where(User.id.in_(wanted))).all()
    return {uid: Person(id=uid, name=name or f"#{uid}") for uid, name in rows}


def _versions(session: Session, ids: list[int | None]) -> dict[int, ProjectDocumentVersion]:
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return {}
    return {
        v.id: v
        for v in session.scalars(
            select(ProjectDocumentVersion).where(ProjectDocumentVersion.id.in_(wanted))
        )
    }


def _stale(at: datetime | None, now: datetime) -> bool:
    return at is not None and now - at > timedelta(days=docs.STALE_DAYS)


def _item(
    doc: ProjectDocument,
    version: ProjectDocumentVersion | None,
    people: dict[int, Person],
    now: datetime,
) -> DocItem:
    return DocItem(
        id=doc.id,
        business_project_id=doc.business_project_id,
        title=doc.title,
        category=doc.category,
        summary=doc.summary,
        position=doc.position,
        pinned=doc.pinned,
        archived=doc.archived_at is not None,
        version=doc.version_count,
        chars=version.chars if version else 0,
        file_kind=version.file_kind if version else None,
        updated_at=_iso(doc.updated_at),
        updated_by=people.get(doc.updated_by) if doc.updated_by else None,
        stale=doc.archived_at is None and _stale(doc.updated_at, now),
    )


def _version_out(
    v: ProjectDocumentVersion, people: dict[int, Person], has_original: bool
) -> VersionOut:
    return VersionOut(
        number=v.number,
        source=v.source,
        note=v.note,
        chars=v.chars,
        file_name=v.file_name,
        file_kind=v.file_kind,
        file_size=v.file_size,
        has_original=has_original,
        created_at=_iso(v.created_at),
        created_by=people.get(v.created_by) if v.created_by else None,
        meta=v.meta,
    )


def _has_original(session: Session, version_ids: list[int]) -> set[int]:
    if not version_ids:
        return set()
    return set(
        session.scalars(
            select(ProjectDocumentVersion.id).where(
                ProjectDocumentVersion.id.in_(version_ids),
                ProjectDocumentVersion.original.is_not(None),
            )
        )
    )


def _categories(counts: dict[str, int]) -> list[CategoryCount]:
    return [
        CategoryCount(
            key=k,
            label=docs.CATEGORY_LABEL[k],
            count=counts.get(k, 0),
            core=k in docs.CORE_CATEGORIES,
        )
        for k in DOC_CATEGORIES
    ]


def _detail(session: Session, principal: Principal, doc: ProjectDocument) -> DocDetail:
    version = session.scalars(
        select(ProjectDocumentVersion)
        .options(undefer(ProjectDocumentVersion.body))
        .where(ProjectDocumentVersion.id == doc.current_version_id)
    ).first()
    if version is None:
        raise HTTPException(404, "document has no content")
    people = _people(session, {doc.updated_by, doc.created_by, version.created_by})
    ids = docs.projects_of(session, doc)
    names = dict(
        session.execute(
            select(BusinessProject.id, BusinessProject.name).where(BusinessProject.id.in_(ids))
        ).all()
    )
    seen = [bp for bp in ids if docs.may_view(principal, bp)]
    item = _item(doc, version, people, datetime.now(UTC))
    item.projects = len(ids)
    return DocDetail(
        **item.model_dump(),
        business_project=names.get(doc.business_project_id, ""),
        body=version.body,
        current=_version_out(version, people, bool(_has_original(session, [version.id]))),
        can_edit=docs.may_edit_doc(session, principal, doc),
        can_delete=docs.may_delete_doc(principal, doc),
        shared=[
            DocProject(
                id=bp,
                name=names.get(bp, ""),
                home=bp == doc.business_project_id,
                can_edit=docs.may_edit(principal, bp),
            )
            for bp in seen
        ],
        hidden_projects=len(ids) - len(seen),
        created_at=_iso(doc.created_at),
        created_by=people.get(doc.created_by) if doc.created_by else None,
        sources=_sources(session, doc.id),
    )


def _sources(session: Session, doc_id: int) -> list[DocSource]:
    out: dict[str, DocSource] = {}
    rows = session.execute(
        select(
            ProjectDocumentVersion.number,
            ProjectDocumentVersion.meta,
            ProjectDocumentVersion.created_at,
        )
        .where(
            ProjectDocumentVersion.document_id == doc_id,
            ProjectDocumentVersion.source == "link",
        )
        .order_by(ProjectDocumentVersion.number.desc())
    )
    for number, meta, at in rows:
        for link in (meta or {}).get("links") or []:
            url = str(link.get("url") or "")
            if url.startswith(("http://", "https://")) and url not in out:
                out[url] = DocSource(
                    url=url,
                    title=link.get("title") or None,
                    via=str(link.get("via") or "web"),
                    version=number,
                    at=_iso(at),
                )
        for f in (meta or {}).get("files") or []:
            name = str(f.get("name") or "")
            if name and f"file:{name}" not in out:
                out[f"file:{name}"] = DocSource(
                    url=None,
                    name=name,
                    title=name,
                    via=str(f.get("via") or "file"),
                    version=number,
                    at=_iso(at),
                )
    return list(out.values())


async def _read_body(request: Request, session: Session) -> bytes:
    limit = docs.MAX_FILE_MB * 1024 * 1024
    await run_in_threadpool(session.commit)  # nothing written yet: frees the connection
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"file lớn hơn {docs.MAX_FILE_MB} MB")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, f"file lớn hơn {docs.MAX_FILE_MB} MB")
    if not data:
        raise HTTPException(422, "file rỗng")
    return bytes(data)


def _file_name(raw: str) -> tuple[str, str]:
    name = unquote(raw).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()[:200]
    lower = name.lower()
    kind = next((k for k in importers.DOCUMENT_KINDS if lower.endswith(k)), None)
    if not name or kind is None:
        raise HTTPException(422, "chỉ nhận " + ", ".join(importers.DOCUMENT_KINDS))
    return name, kind


def _convert(name: str, kind: str, data: bytes) -> tuple[str, dict[str, Any]]:
    """Markdown of an uploaded file, and facts about it (pages of a PDF, sheets)."""
    try:
        text = importers.from_file(name, data)
    except importers.ImportError_ as exc:
        raise HTTPException(422, f"Không đọc được {name}: {exc}") from None
    meta: dict[str, Any] = {}
    if kind == ".pdf":
        meta["pages"] = len(re.findall(r"^--- Trang \d+ ---$", text, re.M))
    if kind == ".xlsx":
        meta["sheets"] = len(re.findall(r"^## ", text, re.M))
    if kind == ".txt" and _looks_plain(text):
        # Shown as written (line breaks kept), nothing read as Markdown syntax.
        meta["plain"] = True
    return text, meta


def _looks_plain(text: str) -> bool:
    """A .txt with no Markdown at all (no heading, list, table, link): shown preformatted."""
    return not re.search(r"^(#{1,6} |\s*[-*+] |\s*\d+[.)] |\|.*\||>)|\]\(", text, re.M)


# -- reading ---------------------------------------------------------------------------------------


@router.get("/docs/projects", response_model=list[ProjectRow])
def doc_projects(principal: CurrentPrincipal, session: SessionDep) -> list[ProjectRow]:
    """The dự án whose documents one may read, with how many active documents each has."""
    projects = docs.viewable_projects(session, principal)
    wanted = [p.id for p in projects]
    counts: dict[int, int] = {}
    for bp_id, n in session.execute(
        select(ProjectDocument.business_project_id, func.count())
        .where(
            ProjectDocument.archived_at.is_(None),
            ProjectDocument.business_project_id.in_(wanted),
        )
        .group_by(ProjectDocument.business_project_id)
    ).all():
        counts[bp_id] = counts.get(bp_id, 0) + int(n)
    # Shared with a dự án: a document of that dự án too (§17).
    for bp_id, n in session.execute(
        select(ProjectDocumentLink.business_project_id, func.count())
        .join(ProjectDocument, ProjectDocument.id == ProjectDocumentLink.document_id)
        .where(
            ProjectDocument.archived_at.is_(None),
            ProjectDocumentLink.business_project_id.in_(wanted),
        )
        .group_by(ProjectDocumentLink.business_project_id)
    ).all():
        counts[bp_id] = counts.get(bp_id, 0) + int(n)
    return [
        ProjectRow(
            id=p.id,
            name=p.name,
            docs=int(counts.get(p.id, 0)),
            can_edit=docs.may_edit(principal, p.id),
        )
        for p in projects
    ]


@router.get("/docs/meta", response_model=LimitsOut)
def doc_meta(principal: CurrentPrincipal) -> LimitsOut:
    """What the upload accepts, the categories and the templates."""
    if not principal.can("docs.view"):
        raise HTTPException(403, "không có quyền xem tài liệu dự án")
    return LimitsOut(
        kinds=list(importers.DOCUMENT_KINDS),
        max_mb=docs.MAX_FILE_MB,
        categories=_categories({}),
        templates=[
            TemplateOut(
                key=t.key, title=t.title, category=t.category, summary=t.summary, body=t.body
            )
            for t in TEMPLATES
        ],
    )


@router.get("/docs", response_model=DocListOut)
def list_docs(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    archived: bool = False,
) -> DocListOut:
    bp = docs.project_for(session, principal, business_project_id)
    query = select(ProjectDocument).where(docs.in_project(bp.id))
    query = query.where(
        ProjectDocument.archived_at.is_not(None)
        if archived
        else ProjectDocument.archived_at.is_(None)
    )
    rows = list(
        session.scalars(
            query.order_by(
                ProjectDocument.pinned.desc(), ProjectDocument.position, ProjectDocument.title
            )
        )
    )
    versions = _versions(session, [d.current_version_id for d in rows])
    people = _people(session, {d.updated_by for d in rows})
    now = datetime.now(UTC)
    active = dict(
        session.execute(
            select(ProjectDocument.category, func.count())
            .where(docs.in_project(bp.id), ProjectDocument.archived_at.is_(None))
            .group_by(ProjectDocument.category)
        ).all()
    )
    covered = sum(1 for k in docs.CORE_CATEGORIES if active.get(k))
    ids = [d.id for d in rows]
    links: dict[int, int] = (
        dict(
            session.execute(
                select(ProjectDocumentLink.document_id, func.count())
                .where(ProjectDocumentLink.document_id.in_(ids))
                .group_by(ProjectDocumentLink.document_id)
            ).all()
        )
        if ids
        else {}
    )
    homes = {d.business_project_id for d in rows if d.business_project_id != bp.id}
    home_names = (
        dict(
            session.execute(
                select(BusinessProject.id, BusinessProject.name).where(
                    BusinessProject.id.in_(homes)
                )
            ).all()
        )
        if homes
        else {}
    )

    def item(d: ProjectDocument) -> DocItem:
        out = _item(d, versions.get(d.current_version_id or -1), people, now)
        out.projects = 1 + int(links.get(d.id, 0))
        if d.business_project_id != bp.id:
            out.shared_from = home_names.get(d.business_project_id, "dự án khác")
        return out

    return DocListOut(
        business_project_id=bp.id,
        business_project=bp.name,
        can_edit=docs.may_edit(principal, bp.id),
        docs=[item(d) for d in rows],
        categories=_categories({k: int(v) for k, v in active.items()}),
        coverage=covered / len(docs.CORE_CATEGORIES),
        stale_days=docs.STALE_DAYS,
    )


@router.get("/docs/search", response_model=list[SearchHit])
def search_docs(
    principal: CurrentPrincipal,
    session: SessionDep,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    business_project_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[SearchHit]:
    if not principal.can("docs.view"):
        raise HTTPException(403, "không có quyền xem tài liệu dự án")
    if business_project_id is not None:
        docs.project_for(session, principal, business_project_id)
    hits = docs.search(session, principal, q, bp_id=business_project_id, limit=limit)
    return [
        SearchHit(
            document_id=h.document_id,
            title=h.title,
            category=h.category,
            business_project_id=h.business_project_id,
            business_project=h.business_project,
            heading=h.heading,
            snippet=h.snippet,
        )
        for h in hits
    ]


@router.get("/docs/{doc_id}", response_model=DocDetail)
def get_doc(doc_id: int, principal: CurrentPrincipal, session: SessionDep) -> DocDetail:
    return _detail(session, principal, docs.document_for(session, principal, doc_id))


@router.get("/docs/{doc_id}/versions", response_model=list[VersionOut])
def list_versions(
    doc_id: int, principal: CurrentPrincipal, session: SessionDep
) -> list[VersionOut]:
    doc = docs.document_for(session, principal, doc_id)
    rows = list(
        session.scalars(
            select(ProjectDocumentVersion)
            .where(ProjectDocumentVersion.document_id == doc.id)
            .order_by(ProjectDocumentVersion.number.desc())
        )
    )
    people = _people(session, {v.created_by for v in rows})
    originals = _has_original(session, [v.id for v in rows])
    return [_version_out(v, people, v.id in originals) for v in rows]


def _version(session: Session, doc: ProjectDocument, number: int) -> ProjectDocumentVersion:
    version = session.scalars(
        select(ProjectDocumentVersion).where(
            ProjectDocumentVersion.document_id == doc.id, ProjectDocumentVersion.number == number
        )
    ).first()
    if version is None:
        raise HTTPException(404, "version not found")
    return version


@router.get("/docs/{doc_id}/versions/{number}")
def get_version(
    doc_id: int, number: int, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    doc = docs.document_for(session, principal, doc_id)
    version = _version(session, doc, number)
    people = _people(session, {version.created_by})
    out = _version_out(version, people, bool(_has_original(session, [version.id]))).model_dump()
    out["body"] = version.body
    return out


@router.get("/docs/{doc_id}/versions/{number}/original")
def get_original(
    doc_id: int,
    number: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    inline: bool = False,
) -> Response:
    """The uploaded file as it was. `inline`: a PDF for the browser's own viewer (the page
    fetches it and shows it from a blob: URL); anything else is always a download, with a
    sandboxing CSP and nosniff, so an uploaded file is never run as a page of this site."""
    doc = docs.document_for(session, principal, doc_id)
    version = _version(session, doc, number)
    data = version.original
    if data is None or not version.file_name:
        raise HTTPException(404, "phiên bản này không có file gốc")
    kind = version.file_kind or ""
    show = inline and kind == ".pdf"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", version.file_name) or "document"
    headers = {
        "Content-Disposition": (
            f'{"inline" if show else "attachment"}; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(version.file_name)}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=300",
    }
    if not show:
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return Response(
        content=data, media_type=MIME.get(kind, "application/octet-stream"), headers=headers
    )


# -- writing ---------------------------------------------------------------------------------------


def _next_position(session: Session, bp_id: int) -> int:
    last = session.scalar(
        select(func.max(ProjectDocument.position)).where(
            ProjectDocument.business_project_id == bp_id
        )
    )
    return int(last if last is not None else -1) + 1


@router.post("/docs", response_model=DocDetail, status_code=201)
def create_doc(body: DocCreateIn, principal: CurrentPrincipal, session: SessionDep) -> DocDetail:
    """A document written on the screen (blank or from a template)."""
    bp = docs.project_for(session, principal, body.business_project_id, edit=True)
    if body.template is not None and body.template not in BY_KEY:
        raise HTTPException(422, "mẫu không tồn tại")
    doc = ProjectDocument(
        business_project_id=bp.id,
        title=docs.clean_title(body.title),
        category=body.category,
        summary=(body.summary or "").strip() or None,
        position=_next_position(session, bp.id),
        created_by=principal.user_id,
        updated_by=principal.user_id,
    )
    session.add(doc)
    session.flush()
    docs.add_version(
        session,
        doc,
        body.body,
        source="template" if body.template else "editor",
        user_id=principal.user_id,
        note=body.note or ("Tạo từ mẫu" if body.template else "Tạo mới"),
        meta={"template": body.template} if body.template else None,
    )
    audit(
        session,
        principal.user_id,
        "doc.create",
        f"doc:{doc.id}",
        business_project=bp.id,
        title=doc.title,
    )
    session.commit()
    return _detail(session, principal, doc)


@router.post("/docs/upload", response_model=DocDetail, status_code=201)
async def upload_doc(
    request: Request,
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    category: Category = "other",
    title: Annotated[str | None, Query(max_length=docs.TITLE_MAX)] = None,
) -> DocDetail:
    """A new document from one file (raw bytes as the body); several files = several calls."""
    name, kind = _file_name(filename)
    await run_in_threadpool(docs.project_for, session, principal, business_project_id, True)
    data = await _read_body(request, session)

    def store() -> DocDetail:
        bp = docs.project_for(session, principal, business_project_id, edit=True)
        text, meta = _convert(name, kind, data)
        doc = ProjectDocument(
            business_project_id=bp.id,
            title=docs.clean_title(title)
            if title and title.strip()
            else docs.title_from(name, text),
            category=category,
            position=_next_position(session, bp.id),
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(doc)
        session.flush()
        docs.add_version(
            session,
            doc,
            text,
            source="upload",
            user_id=principal.user_id,
            note=f"Tải lên {name}",
            file=docs.FileFacts(name=name, kind=kind, size=len(data), data=data),
            meta=meta or None,
        )
        audit(
            session,
            principal.user_id,
            "doc.upload",
            f"doc:{doc.id}",
            business_project=bp.id,
            file=name,
            size=len(data),
        )
        session.commit()
        return _detail(session, principal, doc)

    return await run_in_threadpool(store)


@router.post("/docs/{doc_id}/upload", response_model=DocDetail)
async def upload_version(
    doc_id: int,
    request: Request,
    principal: CurrentPrincipal,
    session: SessionDep,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    note: Annotated[str | None, Query(max_length=docs.NOTE_MAX)] = None,
) -> DocDetail:
    """A new version of a document from a file (the title and place stay)."""
    name, kind = _file_name(filename)
    await run_in_threadpool(docs.document_for, session, principal, doc_id, True)
    data = await _read_body(request, session)

    def store() -> DocDetail:
        doc = docs.document_for(session, principal, doc_id, edit=True)
        session.refresh(doc, with_for_update=True)
        text, meta = _convert(name, kind, data)
        version = docs.add_version(
            session,
            doc,
            text,
            source="upload",
            user_id=principal.user_id,
            note=note or f"Tải lên {name}",
            file=docs.FileFacts(name=name, kind=kind, size=len(data), data=data),
            meta=meta or None,
        )
        audit(
            session,
            principal.user_id,
            "doc.version",
            f"doc:{doc.id}",
            version=version.number,
            file=name,
        )
        session.commit()
        return _detail(session, principal, doc)

    return await run_in_threadpool(store)


@router.put("/docs/{doc_id}/content", response_model=DocDetail)
def save_content(
    doc_id: int, body: DocContentIn, principal: CurrentPrincipal, session: SessionDep
) -> DocDetail:
    """A new version written on the screen. 409 when another version was saved since
    `base_version` (the editor keeps the text and shows what changed)."""
    doc = docs.document_for(session, principal, doc_id, edit=True)
    session.refresh(doc, with_for_update=True)
    if doc.version_count != body.base_version:
        raise HTTPException(
            409,
            f"tài liệu vừa được lưu phiên bản {doc.version_count} bởi người khác: tải lại, "
            "nội dung bạn đang sửa vẫn được giữ trong trình soạn",
        )
    current = (
        session.get(ProjectDocumentVersion, doc.current_version_id)
        if doc.current_version_id
        else None
    )
    if current is not None and current.content_hash == docs.content_hash(
        body.body.replace("\r\n", "\n").strip()
    ):
        return _detail(session, principal, doc)  # nothing changed: no empty version
    version = docs.add_version(
        session, doc, body.body, source="editor", user_id=principal.user_id, note=body.note
    )
    audit(session, principal.user_id, "doc.edit", f"doc:{doc.id}", version=version.number)
    session.commit()
    return _detail(session, principal, doc)


@router.post("/docs/{doc_id}/versions/{number}/restore", response_model=DocDetail)
def restore_version(
    doc_id: int, number: int, principal: CurrentPrincipal, session: SessionDep
) -> DocDetail:
    """Version `number` becomes current again, as a new version (history is never rewritten)."""
    doc = docs.document_for(session, principal, doc_id, edit=True)
    session.refresh(doc, with_for_update=True)
    old = session.scalars(
        select(ProjectDocumentVersion)
        .options(undefer(ProjectDocumentVersion.body), undefer(ProjectDocumentVersion.original))
        .where(
            ProjectDocumentVersion.document_id == doc.id, ProjectDocumentVersion.number == number
        )
    ).first()
    if old is None:
        raise HTTPException(404, "version not found")
    if old.number == doc.version_count:
        raise HTTPException(409, "đây đã là phiên bản hiện tại")
    file = (
        docs.FileFacts(
            name=old.file_name, kind=old.file_kind or "", size=old.file_size or 0, data=old.original
        )
        if old.original is not None and old.file_name
        else None
    )
    version = docs.add_version(
        session,
        doc,
        old.body,
        source="restore",
        user_id=principal.user_id,
        note=f"Khôi phục phiên bản {old.number}",
        file=file,
        meta=old.meta,
    )
    audit(
        session,
        principal.user_id,
        "doc.restore",
        f"doc:{doc.id}",
        restored=old.number,
        version=version.number,
    )
    session.commit()
    return _detail(session, principal, doc)


@router.patch("/docs/{doc_id}", response_model=DocItem)
def update_doc(
    doc_id: int, body: DocPatchIn, principal: CurrentPrincipal, session: SessionDep
) -> DocItem:
    doc = docs.document_for(session, principal, doc_id, edit=True)
    changes: dict[str, Any] = {}
    if body.title is not None:
        title = docs.clean_title(body.title)
        if title != doc.title:
            doc.title = changes["title"] = title
    if body.category is not None and body.category != doc.category:
        doc.category = changes["category"] = body.category
    if "summary" in body.model_fields_set:
        summary = (body.summary or "").strip() or None
        if summary != doc.summary:
            doc.summary = summary
            changes["summary"] = bool(summary)
    if body.pinned is not None and body.pinned != doc.pinned:
        doc.pinned = changes["pinned"] = body.pinned
    if body.position is not None and body.position != doc.position:
        doc.position = changes["position"] = body.position
    if body.archived is not None and body.archived != (doc.archived_at is not None):
        doc.archived_at = datetime.now(UTC) if body.archived else None
        changes["archived"] = body.archived
    if changes:
        doc.updated_by = principal.user_id
        if "title" in changes:
            docs.reindex_title(session, doc)
        audit(session, principal.user_id, "doc.update", f"doc:{doc.id}", **changes)
        session.commit()
        session.refresh(doc)
    version = (
        session.get(ProjectDocumentVersion, doc.current_version_id)
        if doc.current_version_id
        else None
    )
    people = _people(session, {doc.updated_by})
    return _item(doc, version, people, datetime.now(UTC))


@router.delete("/docs/{doc_id}", status_code=204)
def delete_doc(doc_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    """Gone for good, with every version, its original files and its drafts (docs/specs/29 §15,
    user 2026-11-12: a wrong import or an outdated document is removed in one step, after the
    screen's confirmation). Archiving stays the way to hide one and keep it. Images pasted into
    it stay with the dự án (another document may show them). A shared document is deleted by
    its home only; another dự án drops the link instead (§17)."""
    doc = docs.document_for(session, principal, doc_id, edit=True)
    if not docs.may_delete_doc(principal, doc):
        raise HTTPException(
            403, "tài liệu thuộc dự án khác: bỏ liên kết với dự án của bạn thay vì xoá"
        )
    audit(
        session,
        principal.user_id,
        "doc.delete",
        f"doc:{doc.id}",
        business_project=doc.business_project_id,
        title=doc.title,
        versions=doc.version_count,
        archived=doc.archived_at is not None,
    )
    doc.current_version_id = None
    session.flush()
    session.delete(doc)
    session.commit()


# -- images inside documents (docs/specs/29 §13) ------------------------------------------------

ASSET_MAX_MB = 5


class AssetOut(BaseModel):
    id: int
    name: str
    mime: str
    size: int
    markdown: str  # what the editor inserts: ![name](asset:<id>)


@router.post("/docs/assets", response_model=AssetOut, status_code=201)
async def upload_asset(
    request: Request,
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    filename: Annotated[str, Query(max_length=255)] = "",
) -> AssetOut:
    """An image for a document of the dự án (raw bytes: PNG, JPEG, WebP, GIF ≤ 5 MB). The same
    image pasted again is the same asset."""

    await run_in_threadpool(docs.project_for, session, principal, business_project_id, True)
    limit = ASSET_MAX_MB * 1024 * 1024
    await run_in_threadpool(session.commit)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"ảnh lớn hơn {ASSET_MAX_MB} MB")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, f"ảnh lớn hơn {ASSET_MAX_MB} MB")
    raw = bytes(data)
    return await run_in_threadpool(
        _save_asset, session, principal, business_project_id, raw, filename
    )


def _save_asset(
    session: Session, principal: Principal, bp_id: int, raw: bytes, filename: str
) -> AssetOut:
    """One image of the dự án's documents: checked from its bytes, the same bytes kept once."""
    from app.docs.synthesis import image_mime

    mime = image_mime(raw)
    if mime is None:
        raise HTTPException(422, "chỉ nhận ảnh PNG, JPEG, WebP, GIF")
    digest = hashlib.sha256(raw).hexdigest()
    base = unquote(filename).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    name = " ".join("".join(ch for ch in base if ch.isprintable()).replace("]", " ").split())
    name = name[:80] or "Ảnh"
    docs.project_for(session, principal, bp_id, edit=True)
    row = session.scalar(
        select(ProjectDocumentAsset).where(
            ProjectDocumentAsset.business_project_id == bp_id,
            ProjectDocumentAsset.sha256 == digest,
        )
    )
    if row is None:
        row = ProjectDocumentAsset(
            business_project_id=bp_id,
            uploaded_by=principal.user_id,
            name=name,
            mime=mime,
            size=len(raw),
            sha256=digest,
            data=raw,
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:  # the same image pasted twice at once
            session.rollback()
            row = session.scalars(
                select(ProjectDocumentAsset).where(
                    ProjectDocumentAsset.business_project_id == bp_id,
                    ProjectDocumentAsset.sha256 == digest,
                )
            ).one()
    return AssetOut(
        id=row.id,
        name=name,
        mime=row.mime,
        size=row.size,
        markdown=f"![{name}](asset:{row.id})",
    )


class AssetUrlIn(BaseModel):
    business_project_id: int
    url: str = Field(min_length=8, max_length=4000)
    name: str = Field(default="", max_length=120)


@router.post("/docs/assets/fetch", response_model=AssetOut, status_code=201)
def fetch_asset(
    body: AssetUrlIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AssetOut:
    """An image pasted from Google Docs or a web page, by its address (docs/specs/29 §18.1):
    the browser cannot read Google's images (no CORS) and their addresses expire, so the server
    fetches it once (the guarded client of web links) and keeps it as the dự án's own."""
    docs.project_for(session, principal, body.business_project_id, edit=True)
    if not body.url.startswith(("https://", "http://")):
        raise HTTPException(422, "chỉ tải ảnh từ địa chỉ http(s)")
    session.commit()  # nothing held while the image downloads
    try:
        _, raw = importers.fetch_bytes(
            body.url, importers.hosts_of(settings.assistant_url_hosts), ASSET_MAX_MB * 1024 * 1024
        )
    except importers.ImportError_ as exc:
        raise HTTPException(422, f"không tải được ảnh: {exc}") from None
    return _save_asset(session, principal, body.business_project_id, raw, body.name or "Ảnh")


@router.get("/docs/assets/{asset_id}")
def get_asset(asset_id: int, principal: CurrentPrincipal, session: SessionDep) -> Response:
    """An image of a document: whoever may read the dự án's documents sees it. Its bytes never
    change (a new image is a new asset), so browsers keep it."""
    row = session.get(ProjectDocumentAsset, asset_id)
    if row is None or not (
        docs.may_view(principal, row.business_project_id) or _shared_image(session, principal, row)
    ):
        raise HTTPException(404, "image not found")
    data = session.scalar(
        select(ProjectDocumentAsset.data).where(ProjectDocumentAsset.id == row.id)
    )
    return Response(
        content=data or b"",
        media_type=row.mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=86400, immutable",
        },
    )


def _shared_image(session: Session, principal: Principal, asset: ProjectDocumentAsset) -> bool:
    """An image of a dự án one does not see, shown in one of its documents that is shared with a
    dự án one sees (§17): the current text of such a document refers to it."""
    seen = principal.visible_projects()
    shared = select(ProjectDocumentLink.document_id)
    if seen is not None:
        shared = shared.where(ProjectDocumentLink.business_project_id.in_(seen))
    hit = session.scalar(
        select(ProjectDocument.id)
        .join(
            ProjectDocumentVersion, ProjectDocumentVersion.id == ProjectDocument.current_version_id
        )
        .where(
            ProjectDocument.business_project_id == asset.business_project_id,
            ProjectDocument.id.in_(shared),
            ProjectDocumentVersion.body.contains(f"(asset:{asset.id})"),
        )
        .limit(1)
    )
    return hit is not None and principal.can("docs.view")


# -- one document, several dự án (docs/specs/29 §17) ---------------------------------------------


class ProjectsIn(BaseModel):
    # The dự án it is shared with besides its home (the whole set: added and dropped at once).
    project_ids: list[int] = Field(default_factory=list, max_length=50)


@router.put("/docs/{doc_id}/projects", response_model=DocDetail)
def set_projects(
    doc_id: int, body: ProjectsIn, principal: CurrentPrincipal, session: SessionDep
) -> DocDetail:
    """Share a document with other dự án, or stop. Adding a dự án needs the right to edit
    documents there (a document is not pushed into a dự án one does not run); dropping one needs
    it there or at the home. Links to dự án the caller does not see are left as they are."""
    doc = docs.document_for(session, principal, doc_id, edit=True)
    session.refresh(doc, with_for_update=True)
    home = doc.business_project_id
    current = set(docs.linked_ids(session, doc.id))
    wanted = {p for p in body.project_ids if p != home}
    # A dự án the caller cannot see is never added; one already linked stays as it is.
    if any(p not in current and not docs.may_view(principal, p) for p in wanted):
        raise HTTPException(403, "bạn không có quyền sửa tài liệu của một dự án được chọn")
    hidden = {p for p in current if not docs.may_view(principal, p)}
    wanted = {p for p in wanted if docs.may_view(principal, p)} | hidden
    added, dropped = wanted - current, current - wanted
    for bp_id in added:
        if session.get(BusinessProject, bp_id) is None:
            raise HTTPException(422, "dự án không tồn tại")
        if not docs.may_edit(principal, bp_id):
            raise HTTPException(403, "bạn không có quyền sửa tài liệu của một dự án được chọn")
    for bp_id in dropped:
        if not (docs.may_edit(principal, bp_id) or docs.may_edit(principal, home)):
            raise HTTPException(403, "bạn không có quyền bỏ liên kết với một dự án được chọn")
    for bp_id in added:
        session.add(
            ProjectDocumentLink(
                document_id=doc.id, business_project_id=bp_id, linked_by=principal.user_id
            )
        )
    if dropped:
        session.execute(
            delete(ProjectDocumentLink).where(
                ProjectDocumentLink.document_id == doc.id,
                ProjectDocumentLink.business_project_id.in_(dropped),
            )
        )
    if added or dropped:
        audit(
            session,
            principal.user_id,
            "doc.share",
            f"doc:{doc.id}",
            home=home,
            added=sorted(added),
            dropped=sorted(dropped),
        )
    session.commit()
    return _detail(session, principal, doc)
