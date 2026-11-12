"""Tài liệu dự án (docs/specs/29, user 2026-11-11): the documents of a dự án (overview,
architecture, setup, API, deployment, runbook, conventions, decisions…), uploaded as files or
written on the screen, rendered on the web and searchable.

ProjectDocument: one document of a dự án, its title, category and place; it points at its current
version. ProjectDocumentVersion: every upload or edit is a new version (never overwritten), with
its Markdown text and, for an uploaded file, the original bytes (downloaded as is, a PDF shown in
the browser's viewer). ProjectDocumentChunk: the current version's sections, each with a
full-text vector over the accent-folded text, what search matches.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.models.base import Base, TimestampMixin

DOC_CATEGORIES = (
    "overview",
    "architecture",
    "setup",
    "api",
    "deploy",
    "runbook",
    "conventions",
    "decision",
    "other",
)
# "link": written by the LLM from links (docs/specs/29 §12), approved by a person.
VERSION_SOURCES = ("upload", "editor", "template", "restore", "link")
DRAFT_MODES = ("new", "merge", "upgrade")
DRAFT_STATUSES = ("queued", "fetching", "writing", "ready", "failed", "applied", "discarded")


class ProjectDocument(TimestampMixin, Base):
    __tablename__ = "project_documents"
    __table_args__ = (CheckConstraint(f"category IN {DOC_CATEGORIES}", name="category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str]
    category: Mapped[str] = mapped_column(server_default="other")
    summary: Mapped[str | None]  # one line under the title, optional
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    pinned: Mapped[bool] = mapped_column(server_default="false", default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "project_document_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_project_documents_current_version_id",
        )
    )
    version_count: Mapped[int] = mapped_column(server_default="0", default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ProjectDocumentVersion(Base):
    __tablename__ = "project_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "number", name="uq_project_document_version"),
        CheckConstraint(f"source IN {VERSION_SOURCES}", name="source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("project_documents.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int]
    # Markdown: what is rendered and indexed.
    body: Mapped[str] = deferred(mapped_column(String, nullable=False))
    chars: Mapped[int]
    content_hash: Mapped[str]
    source: Mapped[str]
    note: Mapped[str | None]  # what changed, written by whoever saved it
    file_name: Mapped[str | None]
    file_kind: Mapped[str | None]  # ".pdf", ".docx", …
    file_size: Mapped[int | None]
    # The uploaded file itself (≤ MAX_FILE_MB), loaded only when downloaded.
    original: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    # Facts of the conversion: pages of a PDF, sheets of a workbook, the template used.
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectDocumentChunk(Base):
    __tablename__ = "project_document_chunks"
    __table_args__ = (Index("ix_project_document_chunks_search", "search", postgresql_using="gin"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("project_documents.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    heading: Mapped[str | None]  # "Mục > Mục con" the section sits under
    body: Mapped[str]
    search: Mapped[Any | None] = mapped_column(TSVECTOR().with_variant(String(), "sqlite"))


class ProjectDocumentDraft(Base):
    """Tổng hợp từ link (docs/specs/29 §12): the LLM reads up to MAX_LINKS pages or GitLab files
    and writes a document to the dự án's standard: a new one, the current one with the new facts
    added (merge), or the current one rewritten to the standard with every fact kept (upgrade).
    The result is a draft: nothing reaches the document until a person applies it (as a new
    version, source "link", the links in its meta)."""

    __tablename__ = "project_document_drafts"
    __table_args__ = (
        CheckConstraint(f"mode IN {DRAFT_MODES}", name="mode"),
        CheckConstraint(f"status IN {DRAFT_STATUSES}", name="status"),
        CheckConstraint(f"category IN {DOC_CATEGORIES}", name="category"),
        Index("ix_project_document_drafts_created_by_status", "created_by", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    # The document merged into / upgraded; for "new", the one created when applied.
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_documents.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str]
    category: Mapped[str]
    urls: Mapped[list[str]] = mapped_column(JSONB)
    instruction: Mapped[str | None]  # what to focus on, in the requester's words
    status: Mapped[str] = mapped_column(server_default="queued")
    error: Mapped[str | None]
    # The document version the LLM started from (a newer one at apply time is a conflict).
    base_version: Mapped[int | None]
    title: Mapped[str | None]
    summary: Mapped[str | None]
    body: Mapped[str | None] = deferred(mapped_column(String))
    # {"changes": [...], "gaps": [...], "conflicts": [...]}: what the LLM says it did.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Uploaded files used as sources: [{"name", "kind", "size", "text", "truncated"}]; only
    # their text is kept (cleared when the draft is dropped), never the file.
    files: Mapped[list[dict[str, Any]] | None] = deferred(mapped_column(JSONB))
    # One per source: {"url" | "name", "title", "via": "web"|"gitlab"|"file", "chars", "error"}.
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    model: Mapped[str | None]
    cost_usd: Mapped[float | None] = mapped_column(Float)
    applied_version: Mapped[int | None]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The job's run token: a run given up as stale and asked again cannot overwrite the new one.
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Who asked for this run (the creator, or whoever asked to write it again): its cost is theirs.
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
