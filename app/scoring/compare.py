"""Period comparison for dashboards and reports (docs/specs/08 §3: delta vs the previous period
and the team average, task 4.5)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MetricsTeamPeriod, MetricsUserPeriod, TeamMember
from app.scoring.aggregate import PeriodType, next_period, previous_period

COMPARED = (
    "quality_index",
    "avg_score",
    "defect_density",
    "critical",
    "high",
    "build_success_rate",
)


@dataclass
class Comparison:
    current: MetricsUserPeriod | MetricsTeamPeriod | None
    previous: MetricsUserPeriod | MetricsTeamPeriod | None
    delta: dict[str, float | None]
    team_quality_index: float | None = None  # users only: their team's index in the period


def _num(value: Decimal | int | None) -> float | None:
    return float(value) if value is not None else None


def _delta(current: object, previous: object) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for name in COMPARED:
        now = _num(getattr(current, name, None))
        before = _num(getattr(previous, name, None))
        out[name] = round(now - before, 2) if now is not None and before is not None else None
    return out


def compare_user(
    session: Session, user_id: int, period_type: PeriodType, start: date
) -> Comparison:
    def row(day: date) -> MetricsUserPeriod | None:
        return session.scalars(
            select(MetricsUserPeriod).filter_by(
                user_id=user_id, period_type=period_type, period_start=day
            )
        ).first()

    current, previous = row(start), row(previous_period(start, period_type))
    last_day = next_period(start, period_type)
    team_ids = session.scalars(
        select(TeamMember.team_id).where(
            TeamMember.user_id == user_id,
            TeamMember.joined_at < last_day,
            (TeamMember.left_at.is_(None)) | (TeamMember.left_at >= last_day),
        )
    ).all()
    team_values = [
        float(t.quality_index)
        for t in session.scalars(
            select(MetricsTeamPeriod).where(
                MetricsTeamPeriod.team_id.in_(team_ids),
                MetricsTeamPeriod.period_type == period_type,
                MetricsTeamPeriod.period_start == start,
            )
        )
        if t.quality_index is not None
    ]
    team_index = round(sum(team_values) / len(team_values), 2) if team_values else None
    return Comparison(current, previous, _delta(current, previous), team_index)


def compare_team(
    session: Session, team_id: int, period_type: PeriodType, start: date
) -> Comparison:
    def row(day: date) -> MetricsTeamPeriod | None:
        return session.scalars(
            select(MetricsTeamPeriod).filter_by(
                team_id=team_id, period_type=period_type, period_start=day
            )
        ).first()

    current, previous = row(start), row(previous_period(start, period_type))
    return Comparison(current, previous, _delta(current, previous))
