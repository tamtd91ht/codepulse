"""Values an admin sets from the dashboard instead of the environment (outside docs/specs/03;
requested by the user, see PROGRESS.md).

StoredSecret: the Claude subscription token (`claude setup-token`), set by "Đăng nhập Claude" or
by pasting a token on the Admin screen. Encrypted at rest like GitLab tokens; never returned.

AppSetting: plain review settings that override their environment default (today
MAX_REVIEW_LOC); see app/admin_settings.py for which keys exist and their bounds.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class StoredSecret(TimestampMixin, Base):
    __tablename__ = "stored_secrets"

    name: Mapped[str] = mapped_column(primary_key=True)
    value_encrypted: Mapped[str]
    # How it was set: "login" (setup-token run from the dashboard) | "paste".
    source: Mapped[str]
    set_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    set_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class LlmAccount(TimestampMixin, Base):
    """A Claude account reviews may run on (user 2026-11-09; app/review/llm_accounts.py).

    The default one is used whenever it works; the others, in `position` order, only while it
    does not. `state` is why an account is skipped: ok | quota (usage limit) | auth (login
    failed) | model (its plan cannot use the review model); `retry_at` is when it is tried
    again. The token is encrypted like the old single stored token and never returned."""

    __tablename__ = "llm_accounts"
    __table_args__ = (
        CheckConstraint("state IN ('ok', 'quota', 'auth', 'model')", name="state"),
        # At most one default.
        Index(
            "uq_llm_accounts_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str]
    token_encrypted: Mapped[str]
    # How the token was set: "login" | "paste" | "migrated" (the single token before 2026-11-09).
    source: Mapped[str]
    set_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_default: Mapped[bool] = mapped_column(server_default="false", default=False)
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    enabled: Mapped[bool] = mapped_column(server_default="true", default=True)
    state: Mapped[str] = mapped_column(server_default="ok", default="ok")
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(server_default="0", default=0)
    last_error: Mapped[str | None]
    # state "model": the model it could not use; another model makes it usable again.
    blocked_model: Mapped[str | None]
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
