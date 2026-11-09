"""Stetho's knowledge base (user 2026-11-09; docs/specs/28): documents Stetho answers from.

AssistantDoc: one document, imported from a file (.md, .txt, .docx, .xlsx), fetched from a web
link, written on the screen, saved from a corrected answer ("qa"), or built in (the CodePulse
how-tos, `source` "builtin", kept in code and refreshed when they change). AssistantDocChunk: its
pieces, each with a full-text vector over the accent-folded text, which is what a question is
matched against.
"""

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

DOC_SOURCES = ("builtin", "manual", "file", "url", "qa")
AUDIENCES = ("all", "trainers")


class AssistantDoc(TimestampMixin, Base):
    __tablename__ = "assistant_docs"
    __table_args__ = (
        CheckConstraint(f"source IN {DOC_SOURCES}", name="source"),
        CheckConstraint(f"audience IN {AUDIENCES}", name="audience"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Built-in documents: "builtin:<topic>", the key code refreshes them by; NULL otherwise.
    key: Mapped[str | None] = mapped_column(unique=True)
    title: Mapped[str]
    # The text Stetho reads (Markdown); an imported file is stored converted, not as bytes.
    body: Mapped[str]
    source: Mapped[str]
    source_name: Mapped[str | None]  # the file name, or the URL
    # Screens (path prefixes) the document is about: it ranks higher there.
    paths: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    # Words that should find it besides its text (built-ins).
    keywords: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    # all: every Stetho user; trainers: only those who may train Stetho (assistant.train).
    audience: Mapped[str] = mapped_column(server_default="all")
    enabled: Mapped[bool] = mapped_column(server_default="true")
    content_hash: Mapped[str]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AssistantDocChunk(TimestampMixin, Base):
    __tablename__ = "assistant_doc_chunks"
    __table_args__ = (Index("ix_assistant_doc_chunks_search", "search", postgresql_using="gin"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(
        ForeignKey("assistant_docs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    heading: Mapped[str | None]  # "Mục > Mục con" the piece sits under
    body: Mapped[str]
    # TSVECTOR on PostgreSQL; plain text where tests build the schema on SQLite.
    search: Mapped[Any | None] = mapped_column(TSVECTOR().with_variant(String(), "sqlite"))
