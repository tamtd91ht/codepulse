"""Weighted criteria sets (docs/specs/22 §3): versioned sets, given to dự án, and the score of
each review under each set of its dự án."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CriteriaSet(TimestampMixin, Base):
    __tablename__ = "criteria_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    description: Mapped[str | None]
    archived: Mapped[bool] = mapped_column(default=False)
    # The version new scores use; None only between creating the set and its first version.
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("criteria_set_versions.id", use_alter=True, ondelete="SET NULL")
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class CriteriaSetVersion(Base):
    """Immutable: a change of components or caps is a new version (docs/specs/22 §1)."""

    __tablename__ = "criteria_set_versions"
    __table_args__ = (UniqueConstraint("set_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey("criteria_sets.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int]
    components: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    apply_caps: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str | None]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectCriteriaSet(Base):
    """The sets of a dự án: at most one primary, the one metrics and reports use."""

    __tablename__ = "project_criteria_sets"
    __table_args__ = (
        Index(
            "uq_project_criteria_sets_primary",
            "business_project_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), primary_key=True
    )
    set_id: Mapped[int] = mapped_column(
        ForeignKey("criteria_sets.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    is_primary: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(default=0)


class ReviewCriteriaScore(Base):
    """A review's score under one set, at the version it was computed with."""

    __tablename__ = "review_criteria_scores"

    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("reviews.id", ondelete="CASCADE"), primary_key=True
    )
    set_id: Mapped[int] = mapped_column(
        ForeignKey("criteria_sets.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("criteria_set_versions.id", ondelete="CASCADE")
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
