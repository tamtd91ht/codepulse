"""Teaching Stetho (docs/specs/28, user 2026-11-09): its documents, saved answers, web lookup.

Everything here needs `assistant.train` ("Dạy Stetho"): no built-in permission set holds it, so
by default only admins; an admin grants it through a permission set. Documents with audience
"trainers" are read by Stetho only for people who hold it.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import Principal, require
from app.assistant import importers, knowledge
from app.config import Settings, get_settings
from app.models import AppSetting, AssistantDoc, AssistantDocChunk, User

log = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])
Trainer = Annotated[Principal, require("assistant.train")]
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Body = Annotated[str, StringConstraints(min_length=1, max_length=importers.MAX_TEXT_CHARS)]
Audience = Literal["all", "trainers"]
Paths = Annotated[
    list[Annotated[str, StringConstraints(pattern=r"^/[\w\-/]*$", max_length=100)]],
    Field(max_length=20),
]


class DocIn(BaseModel):
    title: Title
    body: Body
    audience: Audience | None = None  # None: "all", or "trainers" for a saved answer
    paths: Paths = []
    # "qa": an answer of Stetho's, corrected and saved by a trainer. Its audience defaults to
    # trainers: the answer came from the trainer's own view of the numbers.
    source: Literal["manual", "qa"] = "manual"
    question: Annotated[str, StringConstraints(max_length=4000)] | None = None


class DocPatch(BaseModel):
    title: Title | None = None
    body: Body | None = None
    audience: Audience | None = None
    paths: Paths | None = None
    enabled: bool | None = None


class UrlIn(BaseModel):
    url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
    title: Title | None = None
    audience: Audience = "all"


class SearchIn(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    path: Annotated[str, StringConstraints(max_length=200)] | None = None


class SettingsIn(BaseModel):
    web: bool


def _out(doc: AssistantDoc, chunks: int, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": doc.id,
        "title": doc.title,
        "source": doc.source,
        "source_name": doc.source_name,
        "audience": doc.audience,
        "enabled": doc.enabled,
        "builtin": doc.source == "builtin",
        "paths": doc.paths or [],
        "chars": len(doc.body),
        "chunks": chunks,
        "updated_at": doc.updated_at,
        "updated_by": names.get(doc.updated_by or doc.created_by or 0),
        "question": (doc.meta or {}).get("question"),
    }


def _names(session: Session, ids: set[int | None]) -> dict[int, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    return {
        u.id: u.full_name or u.primary_email or f"#{u.id}"
        for u in session.scalars(select(User).where(User.id.in_(wanted)))
    }


def _doc(session: Session, doc_id: int) -> AssistantDoc:
    doc = session.get(AssistantDoc, doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    return doc


def _counts(session: Session) -> dict[int, int]:
    return {
        int(d): int(n)
        for d, n in session.execute(
            select(AssistantDocChunk.doc_id, func.count()).group_by(AssistantDocChunk.doc_id)
        )
    }


@router.get("/assistant/docs")
def list_docs(principal: Trainer, session: SessionDep) -> dict[str, Any]:
    """Every document (built-ins first), and whether Stetho may look things up on the web."""
    knowledge.ensure_builtin()
    docs = list(
        session.scalars(
            select(AssistantDoc).order_by(
                (AssistantDoc.source != "builtin").asc(), AssistantDoc.updated_at.desc()
            )
        )
    )
    counts = _counts(session)
    names = _names(session, {d.updated_by for d in docs} | {d.created_by for d in docs})
    return {
        "docs": [_out(d, counts.get(d.id, 0), names) for d in docs],
        "web": knowledge.web_enabled(session),
        "file_kinds": list(importers.FILE_KINDS),
        "max_file_mb": importers.MAX_FILE_BYTES // (1024 * 1024),
    }


@router.get("/assistant/docs/{doc_id}")
def get_doc(doc_id: int, principal: Trainer, session: SessionDep) -> dict[str, Any]:
    doc = _doc(session, doc_id)
    names = _names(session, {doc.updated_by, doc.created_by})
    return {**_out(doc, _counts(session).get(doc.id, 0), names), "body": doc.body}


def _create(
    session: Session,
    principal: Principal,
    *,
    title: str,
    body: str,
    source: str,
    source_name: str | None,
    audience: str,
    paths: list[str],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc = AssistantDoc(
        title=title,
        body=body,
        source=source,
        source_name=source_name,
        audience=audience,
        paths=paths,
        keywords=[],
        enabled=True,
        created_by=principal.user_id,
        updated_by=principal.user_id,
        meta=meta,
    )
    chunks = knowledge.save(session, doc)
    audit(
        session,
        principal.user_id,
        "assistant.doc.add",
        f"assistant_doc:{doc.id}",
        source=source,
        title=title[:100],
        chars=len(body),
    )
    session.commit()
    return _out(doc, chunks, _names(session, {principal.user_id}))


@router.post("/assistant/docs", status_code=201)
def add_doc(body: DocIn, principal: Trainer, session: SessionDep) -> dict[str, Any]:
    """A document written on the screen, or a corrected answer saved as knowledge ("qa": the
    question is kept with it, so the same question finds it)."""
    text = body.body.strip()
    if body.source == "qa" and body.question:
        text = f"**Câu hỏi:** {body.question.strip()}\n\n{text}"
    return _create(
        session,
        principal,
        title=body.title,
        body=text,
        source=body.source,
        source_name=None,
        audience=body.audience or ("trainers" if body.source == "qa" else "all"),
        paths=body.paths,
        meta={"question": body.question.strip()} if body.question else None,
    )


@router.post("/assistant/docs/upload", status_code=201)
async def upload_doc(
    request: Request,
    principal: Trainer,
    session: SessionDep,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    audience: Audience = "all",
    title: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:
    """A file as the request body (raw bytes): .md, .txt, .docx or .xlsx. Its text is kept,
    not the file."""
    name = unquote(filename).replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(importers.FILE_KINDS):
        raise HTTPException(422, "chỉ nhận " + ", ".join(importers.FILE_KINDS))
    limit = importers.MAX_FILE_BYTES
    await run_in_threadpool(session.commit)  # nothing written yet: frees the connection
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"file lớn hơn {limit // (1024 * 1024)} MB")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, f"file lớn hơn {limit // (1024 * 1024)} MB")

    def store() -> dict[str, Any]:
        try:
            text = importers.from_file(name, bytes(data))
        except importers.ImportError_ as exc:
            raise HTTPException(422, f"Không đọc được {name}: {exc}") from None
        stem = name.rsplit(".", 1)[0]
        return _create(
            session,
            principal,
            title=(title or stem).strip()[:200] or stem,
            body=text,
            source="file",
            source_name=name,
            audience=audience,
            paths=[],
        )

    return await run_in_threadpool(store)


@router.post("/assistant/docs/url", status_code=201)
def import_url(
    body: UrlIn, principal: Trainer, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """A web page (or a text / Markdown file) by its link; "Lấy lại" fetches it again. The
    link shown (and given to Stetho) has no query string; the full one is kept for refetching."""
    try:
        page_title, text = importers.from_url(
            body.url, importers.hosts_of(settings.assistant_url_hosts)
        )
    except importers.ImportError_ as exc:
        raise HTTPException(422, f"Không lấy được nội dung: {exc}") from None
    return _create(
        session,
        principal,
        title=body.title or page_title,
        body=text,
        source="url",
        source_name=importers.safe_source(body.url),
        audience=body.audience,
        paths=[],
        meta={"fetched_at": datetime.now(UTC).isoformat(), "url": body.url},
    )


@router.post("/assistant/docs/{doc_id}/refresh")
def refresh_url(
    doc_id: int, principal: Trainer, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    doc = _doc(session, doc_id)
    url = str((doc.meta or {}).get("url") or doc.source_name or "")
    if doc.source != "url" or not url:
        raise HTTPException(409, "chỉ tài liệu lấy từ link mới lấy lại được")
    try:
        _, text = importers.from_url(url, importers.hosts_of(settings.assistant_url_hosts))
    except importers.ImportError_ as exc:
        raise HTTPException(422, f"Không lấy được nội dung: {exc}") from None
    doc.body = text
    doc.updated_by = principal.user_id
    doc.meta = {**(doc.meta or {}), "fetched_at": datetime.now(UTC).isoformat()}
    chunks = knowledge.save(session, doc)
    audit(session, principal.user_id, "assistant.doc.refresh", f"assistant_doc:{doc.id}")
    session.commit()
    return _out(doc, chunks, _names(session, {principal.user_id}))


@router.patch("/assistant/docs/{doc_id}")
def update_doc(
    doc_id: int, body: DocPatch, principal: Trainer, session: SessionDep
) -> dict[str, Any]:
    """Edit a document; a built-in one can only be switched on or off."""
    doc = _doc(session, doc_id)
    changes = body.model_dump(exclude_none=True)
    if doc.source == "builtin" and set(changes) - {"enabled"}:
        raise HTTPException(409, "tài liệu có sẵn của CodePulse chỉ bật/tắt được")
    reindex = False
    if body.title is not None:
        doc.title, reindex = body.title, True
    if body.body is not None:
        doc.body, reindex = body.body.strip(), True
    if body.paths is not None:
        doc.paths, reindex = body.paths, True
    if body.audience is not None:
        doc.audience = body.audience
    if body.enabled is not None:
        doc.enabled = body.enabled
    doc.updated_by = principal.user_id
    chunks = knowledge.save(session, doc) if reindex else _counts(session).get(doc.id, 0)
    audit(
        session,
        principal.user_id,
        "assistant.doc.update",
        f"assistant_doc:{doc.id}",
        fields=sorted(changes),
    )
    session.commit()
    return _out(doc, chunks, _names(session, {doc.updated_by, doc.created_by}))


@router.delete("/assistant/docs/{doc_id}", status_code=204)
def delete_doc(doc_id: int, principal: Trainer, session: SessionDep) -> None:
    doc = _doc(session, doc_id)
    if doc.source == "builtin":
        raise HTTPException(409, "tài liệu có sẵn của CodePulse không xoá được, chỉ tắt được")
    audit(
        session,
        principal.user_id,
        "assistant.doc.delete",
        f"assistant_doc:{doc.id}",
        title=doc.title[:100],
    )
    session.delete(doc)
    session.commit()


@router.post("/assistant/docs/search")
def try_search(body: SearchIn, principal: Trainer, session: SessionDep) -> dict[str, Any]:
    """ "Thử hỏi": the pieces Stetho would read for this question (no LLM call)."""
    knowledge.ensure_builtin()
    hits = knowledge.search(session, body.question, path=body.path, trainer=True)
    return {
        "terms": knowledge.terms(body.question),
        "hits": [
            {
                "doc_id": h.doc_id,
                "title": h.title,
                "heading": h.heading,
                "body": h.body,
                "source": h.source,
                "score": round(h.score, 4),
            }
            for h in hits
        ],
    }


@router.put("/assistant/settings")
def save_settings(body: SettingsIn, principal: Trainer, session: SessionDep) -> dict[str, Any]:
    """Whether Stetho may search the web (WebSearch only) when its documents do not answer."""
    row = session.get(AppSetting, knowledge.WEB_KEY)
    if row is None:
        row = AppSetting(key=knowledge.WEB_KEY, value=body.web, set_by=principal.user_id)
        session.add(row)
    else:
        row.value, row.set_by = body.web, principal.user_id
    audit(session, principal.user_id, "assistant.web", "assistant", enabled=body.web)
    session.commit()
    return {"web": body.web}
