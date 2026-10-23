"""Telegram bots that take review commands (outside docs/specs/03; requested by the user).

One bot per dự án (business project), optionally pinned to one repo of that dự án. The bot
token is encrypted at rest like GitLab tokens. Requests remember which chat asked for which
reviews so the result can be posted back once the worker is done.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TelegramBot(TimestampMixin, Base):
    __tablename__ = "telegram_bots"
    __table_args__ = (
        # One bot per dự án, or per (dự án, repo) when pinned; NULL repo counts as a value.
        UniqueConstraint(
            "business_project_id",
            "project_id",
            name="uq_telegram_bots_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    bot_username: Mapped[str | None]  # from getMe, shown in the dashboard
    token_encrypted: Mapped[str]
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE")
    )
    # Pinned repo ("sub project"): commands default to it and may not reach other repos; the bot
    # goes with the repo instead of silently widening to the whole dự án.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    enabled: Mapped[bool] = mapped_column(server_default="true")
    # Telegram chat ids the bot answers in; empty/NULL = any chat (private or group).
    allowed_chat_ids: Mapped[list[int] | None] = mapped_column(JSONB)
    last_update_id: Mapped[int | None] = mapped_column(BigInteger)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]


class TelegramRequest(TimestampMixin, Base):
    """A review command that queued jobs; answered in the chat when every target finished."""

    __tablename__ = "telegram_requests"
    __table_args__ = (Index("ix_telegram_requests_status_created_at", "status", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_bots.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    command: Mapped[str]
    # [{"kind": "mr", "project_id": 3, "ref": "12", "sha": "<head>", "label": "..."},
    #  {"kind": "commit", "project_id": 3, "ref": "<sha>", "sha": "<sha>", "label": "..."}]
    targets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # queued -> notified (result posted) | failed (Telegram refused delivery, see error)
    status: Mapped[str] = mapped_column(server_default="queued")
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None]
