"""Jira projects registered as a DPI task source (docs/specs/27 §4)."""

from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.models.base import Base, TimestampMixin


class JiraProject(TimestampMixin, Base):
    __tablename__ = "jira_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)  # ABC
    name: Mapped[str | None]
    # The dự án its issues count for in team reports and permissions; NULL: admins only.
    business_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_projects.id", ondelete="SET NULL"), index=True
    )
    enabled: Mapped[bool] = mapped_column(server_default="true")
    # Issues updated before this are in; advances only when a whole sync of the project ends.
    synced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]
    issues_count: Mapped[int] = mapped_column(server_default="0")
