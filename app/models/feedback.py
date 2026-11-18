"""Phản ánh & Đề xuất (docs/specs/32, user 2026-11-18): what people report about CodePulse or
ask of it, analysed by the LLM into a structured Markdown note on a virtual tree (lĩnh vực ▸
chức năng ▸ phản ánh), triaged by whoever holds `feedback.manage`, and exported in batches as one
Markdown file a coding agent works from.

FeedbackItem: one report, always with its author. FeedbackExport: one exported batch, kept as the
exact file handed over (downloaded again as is).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.models.base import Base, TimestampMixin

FEEDBACK_KINDS = ("bug", "feature", "improvement", "question")
FEEDBACK_STATUSES = ("new", "accepted", "planned", "done", "rejected")
ANALYSIS_STATUSES = ("queued", "analyzing", "ready", "failed")
PRIORITIES = ("p1", "p2", "p3")
FEEDBACK_AREAS = (
    "overview",
    "review",
    "tasks",
    "testcases",
    "docs",
    "dpi",
    "reports",
    "insights",
    "stetho",
    "admin",
    "integrations",
    "general",
)


class FeedbackItem(TimestampMixin, Base):
    __tablename__ = "feedback_items"
    __table_args__ = (
        CheckConstraint(f"kind IN {FEEDBACK_KINDS}", name="kind"),
        CheckConstraint(f"status IN {FEEDBACK_STATUSES}", name="status"),
        CheckConstraint(f"analysis_status IN {ANALYSIS_STATUSES}", name="analysis_status"),
        CheckConstraint(f"area IN {FEEDBACK_AREAS}", name="area"),
        CheckConstraint(f"ai_area IS NULL OR ai_area IN {FEEDBACK_AREAS}", name="ai_area"),
        CheckConstraint(f"ai_kind IS NULL OR ai_kind IN {FEEDBACK_KINDS}", name="ai_kind"),
        CheckConstraint(f"priority IS NULL OR priority IN {PRIORITIES}", name="priority"),
        Index("ix_feedback_items_author_id_created_at", "author_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str]  # as the author picked it
    area: Mapped[str] = mapped_column(server_default="general")  # as the author picked it
    title: Mapped[str]
    body: Mapped[str] = deferred(mapped_column(String, nullable=False))
    page_url: Mapped[str | None]  # the page the author was on
    status: Mapped[str] = mapped_column(server_default="new", index=True)
    triage_note: Mapped[str | None]  # the manager's note: shown to the author, exported
    # The LLM's reading (§5); a manager's edits overwrite ai_area / feature / priority.
    analysis_status: Mapped[str] = mapped_column(server_default="queued")
    ai_kind: Mapped[str | None]
    ai_area: Mapped[str | None] = mapped_column(index=True)
    feature: Mapped[str | None]
    priority: Mapped[str | None]
    ai_title: Mapped[str | None]
    summary: Mapped[str | None]
    analysis_md: Mapped[str | None] = deferred(mapped_column(String))
    # {"problem", "proposal", "acceptance": [], "tags": [], "duplicates": [ids], "questions": []}
    ai_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None]
    model: Mapped[str | None]
    cost_usd: Mapped[float | None] = mapped_column(Float)
    # The run token: a stale run asked again cannot overwrite the newer one.
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    export_count: Mapped[int] = mapped_column(server_default="0", default=0)
    triaged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeedbackExport(Base):
    __tablename__ = "feedback_exports"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    note: Mapped[str | None]
    item_ids: Mapped[list[int]] = mapped_column(JSONB)
    body: Mapped[str] = deferred(mapped_column(String, nullable=False))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
