from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.models.base import Base, TimestampMixin


class PeriodMetrics(TimestampMixin):
    """Columns shared by the per-user and per-team period tables (docs/specs/08)."""

    @declared_attr.directive
    def __table_args__(cls) -> tuple[CheckConstraint, ...]:
        return (CheckConstraint("period_type IN ('week','month')", name="period_type"),)

    period_type: Mapped[str] = mapped_column(primary_key=True)
    period_start: Mapped[date] = mapped_column(primary_key=True)
    reviews_count: Mapped[int | None]
    avg_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    loc_changed: Mapped[int | None]
    critical: Mapped[int | None]
    high: Mapped[int | None]
    medium: Mapped[int | None]
    low: Mapped[int | None]
    info: Mapped[int | None]
    weighted_penalty: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    defect_density: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    mr_count: Mapped[int | None]
    avg_review_time_h: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    avg_mr_size: Mapped[int | None]
    avg_review_rounds: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    build_success_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # Share 0..1 of critical/high findings fixed in a later review (08; not in 03, added later).
    fix_rate: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    quality_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))


class MetricsUserPeriod(PeriodMetrics, Base):
    __tablename__ = "metrics_user_period"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class MetricsTeamPeriod(PeriodMetrics, Base):
    __tablename__ = "metrics_team_period"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
