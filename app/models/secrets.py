"""Values an admin sets from the dashboard instead of the environment (outside docs/specs/03;
requested by the user, see PROGRESS.md).

StoredSecret: the Claude subscription token (`claude setup-token`), set by "Đăng nhập Claude" or
by pasting a token on the Admin screen. Encrypted at rest like GitLab tokens; never returned.

AppSetting: plain review settings that override their environment default (today
MAX_REVIEW_LOC); see app/admin_settings.py for which keys exist and their bounds.
"""

from typing import Any

from sqlalchemy import ForeignKey
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
