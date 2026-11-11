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
VERSION_SOURCES = ("upload", "editor", "template", "restore")


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
