"""Metrics endpoints (docs/specs/09). Phase 0: individual scores for the person and their lead
only; managers get team aggregates."""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.schemas import Leaderboard, MetricsOut, MetricsSeries, TeamOut, UserOut
from app.api.security import CurrentPrincipal, LeadOrManagerPrincipal
from app.models import MetricsTeamPeriod, MetricsUserPeriod, Team, TeamMember, User
from app.scoring.aggregate import PeriodType, next_period, period_start_of, previous_period
from app.scoring.compare import compare_team, compare_user

router = APIRouter(prefix="", tags=["metrics"])
SessionDep = Annotated[Session, Depends(get_session)]
Period = Annotated[Literal["week", "month"], Query()]


def _default_range(
    period: PeriodType, date_from: date | None, date_to: date | None
) -> tuple[date, date]:
    """Default: the last 12 weeks or 6 months, ending with the current period."""
    end = period_start_of(date_to or datetime.now(UTC).date(), period)
    if date_from:
        return period_start_of(date_from, period), end
    start = end
    for _ in range(11 if period == "week" else 5):
        start = previous_period(start, period)
    return start, end


@router.get("/metrics/users/{user_id}", response_model=MetricsSeries)
def user_metrics(
    user_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    period: Period = "week",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    if not principal.can_see_user(user_id):
        raise HTTPException(403, "individual metrics are visible to the person and their lead only")
    start, end = _default_range(period, date_from, date_to)
    series = session.scalars(
        select(MetricsUserPeriod)
        .where(
            MetricsUserPeriod.user_id == user_id,
            MetricsUserPeriod.period_type == period,
            MetricsUserPeriod.period_start.between(start, end),
        )
        .order_by(MetricsUserPeriod.period_start)
    ).all()
    comparison = compare_user(session, user_id, period, end)
    if user_id != principal.user_id:
        audit(session, principal.user_id, "metrics.user.read", f"user:{user_id}", period=period)
        session.commit()
    return {
        "owner": f"user:{user_id}",
        "series": series,
        "delta": comparison.delta,
        "team_quality_index": comparison.team_quality_index,
    }


@router.get("/metrics/teams/{team_id}", response_model=MetricsSeries)
def team_metrics(
    team_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    period: Period = "month",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    if session.get(Team, team_id) is None:
        raise HTTPException(404, "team not found")
    if not principal.can_see_team(team_id):
        raise HTTPException(403, "not allowed to see this team")
    start, end = _default_range(period, date_from, date_to)
    series = session.scalars(
        select(MetricsTeamPeriod)
        .where(
            MetricsTeamPeriod.team_id == team_id,
            MetricsTeamPeriod.period_type == period,
            MetricsTeamPeriod.period_start.between(start, end),
        )
        .order_by(MetricsTeamPeriod.period_start)
    ).all()
    return {
        "owner": f"team:{team_id}",
        "series": series,
        "delta": compare_team(session, team_id, period, end).delta,
    }


@router.get("/leaderboard", response_model=Leaderboard)
def leaderboard(
    team_id: int,
    principal: LeadOrManagerPrincipal,
    session: SessionDep,
    period: Period = "week",
    start: date | None = None,
) -> dict[str, Any]:
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    leads_it = principal.is_admin or team_id in principal.lead_teams
    if not (leads_it or team_id in principal.managed_teams):
        raise HTTPException(403, "not allowed to see this team")
    period_start = period_start_of(start or datetime.now(UTC).date(), period)
    team_row = session.scalars(
        select(MetricsTeamPeriod).filter_by(
            team_id=team_id, period_type=period, period_start=period_start
        )
    ).first()
    members = None
    if leads_it:
        last_day = next_period(period_start, period)
        rows = session.execute(
            select(MetricsUserPeriod, User)
            .join(User, User.id == MetricsUserPeriod.user_id)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.joined_at < last_day,
                (TeamMember.left_at.is_(None)) | (TeamMember.left_at >= period_start),
                MetricsUserPeriod.period_type == period,
                MetricsUserPeriod.period_start == period_start,
            )
            .order_by(MetricsUserPeriod.quality_index.desc().nulls_last())
        ).tuples()
        members = [
            {**MetricsOut.model_validate(m).model_dump(), "user": UserOut.model_validate(u)}
            for m, u in rows
        ]
        audit(session, principal.user_id, "leaderboard.read", f"team:{team_id}", period=period)
        session.commit()
    return {
        "team": TeamOut.model_validate(team),
        "period_type": period,
        "period_start": period_start,
        "team_metrics": team_row,
        "members": members,
    }
