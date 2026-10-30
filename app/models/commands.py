"""Runs of a repo's `.claude/commands` from the dashboard (outside the spec, requested by the user
2026-10-30; see PROGRESS.md and app/commands).

A row says who ran what, where (repo, branch, the shas actually used) and how it ended. The
command's output files are never stored here: they live in the run's folder until the first
download or COMMAND_FILE_TTL_HOURS, then `files_deleted_at` is set. `files` only lists their
names and sizes.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
ACTIVE = ("queued", "running")
SOURCES = ("repo", "shared")


class CommandRun(Base):
    __tablename__ = "command_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')", name="status"
        ),
        CheckConstraint("source IN ('repo','shared')", name="source"),
        Index("ix_command_runs_created_at", "created_at"),
        # At most one queued/running run per repo: two runs would race on the same budget and
        # an admin double-clicking "Chạy" must not pay twice.
        Index(
            "uq_command_runs_active_project",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running') AND project_id IS NOT NULL"),
            sqlite_where=text("status IN ('queued','running') AND project_id IS NOT NULL"),
        ),
        # The same for a shared command run without a repo, per dự án.
        Index(
            "uq_command_runs_active_business_project",
            "business_project_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running') AND project_id IS NULL"),
            sqlite_where=text("status IN ('queued','running') AND project_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    business_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_projects.id", ondelete="SET NULL")
    )
    # "repo": the repo's own .claude/commands; "shared": the dự án's shared .claude repo.
    source: Mapped[str] = mapped_column(String(10))
    command: Mapped[str] = mapped_column(String(200))  # as typed after "/", e.g. "report:weekly"
    arguments: Mapped[str] = mapped_column(server_default="")
    ref: Mapped[str | None] = mapped_column(String(255))  # the repo's branch; NULL: no repo
    repo_sha: Mapped[str | None] = mapped_column(String(64))
    shared_ref: Mapped[str | None] = mapped_column(String(255))
    shared_sha: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default="queued")
    error: Mapped[str | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    num_turns: Mapped[int | None]
    # [{"name", "size"}] of what the run handed back; the files themselves are not kept here.
    files: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Shared files the repo's own .claude replaced, and other facts about how it ran.
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    files_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
