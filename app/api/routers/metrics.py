"""Metrics endpoints (docs/specs/09). Phase 0: individual scores for the person and their lead
only; managers get team aggregates."""

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.access import review_scope, reviews_in_projects
from app.api.audit import audit
from app.api.deps import get_session
from app.api.personal import at_least, covers_person, covers_team, sees_team, visible_team_ids
from app.api.schemas import (
    CategoryWeek,
    Hotspots,
    Leaderboard,
    MetricsOut,
    MetricsSeries,
    TeamOut,
    UserOut,
)
from app.api.security import CurrentPrincipal, Principal, require
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
    LiveTeamWindow,
    PeriodType,
    compute_team_window,
    next_period,
    period_bounds,
    period_start_of,
    previous_period,
)
from app.scoring.compare import _delta as compare_delta
from app.scoring.compare import compare_team, compare_user

router = APIRouter(prefix="", tags=["metrics"])
SessionDep = Annotated[Session, Depends(get_session)]
Period = Annotated[Literal["week", "month"], Query()]
# docs/specs/10: the Team page also takes whole days (period=range, start..end inclusive, e.g.
# "Hôm nay": today..today, 00:00 to now), computed from the reviews when asked.
TeamPeriod = Annotated[Literal["week", "month", "range"], Query()]
RANGE_MAX_DAYS = 366
# docs/specs/21: stored per-person scores, team numbers (leaderboard, report), evaluation.
PersonMetrics = Annotated[Principal, require("metrics.person")]
TeamMetrics = Annotated[Principal, require("metrics.team")]


def _team_wide(principal: Annotated[Principal, require("metrics.team")]) -> Principal:
    """The leaderboard and the weekly report rank people: `metrics.team` at "team" or wider
    (a member's "own" scope is their own team's aggregates only)."""
    if principal.scope("metrics.team") in (None, "own"):
        raise HTTPException(403, "thiếu quyền xem chỉ số team")
    return principal


TeamWideMetrics = Annotated[Principal, Depends(_team_wide)]
EvaluationViewer = Annotated[Principal, require("evaluation.view")]


def _leads_team(principal: Principal, team_id: int) -> bool:
    """The lead's version of a team's numbers, with per-person rows: the team's lead holding
    `metrics.person` over their team, or admin. Never by managing the team (Phase 0: a manager
    sees aggregates only), whatever the sets say."""
    return principal.is_admin or (
        team_id in principal.lead_teams and at_least(principal, "metrics.person", "team")
    )


def _day_range(
    start: date | None, end: date | None, settings: Settings
) -> tuple[date, date, ZoneInfo]:
    """[start, end] of whole days in REPORT_TZ for period=range, the end clipped to today."""
    tz = ZoneInfo(settings.report_tz)
    today = datetime.now(tz).date()
    if start is None or end is None:
        raise HTTPException(422, "period=range needs start and end")
    if start.year < 2000:
        raise HTTPException(422, "start must be in 2000 or later")
    if start > today:
        raise HTTPException(422, "start is in the future")
    end = min(end, today)
    if end < start:
        raise HTTPException(422, "end is before start")
    if (end - start).days >= RANGE_MAX_DAYS:
        raise HTTPException(422, f"the range may cover at most {RANGE_MAX_DAYS} days")
    return start, end, tz


def _live(
    session: Session, settings: Settings, team_id: int, first: date, last: date, tz: ZoneInfo
) -> LiveTeamWindow:
    return compute_team_window(
        session,
        team_id,
        first,
        last,
        tz,
        settings.scoring_weights,
        settings.min_sample,
        min_confidence_unverified=settings.verify_min_confidence_unverified,
    )


def _range_row(row: dict[str, Any] | None, start: date) -> dict[str, Any] | None:
    """A computed row shaped as a stored one (period_type "range", starting on `start`)."""
    return None if row is None else {**row, "period_type": "range", "period_start": start}


def _bounds(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time.min, tz),
        datetime.combine(end + timedelta(days=1), time.min, tz),
    )


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
    principal: PersonMetrics,
    session: SessionDep,
    period: Period = "week",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    if not covers_person(session, principal, "metrics.person", user_id):
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
    period: TeamPeriod = "month",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    if session.get(Team, team_id) is None:
        raise HTTPException(404, "team not found")
    if not sees_team(principal, team_id):
        raise HTTPException(403, "not allowed to see this team")
    if period == "range":
        return _team_range(session, settings, team_id, start, end)
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


def _team_range(
    session: Session, settings: Settings, team_id: int, start: date | None, end: date | None
) -> dict[str, Any]:
    """period=range: the team's numbers over the days, against the as-long days right before;
    the chart keeps the stored weeks up to the range's last week."""
    first, last, tz = _day_range(start, end, settings)
    days = (last - first).days + 1
    current = _live(session, settings, team_id, first, last, tz).team
    previous = _live(
        session, settings, team_id, first - timedelta(days=days), first - timedelta(days=1), tz
    ).team
    last_week = period_start_of(last, "week")
    first_week = last_week - timedelta(weeks=11)
    series = session.scalars(
        select(MetricsTeamPeriod)
        .where(
            MetricsTeamPeriod.team_id == team_id,
            MetricsTeamPeriod.period_type == "week",
            MetricsTeamPeriod.period_start.between(first_week, last_week),
        )
        .order_by(MetricsTeamPeriod.period_start)
    ).all()
    return {
        "owner": f"team:{team_id}",
        "series": series,
        "current": _range_row(current, first),
        "delta": compare_delta(
            SimpleNamespace(**(current or {})), SimpleNamespace(**(previous or {}))
        ),
        "period_range": _bounds(first, last, tz),
    }


@router.get("/leaderboard", response_model=Leaderboard)
def leaderboard(
    team_id: int,
    principal: TeamWideMetrics,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    period: TeamPeriod = "week",
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    if not covers_team(principal, "metrics.team", team_id):
        raise HTTPException(403, "not allowed to see this team")
    leads_it = _leads_team(principal, team_id)
    if period == "range":
        return _leaderboard_range(session, settings, principal, team, leads_it, start, end)
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
                "viewable": covers_person(session, principal, "metrics.person", u.id),
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


def _member_trends(
    session: Session, user_ids: list[int], last_day: date
) -> tuple[list[date], dict[int, dict[date, Any]]]:
    """The 8 weeks ending with the week of `last_day`, and each person's stored quality index."""
    trend_end = period_start_of(last_day, "week")
    trend_start = trend_end - timedelta(weeks=7)
    history: dict[int, dict[date, Any]] = {}
    for row in session.scalars(
        select(MetricsUserPeriod).where(
            MetricsUserPeriod.user_id.in_(user_ids),
            MetricsUserPeriod.period_type == "week",
            MetricsUserPeriod.period_start.between(trend_start, trend_end),
        )
    ):
        history.setdefault(row.user_id, {})[row.period_start] = row.quality_index
    return [trend_start + timedelta(weeks=i) for i in range(8)], history


def _leaderboard_range(
    session: Session,
    settings: Settings,
    principal: Principal,
    team: Team,
    leads_it: bool,
    start: date | None,
    end: date | None,
) -> dict[str, Any]:
    """period=range (docs/specs/10, e.g. "Hôm nay"): the team and its people over whole days,
    computed from the reviews (nothing is stored for a range)."""
    first, last, tz = _day_range(start, end, settings)
    live = _live(session, settings, team.id, first, last, tz)
    members = None
    if leads_it:
        people = {
            u.id: u for u in session.scalars(select(User).where(User.id.in_(list(live.users))))
        }
        weeks, history = _member_trends(session, list(people), last)
        rows = sorted(
            ((uid, row) for uid, row in live.users.items() if uid in people),
            key=lambda item: (
                item[1]["quality_index"] is None,
                -(item[1]["quality_index"] or 0),
                people[item[0]].full_name or "",
            ),
        )
        members = [
            {
                **(_range_row(row, first) or {}),
                "user": UserOut.model_validate(people[uid]),
                "trend": [history.get(uid, {}).get(w) for w in weeks],
                "viewable": covers_person(session, principal, "metrics.person", uid),
            }
            for uid, row in rows
        ]
        audit(session, principal.user_id, "leaderboard.read", f"team:{team.id}", period="range")
        session.commit()
    return {
        "team": TeamOut.model_validate(team),
        "period_type": "range",
        "period_start": first,
        "team_metrics": _range_row(live.team, first),
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
    period: TeamPeriod = "month",
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Top 5 error categories and "hot" files of a team (docs/specs/08 §4, 10 Team Overview),
    over a week, a month or whole days (period=range)."""
    if not sees_team(principal, team_id):
        raise HTTPException(403, "not allowed to see this team")
    if period == "range":
        first, last, tz = _day_range(start, end, settings)
        lo, hi = _bounds(first, last, tz)
        members = _team_member_ids(session, team_id, first, last + timedelta(days=1))
    else:
        tz = ZoneInfo(settings.report_tz)
        period_start = period_start_of(start or datetime.now(tz).date(), period)
        lo, hi = period_bounds(period_start, period, tz)
        members = _team_member_ids(
            session, team_id, period_start, next_period(period_start, period)
        )
    query = (
        select(ReviewFinding)
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(
            Review.status == "done",
            Review.author_user_id.in_(members),
            Review.counted_at >= lo,
            Review.counted_at < hi,
            ReviewFinding.status != "false_positive",
        )
    )
    # File paths name repos: only those of the dự án this person is granted (docs/specs/09).
    if (visible := principal.visible_projects()) is not None:
        query = query.where(reviews_in_projects(visible))
    base = query.subquery()
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
    principal: EvaluationViewer,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    weeks: Annotated[int, Query(ge=1, le=52)] = 12,
) -> list[dict[str, Any]]:
    """Findings per category per week (Member Detail heatmap, docs/specs/10)."""
    if not covers_person(session, principal, "evaluation.view", user_id):
        raise HTTPException(403, "not allowed to see this person")
    tz = ZoneInfo(settings.report_tz)
    first = period_start_of(datetime.now(tz).date(), "week") - timedelta(weeks=weeks - 1)
    rows = session.execute(
        select(Review.counted_at, ReviewFinding.category)
        .join(ReviewFinding, ReviewFinding.review_id == Review.id)
        .where(
            Review.status == "done",
            Review.author_user_id == user_id,
            Review.counted_at >= period_bounds(first, "week", tz)[0],
            ReviewFinding.status != "false_positive",
            # Someone else's work: only what the viewer may see (docs/specs/09).
            *(
                [scope]
                if user_id != principal.user_id and (scope := review_scope(principal)) is not None
                else []
            ),
        )
    ).tuples()
    counts: Counter[tuple[date, str]] = Counter()
    for counted_at, category in rows:
        if counted_at is not None:
            counts[(period_start_of(counted_at.astimezone(tz).date(), "week"), category)] += 1
    return [
        {"week": week, "category": category, "count": n}
        for (week, category), n in sorted(counts.items())
    ]


@router.get("/teams/visible", response_model=list[TeamOut])
def visible_teams(principal: CurrentPrincipal, session: SessionDep) -> list[Team]:
    """Teams the signed-in person can open in the dashboard (`sees_team`). Everyone has their
    own teams, so this asks for no permission."""
    query = select(Team).order_by(Team.name)
    if (ids := visible_team_ids(principal)) is not None:
        query = query.where(Team.id.in_(ids))
    return list(session.scalars(query))


@router.get("/people/{user_id}", response_model=UserOut)
def person(user_id: int, principal: EvaluationViewer, session: SessionDep) -> User:
    if not covers_person(session, principal, "evaluation.view", user_id):
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
    principal: TeamWideMetrics,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    format: Literal["md", "html"] = "md",
) -> Response:
    """docs/specs/09 /reports: the lead version for the team's lead, aggregates for managers."""
    if not covers_team(principal, "metrics.team", team_id):
        raise HTTPException(403, "not allowed to see this team's report")
    if session.get(Team, team_id) is None:
        raise HTTPException(404, "team not found")
    audience: Audience = "lead" if _leads_team(principal, team_id) else "manager"
    built = build_team_report(
        session,
        team_id,
        period,
        period_start_of(start, period),
        ZoneInfo(settings.report_tz),
        audience,
        projects=principal.visible_projects(),
    )
    media = "text/markdown" if format == "md" else "text/html"
    return Response(render(built, format), media_type=f"{media}; charset=utf-8")
