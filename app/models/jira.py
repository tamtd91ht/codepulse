"""Jira projects registered as a DPI task source (docs/specs/27 §4) and the CodePulse dự án
their issues count for (§15: one Jira project can feed several, e.g. FE and BE)."""

from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.models.base import Base, TimestampMixin


class JiraProject(TimestampMixin, Base):
    __tablename__ = "jira_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)  # ABC
    name: Mapped[str | None]
    enabled: Mapped[bool] = mapped_column(server_default="true")
    # Issues updated before this are in; advances only when a whole sync of the project ends.
    synced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]
    issues_count: Mapped[int] = mapped_column(server_default="0")


class JiraProjectScope(TimestampMixin, Base):
    """One CodePulse dự án a Jira project's issues count for (docs/specs/27 §15). With
    `components` / `labels`: the issues having one of them (case-insensitive); with neither:
    "every other issue", the ones no rule of the project matches. A Jira project with no scope
    is the admins' only."""

    __tablename__ = "jira_project_scopes"
    __table_args__ = (UniqueConstraint("jira_project_id", "business_project_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    jira_project_id: Mapped[int] = mapped_column(
        ForeignKey("jira_projects.id", ondelete="CASCADE"), index=True
    )
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    components: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    labels: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    position: Mapped[int] = mapped_column(server_default="0")  # order on the screen
