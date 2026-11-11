"""Tài liệu dự án (docs/specs/29): who may read and edit, versions, the search index.

A document belongs to one dự án. Reading needs `docs.view` and the dự án in one's view (the
others do not exist for them, docs/specs/09); editing needs `docs.edit`: scope `project` covers
the dự án one manages, `all` every dự án one sees (admins hold it). Every save is a new version
(numbered from 1, never overwritten); the document points at its current one, whose sections are
indexed for search (accent-folded full text, the same folding as Stetho's knowledge base).
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import case, delete, func, literal, select
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.assistant.knowledge import exact_terms, fold, split, terms
from app.models import (
    BusinessProject,
    ProjectDocument,
    ProjectDocumentChunk,
    ProjectDocumentVersion,
)

MAX_FILE_MB = 20
MAX_BODY_CHARS = 400_000
TITLE_MAX = 200
NOTE_MAX = 300
CHUNK_CHARS = 1_200
SEARCH_CANDIDATES = 60
SNIPPET_CHARS = 220
# A document not updated for this long is flagged "có thể đã cũ" on the screen.
STALE_DAYS = 180
# The categories a dự án is expected to cover (the completeness card); "other" is not one.
CORE_CATEGORIES = (
    "overview",
    "architecture",
    "setup",
    "api",
    "deploy",
    "runbook",
    "conventions",
)
CATEGORY_LABEL = {
    "overview": "Tổng quan",
    "architecture": "Kiến trúc",
    "setup": "Cài đặt & môi trường",
    "api": "API & tích hợp",
    "deploy": "Triển khai",
    "runbook": "Vận hành & sự cố",
    "conventions": "Quy ước & quy trình",
    "decision": "Quyết định kỹ thuật (ADR)",
    "other": "Khác",
}


# -- permissions -----------------------------------------------------------------------------------


def may_view(principal: Principal, bp_id: int | None) -> bool:
    return principal.can("docs.view") and principal.can_view_project(bp_id)


def may_edit(principal: Principal, bp_id: int | None) -> bool:
    scope = principal.scope("docs.edit")
    if scope is None or not principal.can_view_project(bp_id):
        return False
    return scope == "all" or principal.can_manage_project(bp_id)


def viewable_projects(session: Session, principal: Principal) -> list[BusinessProject]:
    if not principal.can("docs.view"):
        return []
    query = select(BusinessProject).order_by(BusinessProject.name)
    seen = principal.visible_projects()
    if seen is not None:
        query = query.where(BusinessProject.id.in_(seen))
    return list(session.scalars(query))


def project_for(
    session: Session, principal: Principal, bp_id: int, edit: bool = False
) -> BusinessProject:
    bp = session.get(BusinessProject, bp_id)
    if bp is None or not may_view(principal, bp.id):
        raise HTTPException(404, "dự án not found")
    if edit and not may_edit(principal, bp.id):
        raise HTTPException(403, "bạn không có quyền sửa tài liệu của dự án này")
    return bp


def document_for(
    session: Session, principal: Principal, doc_id: int, edit: bool = False
) -> ProjectDocument:
    doc = session.get(ProjectDocument, doc_id)
    if doc is None or not may_view(principal, doc.business_project_id):
        raise HTTPException(404, "document not found")
    if edit and not may_edit(principal, doc.business_project_id):
        raise HTTPException(403, "bạn không có quyền sửa tài liệu của dự án này")
    return doc


# -- versions --------------------------------------------------------------------------------------


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_title(raw: str) -> str:
    title = " ".join("".join(ch for ch in raw if ch.isprintable()).split())
    if not title:
        raise HTTPException(422, "tiêu đề trống")
    return title[:TITLE_MAX]


def title_from(file_name: str, body: str) -> str:
    """A title for an uploaded file: its first `#` heading; else its first line when that reads
    like a title (short, not a sentence, not a table or page marker: a .doc or a PDF has no
    heading styles); else its name without extension, first letter capitalised."""
    if m := re.search(r"^#\s+(.+)$", body, re.M):
        return clean_title(m.group(1).strip("# "))
    for line in body.split("\n"):
        text = line.strip().strip("*_ ")
        if not text or re.match(r"^-{3}\s*Trang \d+\s*-{3}$", text):
            continue
        if len(text) <= 100 and not text.endswith((".", ":", ",", ";")) and text[0] not in "|-`>":
            return clean_title(text)
        break
    stem = " ".join(file_name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").split())
    return clean_title(stem[:1].upper() + stem[1:] if stem else file_name)


@dataclass
class FileFacts:
    name: str
    kind: str
    size: int
    data: bytes


def add_version(
    session: Session,
    doc: ProjectDocument,
    body: str,
    *,
    source: str,
    user_id: int | None,
    note: str | None = None,
    file: FileFacts | None = None,
    meta: dict[str, Any] | None = None,
) -> ProjectDocumentVersion:
    """A new current version of `doc` and its search index. The caller commits."""
    text = body.replace("\r\n", "\n").strip()
    if not text:
        raise HTTPException(422, "tài liệu trống")
    if len(text) > MAX_BODY_CHARS:
        raise HTTPException(422, f"tài liệu dài hơn {MAX_BODY_CHARS:,} ký tự: chia nhỏ")
    number = doc.version_count + 1
    version = ProjectDocumentVersion(
        document_id=doc.id,
        number=number,
        body=text,
        chars=len(text),
        content_hash=content_hash(text),
        source=source,
        note=(note or "").strip()[:NOTE_MAX] or None,
        file_name=file.name if file else None,
        file_kind=file.kind if file else None,
        file_size=file.size if file else None,
        original=file.data if file else None,
        meta=meta,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    session.add(version)
    session.flush()
    doc.current_version_id = version.id
    doc.version_count = number
    doc.updated_by = user_id
    doc.updated_at = datetime.now(UTC)
    index(session, doc, text)
    return version


def index(session: Session, doc: ProjectDocument, body: str) -> int:
    """Replace the sections of `doc` (its title searchable with each)."""
    session.execute(delete(ProjectDocumentChunk).where(ProjectDocumentChunk.document_id == doc.id))
    pieces = split(body, CHUNK_CHARS)
    for i, piece in enumerate(pieces):
        searchable = fold(" ".join(x for x in (doc.title, piece.heading or "", piece.body) if x))
        session.add(
            ProjectDocumentChunk(
                document_id=doc.id,
                position=i,
                heading=piece.heading,
                body=piece.body,
                search=func.to_tsvector("simple", searchable),
            )
        )
    session.flush()
    return len(pieces)


def reindex_title(session: Session, doc: ProjectDocument) -> None:
    """After a rename: the title is part of every section's vector."""
    version = (
        session.get(ProjectDocumentVersion, doc.current_version_id)
        if doc.current_version_id
        else None
    )
    if version is not None:
        index(session, doc, version.body)


# -- search ----------------------------------------------------------------------------------------


@dataclass
class Hit:
    document_id: int
    title: str
    category: str
    business_project_id: int
    business_project: str
    heading: str | None
    snippet: str
    score: float


def snippet(body: str, words: list[str]) -> str:
    """About SNIPPET_CHARS of the section around the first word found, on one line."""
    # Markdown syntax is noise in a one-line extract: callout markers, quote/heading/list marks,
    # code ticks, emphasis, table rules.
    plain = re.sub(r"> ?\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]", " ", body, flags=re.I)
    plain = re.sub(r"(?m)^\s*(?:#{1,6}\s+|>\s?|[-*+]\s+(?:\[[ xX]\]\s+)?)", "", plain)
    plain = re.sub(r"(?m)^\s*\|?[\s:|-]{3,}\|?\s*$", " ", plain)
    plain = plain.replace("`", "").replace("**", "").replace("|", " · ")
    flat = " ".join(plain.split())
    folded = fold(flat)
    at = min((i for w in words if (i := folded.find(w)) >= 0), default=0)
    start = max(0, at - SNIPPET_CHARS // 3)
    if start:
        space = flat.find(" ", start)
        start = space + 1 if 0 <= space < at else start
    text = flat[start : start + SNIPPET_CHARS]
    return ("…" if start else "") + text + ("…" if start + SNIPPET_CHARS < len(flat) else "")


def search(
    session: Session,
    principal: Principal,
    q: str,
    *,
    bp_id: int | None = None,
    limit: int = 20,
) -> list[Hit]:
    """Sections that best match `q` in the documents one may read (archived ones left out),
    most of the query's words first, then rank; one hit per document (its best section)."""
    words = terms(q)
    if not words:
        return []
    exact = [w for w in exact_terms(q) if fold(w) in words]
    query = func.to_tsquery("simple", literal(" | ".join(words)))
    covered = sum(
        (
            case(
                (ProjectDocumentChunk.search.op("@@")(func.to_tsquery("simple", literal(w))), 1),
                else_=0,
            )
            for w in words
        ),
        start=literal(0),
    )
    rank = func.ts_rank_cd(ProjectDocumentChunk.search, query, 32)
    stmt = (
        select(
            ProjectDocumentChunk.document_id,
            ProjectDocumentChunk.heading,
            ProjectDocumentChunk.body,
            ProjectDocument.title,
            ProjectDocument.category,
            ProjectDocument.business_project_id,
            BusinessProject.name,
            covered.label("covered"),
            rank.label("rank"),
        )
        .join(ProjectDocument, ProjectDocument.id == ProjectDocumentChunk.document_id)
        .join(BusinessProject, BusinessProject.id == ProjectDocument.business_project_id)
        .where(ProjectDocument.archived_at.is_(None), ProjectDocumentChunk.search.op("@@")(query))
        .order_by(covered.desc(), rank.desc(), ProjectDocumentChunk.id)
        .limit(SEARCH_CANDIDATES)
    )
    if bp_id is not None:
        stmt = stmt.where(ProjectDocument.business_project_id == bp_id)
    seen = principal.visible_projects()
    if seen is not None:
        stmt = stmt.where(ProjectDocument.business_project_id.in_(seen))
    best: dict[int, Hit] = {}
    for r in session.execute(stmt):
        # Accented words of the query found as written count a little more ("thưởng" ≠ "thường").
        bonus = 0.5 * sum(1 for w in exact if w in (r.body or "").casefold())
        # Words of the query in the title (or the section heading) say more than in the body.
        named = re.findall(r"[a-z0-9]+", fold(f"{r.title} {r.heading or ''}"))
        bonus += sum(1.0 for w in words if w in named)
        score = int(r.covered) + float(r.rank) + bonus
        if r.document_id in best and best[r.document_id].score >= score:
            continue
        best[r.document_id] = Hit(
            document_id=r.document_id,
            title=r.title,
            category=r.category,
            business_project_id=r.business_project_id,
            business_project=r.name,
            heading=r.heading,
            snippet=snippet(r.body, words),
            score=score,
        )
    return sorted(best.values(), key=lambda h: -h.score)[:limit]
