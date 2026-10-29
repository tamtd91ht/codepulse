"""Per-member evaluation (the Thành viên page; outside docs/specs/10, requested by the user
2026-10-27). Live over `reviews` for any date range, scoped like everything personal
(docs/specs/09 RBAC): a developer sees their own row, a lead the people they lead, admin all.
A manager gets only their own row (Phase 0: aggregates only).
The maths is app/scoring/evaluation.py.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.schemas import MemberEvaluationDetail, MembersEvaluation, UserOut
from app.api.security import CurrentPrincipal, Principal
from app.config import Settings, get_settings
from app.models import (
    Commit,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    Team,
    TeamMember,
    User,
)
from app.scoring.aggregate import period_bounds
from app.scoring.evaluation import (
    Bucket,
    FindingFacts,
    ReviewFacts,
    evaluate_member,
    evaluate_members,
)

router = APIRouter(tags=["evaluation"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
DEFAULT_DAYS = 30
MAX_DAYS = 366
MIN_YEAR, MAX_YEAR = 2000, 2100


def _range(
    tz: ZoneInfo, date_from: date | None, date_to: date | None
) -> tuple[date, date, datetime, datetime]:
    """[from, to] as days in REPORT_TZ (default: the last 30 days up to today) and the matching
    [lo, hi) instants for `Review.counted_at` (when the change was made)."""
    for day in (date_from, date_to):
        if day is not None and not MIN_YEAR <= day.year <= MAX_YEAR:
            raise HTTPException(422, f"dates must fall between {MIN_YEAR} and {MAX_YEAR}")
    end = date_to or datetime.now(tz).date()
    start = date_from or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise HTTPException(422, "from must not be after to")
    if (end - start).days >= MAX_DAYS:
        raise HTTPException(422, f"the range may cover at most {MAX_DAYS} days")
    lo = period_bounds(start, "week", tz)[0]
    hi = period_bounds(end + timedelta(days=1), "week", tz)[0]
    return start, end, lo, hi


def _load(
    session: Session,
    users: set[int] | None,
    lo: datetime,
    hi: datetime,
    *,
    viewer: Principal,
    business_project_id: int | None = None,
    project_id: int | None = None,
) -> list[ReviewFacts]:
    """Finished reviews in [lo, hi) by active people in `users` (None: anyone), with the repo
    of their MR / commit and their findings. Only the columns the counts need are read (no
    summaries, excerpts or JSONB), and findings come through a join, not an id list, so a year
    of reviews stays one bounded query each."""
    # The repo is the MR's or, for a commit review, the commit's.
    repo_of_mr = select(MergeRequest.id, MergeRequest.project_id).subquery()
    repo_of_commit = select(Commit.sha, Commit.project_id).subquery()
    repo_id = func.coalesce(repo_of_mr.c.project_id, repo_of_commit.c.project_id)
    # Inactive people are left out like the stored metrics leave them out (scoring/aggregate).
    active = select(User.id).where(User.active.is_not(False))
    reviews = (
        select(
            Review.id.label("review_id"),
            Review.author_user_id,
            Review.finished_at,
            Review.counted_at.label("counted_at"),
            Review.score,
            Review.loc_reviewed,
            Review.target_type,
            Review.verdict,
            Review.rubric_version,
            Project.id.label("project_id"),
            Project.path_with_namespace,
        )
        .outerjoin(repo_of_mr, repo_of_mr.c.id == Review.mr_id)
        .outerjoin(repo_of_commit, repo_of_commit.c.sha == Review.commit_sha)
        .outerjoin(Project, Project.id == repo_id)
        .where(
            Review.status == "done",
            Review.author_user_id.in_(active),
            Review.counted_at >= lo,
            Review.counted_at < hi,
        )
    )
    if users is not None:
        reviews = reviews.where(Review.author_user_id.in_(users))
    # Only work in the dự án the viewer is granted, besides their own (docs/specs/09).
    if (granted := viewer.visible_projects()) is not None:
        reviews = reviews.where(
            or_(
                Review.author_user_id == viewer.user_id,
                Project.business_project_id.in_(granted),
            )
        )
    if project_id is not None:
        reviews = reviews.where(Project.id == project_id)
    if business_project_id is not None:
        reviews = reviews.where(Project.business_project_id == business_project_id)
    facts: dict[int, ReviewFacts] = {}
    for row in session.execute(reviews):
        if row.author_user_id is None or row.finished_at is None:
            continue
        facts[row.review_id] = ReviewFacts(
            id=row.review_id,
            user_id=row.author_user_id,
            finished_at=row.finished_at,
            score=float(row.score) if row.score is not None else None,
            loc=row.loc_reviewed or 0,
            target_type=row.target_type,
            verdict=row.verdict,
            rubric_version=row.rubric_version,
            project_id=row.project_id,
            project_path=row.path_with_namespace,
            counted_at=row.counted_at,
        )
    if facts:
        picked = reviews.subquery()
        findings = select(
            ReviewFinding.review_id,
            ReviewFinding.severity,
            ReviewFinding.category,
            ReviewFinding.confidence,
            ReviewFinding.status,
            ReviewFinding.verified,
        ).join(picked, picked.c.review_id == ReviewFinding.review_id)
        for f in session.execute(findings):
            review = facts.get(f.review_id) if f.review_id is not None else None
            if review is None:
                continue
            review.findings.append(
                FindingFacts(
                    severity=f.severity,
                    category=f.category,
                    confidence=float(f.confidence or 1),
                    status=f.status or "open",
                    verified=f.verified or "skipped",
                )
            )
    return list(facts.values())


def _team_members(session: Session, team_id: int, start: date, end: date) -> set[int]:
    """People in the team at any time during [start, end]."""
    return set(
        session.scalars(
            select(TeamMember.user_id).where(
                TeamMember.team_id == team_id,
                TeamMember.joined_at <= end,
                (TeamMember.left_at.is_(None)) | (TeamMember.left_at > start),
            )
        )
    )


def _teams_of(session: Session, user_ids: set[int], end: date) -> dict[int, list[str]]:
    rows = session.execute(
        select(TeamMember.user_id, Team.name)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            TeamMember.user_id.in_(user_ids),
            TeamMember.joined_at <= end,
            TeamMember.left_at.is_(None),
        )
        .order_by(Team.name)
    ).tuples()
    out: dict[int, list[str]] = {}
    for user_id, name in rows:
        out.setdefault(user_id, []).append(name)
    return out


def _scope(principal: Principal, team_id: int | None) -> set[int] | None:
    if team_id is not None and not principal.can_see_team(team_id):
        raise HTTPException(403, "not allowed to see this team")
    return principal.visible_users()


@router.get("/members/evaluation", response_model=MembersEvaluation)
def members_evaluation(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    team_id: int | None = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
) -> dict[str, Any]:
    """One row per person in the viewer's scope who had a finished review in the range (with
    the filters), plus everyone in scope who had none when no repo filter narrows it."""
    tz = ZoneInfo(settings.report_tz)
    start, end, lo, hi = _range(tz, date_from, date_to)
    visible = _scope(principal, team_id)
    if business_project_id is not None and not principal.can_view_project(business_project_id):
        raise HTTPException(404, "dự án not found")
    if project_id is not None:
        repo = session.get(Project, project_id)
        if repo is None or not principal.can_view_project(repo.business_project_id):
            raise HTTPException(404, "repo not found")
    if team_id is not None:
        if session.get(Team, team_id) is None:
            raise HTTPException(404, "team not found")
        members = _team_members(session, team_id, start, end)
        visible = members if visible is None else visible & members
    reviews = _load(
        session,
        visible,
        lo,
        hi,
        viewer=principal,
        business_project_id=business_project_id,
        project_id=project_id,
    )
    result = evaluate_members(reviews, start, end, tz)
    listed = {m["user_id"] for m in result["members"]}
    # People in scope without a review still get a row (0 reviews): a lead wants to see who was
    # not reviewed at all, not only who was. Not with a repo filter: "no review in this repo" is
    # not a fact about the person.
    if business_project_id is None and project_id is None:
        query = select(User.id).where(User.active.is_not(False))
        if visible is not None:
            query = query.where(User.id.in_(visible))
        listed |= set(session.scalars(query))
    users = {u.id: u for u in session.scalars(select(User).where(User.id.in_(listed)))}
    teams = _teams_of(session, set(users), end)
    rows = [
        {
            **m,
            "user": UserOut.model_validate(users[m["user_id"]]),
            "teams": teams.get(m["user_id"], []),
        }
        for m in result["members"]
        if m["user_id"] in users
    ]
    rows += [
        {
            **Bucket().as_dict(),
            "user_id": user_id,
            "user": UserOut.model_validate(user),
            "teams": teams.get(user_id, []),
            "trend": [None] * len(result["weeks"]),
        }
        for user_id, user in sorted(users.items(), key=lambda kv: (kv[1].full_name or "", kv[0]))
        if user_id not in {m["user_id"] for m in result["members"]}
    ]
    if visible is None or visible - {principal.user_id}:
        audit(
            session,
            principal.user_id,
            "evaluation.read",
            "members",
            **_filters(start, end, team_id, business_project_id, project_id),
        )
        session.commit()
    return {
        "from": start,
        "to": end,
        "range": (lo, hi),
        "weeks": result["weeks"],
        "totals": result["totals"],
        "members": rows,
    }


def _filters(
    start: date, end: date, team_id: int | None, bp_id: int | None, project_id: int | None
) -> dict[str, Any]:
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        **({"team_id": team_id} if team_id is not None else {}),
        **({"business_project_id": bp_id} if bp_id is not None else {}),
        **({"project_id": project_id} if project_id is not None else {}),
    }


@router.get("/members/{user_id}/evaluation", response_model=MemberEvaluationDetail)
def member_evaluation(
    user_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    """One person over the range: summary, per-repo rows, verdict / severity / category mix."""
    if not principal.can_see_user(user_id):
        raise HTTPException(403, "individual metrics are visible to the person and their lead only")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "person not found")
    tz = ZoneInfo(settings.report_tz)
    start, end, lo, hi = _range(tz, date_from, date_to)
    result = evaluate_member(_load(session, {user_id}, lo, hi, viewer=principal), start, end, tz)
    if user_id != principal.user_id:
        audit(
            session,
            principal.user_id,
            "evaluation.read",
            f"user:{user_id}",
            **_filters(start, end, None, None, None),
        )
        session.commit()
    return {
        "user": UserOut.model_validate(user),
        "from": start,
        "to": end,
        "range": (lo, hi),
        **result,
    }
