"""Stetho's knowledge base (user 2026-11-09; docs/specs/28): documents Stetho answers from.

AssistantDoc: one document, imported from a file (.md, .txt, .docx, .xlsx), fetched from a web
link, written on the screen, saved from a corrected answer ("qa"), or built in (the CodePulse
how-tos, `source` "builtin", kept in code and refreshed when they change). AssistantDocChunk: its
pieces, each with a full-text vector over the accent-folded text, which is what a question is
matched against.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String
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


# -- Lịch sử Stetho (docs/specs/28 §9, user 2026-11-09): what people asked, kept 30 days --------

CONVERSATION_TAGS = (
    "codepulse_howto",
    "jira_commit",
    "quality_analysis",
    "findings_fix",
    "people_team",
    "dpi",
    "admin_config",
    "tech_general",
    "other",
)


class AssistantConversation(TimestampMixin, Base):
    """One Stetho conversation (a "Cuộc trò chuyện mới" starts the next), with its topic tag
    from the fixed list (app/assistant/history.py). Admins only see these; deleted after
    HISTORY_KEEP_DAYS."""

    __tablename__ = "assistant_conversations"
    __table_args__ = (
        CheckConstraint(f"tag IS NULL OR tag IN {CONVERSATION_TAGS}", name="tag"),
        CheckConstraint(
            "tag_source IS NULL OR tag_source IN ('ai', 'rule', 'manual')", name="tag_source"
        ),
        Index("ix_assistant_conversations_last_at", "last_at"),
        Index("ix_assistant_conversations_user_id_last_at", "user_id", "last_at"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)  # a UUID the client makes
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str]  # its first question, cut
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    turns: Mapped[int] = mapped_column(server_default="0", default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), server_default="0", default=0)
    tag: Mapped[str | None]
    tag_source: Mapped[str | None]  # ai | rule (keywords, when the AI could not) | manual
    tagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssistantTurn(TimestampMixin, Base):
    """One question and the gist of its answer (a few points, never the whole answer)."""

    __tablename__ = "assistant_turns"
    __table_args__ = (CheckConstraint("status IN ('ok', 'error', 'cancelled')", name="status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True
    )
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    question: Mapped[str]
    answer_points: Mapped[str | None]  # up to 3 key points, one per line
    answer_chars: Mapped[int] = mapped_column(server_default="0", default=0)
    status: Mapped[str]
    screen: Mapped[str | None]  # the page the question was asked on
    model: Mapped[str | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    duration_ms: Mapped[int | None]
    docs: Mapped[int] = mapped_column(server_default="0", default=0)  # knowledge pieces read
    web: Mapped[bool] = mapped_column(server_default="false", default=False)
