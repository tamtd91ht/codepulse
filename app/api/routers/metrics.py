"""Metrics endpoints (docs/specs/09). Phase 0: individual scores for the person and their lead
only; managers get team aggregates."""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.schemas import (
    CategoryWeek,
    Hotspots,
    Leaderboard,
    MetricsOut,
    MetricsSeries,
    TeamOut,
    UserOut,
)
from app.api.security import CurrentPrincipal, LeadOrManagerPrincipal
from app.config import Settings, get_settings
from app.models import (
    MetricsTeamPeriod,
    MetricsUserPeriod,
    Review,
    ReviewFinding,
    Team,
    TeamMember,
    User,
)
from app.reporting.report import Audience, build_team_report, render
from app.scoring.aggregate import (
    PeriodType,
    next_period,
    period_bounds,
    period_start_of,
    previous_period,
)
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
    settings: Annotated[Settings, Depends(get_settings)],
    period: Period = "month",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    if session.get(Team, team_id) is None:
        raise HTTPException(404, "team not found")
    if not principal.can_see_team(team_id):
        raise HTTPException(403, "not allowed to see this team")
    start, end = _default_range(period, date_from, date_to)
    tz = ZoneInfo(settings.report_tz)
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
        "period_range": period_bounds(end, period, tz),
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
        rows = list(
            session.execute(
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
        )
        # Week-aligned, ending with the period's last week (also when the period is a month).
        trend_end = period_start_of(next_period(period_start, period) - timedelta(days=1), "week")
        trend_start = trend_end - timedelta(weeks=7)
        history: dict[int, dict[date, Any]] = {}
        for row in session.scalars(
            select(MetricsUserPeriod).where(
                MetricsUserPeriod.user_id.in_([u.id for _, u in rows]),
                MetricsUserPeriod.period_type == "week",
                MetricsUserPeriod.period_start.between(trend_start, trend_end),
            )
        ):
            history.setdefault(row.user_id, {})[row.period_start] = row.quality_index
        weeks = [trend_start + timedelta(weeks=i) for i in range(8)]
        members = [
            {
                **MetricsOut.model_validate(m).model_dump(),
                "user": UserOut.model_validate(u),
                "trend": [history.get(u.id, {}).get(w) for w in weeks],
                # Someone who left the team since: in its numbers, but the lead no longer sees
                # their findings, so the row's counts cannot link to them.
                "viewable": principal.can_see_user(u.id),
            }
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


def _team_member_ids(session: Session, team_id: int, start: date, end: date) -> list[int]:
    """People who were in the team at any time during [start, end)."""
    return list(
        session.scalars(
            select(TeamMember.user_id).where(
                TeamMember.team_id == team_id,
                TeamMember.joined_at < end,
                (TeamMember.left_at.is_(None)) | (TeamMember.left_at > start),
            )
        )
    )


@router.get("/metrics/teams/{team_id}/hotspots", response_model=Hotspots)
def team_hotspots(
    team_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    period: Period = "month",
    start: date | None = None,
) -> dict[str, Any]:
    """Top 5 error categories and "hot" files of a team (docs/specs/08 §4, 10 Team Overview)."""
    if not principal.can_see_team(team_id):
        raise HTTPException(403, "not allowed to see this team")
    tz = ZoneInfo(settings.report_tz)
    period_start = period_start_of(start or datetime.now(tz).date(), period)
    lo, hi = period_bounds(period_start, period, tz)
    members = _team_member_ids(session, team_id, period_start, next_period(period_start, period))
    base = (
        select(ReviewFinding)
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(
            Review.status == "done",
            Review.author_user_id.in_(members),
            Review.finished_at >= lo,
            Review.finished_at < hi,
            ReviewFinding.status != "false_positive",
        )
        .subquery()
    )
    count = func.count().label("count")
    categories = session.execute(
        select(base.c.category, count).group_by(base.c.category).order_by(count.desc()).limit(5)
    ).all()
    files = session.execute(
        select(base.c.file_path, count)
        .where(base.c.file_path.is_not(None))
        .group_by(base.c.file_path)
        .order_by(count.desc())
        .limit(5)
    ).all()
    return {
        "categories": [{"category": c, "count": n} for c, n in categories],
        "files": [{"file_path": f, "count": n} for f, n in files],
    }


@router.get("/metrics/users/{user_id}/categories", response_model=list[CategoryWeek])
def user_category_heatmap(
    user_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    weeks: Annotated[int, Query(ge=1, le=52)] = 12,
) -> list[dict[str, Any]]:
    """Findings per category per week (Member Detail heatmap, docs/specs/10)."""
    if not principal.can_see_user(user_id):
        raise HTTPException(403, "individual metrics are visible to the person and their lead only")
    tz = ZoneInfo(settings.report_tz)
    first = period_start_of(datetime.now(tz).date(), "week") - timedelta(weeks=weeks - 1)
    rows = session.execute(
        select(Review.finished_at, ReviewFinding.category)
        .join(ReviewFinding, ReviewFinding.review_id == Review.id)
        .where(
            Review.status == "done",
            Review.author_user_id == user_id,
            Review.finished_at >= period_bounds(first, "week", tz)[0],
            ReviewFinding.status != "false_positive",
        )
    ).tuples()
    counts: Counter[tuple[date, str]] = Counter()
    for finished_at, category in rows:
        if finished_at is not None:
            counts[(period_start_of(finished_at.astimezone(tz).date(), "week"), category)] += 1
    return [
        {"week": week, "category": category, "count": n}
        for (week, category), n in sorted(counts.items())
    ]


@router.get("/teams/visible", response_model=list[TeamOut])
def visible_teams(principal: CurrentPrincipal, session: SessionDep) -> list[Team]:
    """Teams the signed-in person can open in the dashboard."""
    query = select(Team).order_by(Team.name)
    if not principal.is_admin:
        ids = principal.own_teams | principal.lead_teams | principal.managed_teams
        query = query.where(Team.id.in_(ids))
    return list(session.scalars(query))


@router.get("/people/{user_id}", response_model=UserOut)
def person(user_id: int, principal: CurrentPrincipal, session: SessionDep) -> User:
    if not principal.can_see_user(user_id):
        raise HTTPException(403, "not allowed to see this person")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "person not found")
    return user


@router.get("/reports/{period}/{start}")
def report(
    period: Literal["week", "month"],
    start: date,
    team_id: int,
    principal: LeadOrManagerPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    format: Literal["md", "html"] = "md",
) -> Response:
    """docs/specs/09 /reports: the lead version for the team's lead, aggregates for managers."""
    if not (principal.is_admin or team_id in principal.lead_teams | principal.managed_teams):
        raise HTTPException(403, "not allowed to see this team's report")
    if session.get(Team, team_id) is None:
        raise HTTPException(404, "team not found")
    audience: Audience = (
        "lead" if principal.is_admin or team_id in principal.lead_teams else "manager"
    )
    built = build_team_report(
        session,
        team_id,
        period,
        period_start_of(start, period),
        ZoneInfo(settings.report_tz),
        audience,
    )
    media = "text/markdown" if format == "md" else "text/html"
    return Response(render(built, format), media_type=f"{media}; charset=utf-8")
