"""Evidence packs: one person's month, for the 1:1 (docs/specs/20 §5.4, §6). No score."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

EVIDENCE_STATUSES = ("draft", "submitted", "reviewed", "shared")
EVIDENCE_KINDS = (
    "mr",
    "commit",
    "review_given",
    "finding_fixed",
    "incident",
    "design",
    "mentoring",
    "other",
)


class EvidencePack(TimestampMixin, Base):
    __tablename__ = "evidence_packs"
    __table_args__ = (
        UniqueConstraint("user_id", "period_start"),
        CheckConstraint(f"status IN {EVIDENCE_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    period_start: Mapped[date] = mapped_column(Date)  # the 1st of the month
    status: Mapped[str] = mapped_column(server_default="draft")
    # What the system gathered (app/evidence/snapshot.py); frozen when the person submits.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    self_notes: Mapped[str | None]
    lead_notes: Mapped[str | None]
    ai_draft: Mapped[str | None]  # 9.11; never published on its own
    goals_next: Mapped[str | None]
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    insights_version: Mapped[str]


class EvidenceItem(TimestampMixin, Base):
    """Something the person or their lead marks as worth recognising, Git or not."""

    __tablename__ = "evidence_items"
    __table_args__ = (CheckConstraint(f"kind IN {EVIDENCE_KINDS}", name="kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pack_id: Mapped[int] = mapped_column(
        ForeignKey("evidence_packs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str]
    ref: Mapped[str | None]  # an internal id or a URL
    title: Mapped[str]
    note: Mapped[str | None]
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
