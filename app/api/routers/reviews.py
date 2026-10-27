"""Reviews and findings (docs/specs/09), scoped by role (developer: own; lead: team; admin: all)."""

from datetime import UTC, date, datetime, time
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date, Select, exists, func, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.schemas import (
    BoardFindingOut,
    FindingOut,
    Page,
    RerunIn,
    ReviewDetail,
    ReviewListItem,
    ReviewOut,
    decode_cursor,
    encode_cursor,
)
from app.api.security import CurrentPrincipal, LeadPrincipal, Principal
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.models import (
    Commit,
    Connection,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    TeamMember,
    User,
)
from app.webhook.jobs import REVIEW_RETRY, review_job_timeout

router = APIRouter(tags=["reviews"])
SessionDep = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=200)]


def scoped[Q: Select[Any] | Select[Any, Any, Any]](query: Q, principal: Principal) -> Q:
    visible = principal.visible_users()
    return query if visible is None else cast(Q, query.where(Review.author_user_id.in_(visible)))


def _range(
    query: Select[Any], column: Any, date_from: date | None, date_to: date | None
) -> Select[Any]:
    if date_from:
        query = query.where(column >= datetime.combine(date_from, time.min, UTC))
    if date_to:
        query = query.where(column <= datetime.combine(date_to, time.max, UTC))
    return query


def _target(session: Session, review: Review) -> tuple[Project | None, str | None, str]:
    """Project, GitLab web URL and a short label for a review's MR or commit."""
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        project = session.get(Project, mr.project_id) if mr else None
        label, path = (f"MR !{mr.iid}", f"/-/merge_requests/{mr.iid}") if mr else ("MR", None)
    else:
        commit = session.get(Commit, review.commit_sha) if review.commit_sha else None
        project = session.get(Project, commit.project_id) if commit and commit.project_id else None
        sha = review.commit_sha or ""
        label, path = f"commit {sha[:8]}", f"/-/commit/{sha}"
    url = None
    if project and path:
        connection = session.get(Connection, project.connection_id)
        if connection:
            url = f"{connection.base_url}/{project.path_with_namespace}{path}"
    return project, url, label


@router.get("/reviews", response_model=Page[ReviewListItem])
def list_reviews(
    principal: CurrentPrincipal,
    session: SessionDep,
    user_id: int | None = None,
    project_id: int | None = None,
    severity: str | None = None,
    verdict: Literal["approve", "needs_human", "fix_required", "block"] | None = None,
    trigger: str | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[Any]:
    if user_id is not None and not principal.can_see_user(user_id):
        raise HTTPException(403, "not allowed to see this person's reviews")
    query = scoped(select(Review), principal)
    if user_id is not None:
        query = query.where(Review.author_user_id == user_id)
    if verdict is not None:
        query = query.where(Review.verdict == verdict)
    if trigger is not None:
        query = query.where(Review.trigger == trigger)
    if project_id is not None:
        mr_ids = select(MergeRequest.id).where(MergeRequest.project_id == project_id)
        shas = select(Commit.sha).where(Commit.project_id == project_id)
        query = query.where(Review.mr_id.in_(mr_ids) | Review.commit_sha.in_(shas))
    if severity:
        with_severity = select(ReviewFinding.review_id).where(ReviewFinding.severity == severity)
        query = query.where(Review.id.in_(with_severity))
    query = _range(query, Review.finished_at, date_from, date_to)
    if (after := decode_cursor(cursor)) is not None:
        query = query.where(Review.id < after)
    rows = list(session.scalars(query.order_by(Review.id.desc()).limit(limit + 1)))
    if user_id is not None and user_id != principal.user_id:
        audit(session, principal.user_id, "reviews.read", f"user:{user_id}")
        session.commit()
    more = len(rows) > limit
    items = []
    for review in rows[:limit]:
        project, url, label = _target(session, review)
        items.append(
            {
                **ReviewOut.model_validate(review).model_dump(),
                "target": label,
                "project": project.path_with_namespace if project else None,
                "web_url": url,
            }
        )
    return Page(items=items, next_cursor=encode_cursor(rows[limit - 1].id) if more else None)


def _visible_review(session: Session, principal: Principal, review_id: int) -> Review:
    review = session.get(Review, review_id)
    if review is None:
        raise HTTPException(404, "review not found")
    if review.author_user_id is None:
        if not principal.is_admin:
            raise HTTPException(403, "not allowed to see this review")
    elif not principal.can_see_user(review.author_user_id):
        raise HTTPException(403, "not allowed to see this review")
    return review


@router.get("/reviews/{review_id}", response_model=ReviewDetail)
def get_review(review_id: int, principal: CurrentPrincipal, session: SessionDep) -> dict[str, Any]:
    review = _visible_review(session, principal, review_id)
    findings = list(
        session.scalars(
            select(ReviewFinding)
            .where(ReviewFinding.review_id == review.id)
            .order_by(ReviewFinding.id)
        )
    )
    project, url, _ = _target(session, review)
    if review.author_user_id not in (None, principal.user_id):
        audit(session, principal.user_id, "review.read", f"review:{review.id}")
        session.commit()
    return {
        **ReviewOut.model_validate(review).model_dump(),
        "findings": findings,
        "project": project.path_with_namespace if project else None,
        "web_url": url,
        "history": _history(session, review),
        "can_rerun": _can_rerun(session, review, project),
    }


def _history(session: Session, review: Review) -> list[dict[str, Any]]:
    """Every review of the same MR (all heads) or commit, oldest first."""
    if review.mr_id is not None:
        where = Review.mr_id == review.mr_id
    elif review.commit_sha is not None:
        where = Review.commit_sha == review.commit_sha
    else:
        return []
    rows = session.scalars(
        select(Review).where(Review.target_type == review.target_type, where).order_by(Review.id)
    )
    return [
        {
            "id": r.id,
            "round": r.round,
            "reviewed_sha": r.reviewed_sha,
            "status": r.status,
            "score": r.score,
            "verdict": r.verdict,
            "mode": r.mode,
            "trigger": r.trigger,
            "finished_at": r.finished_at,
            "basis": r.basis,
        }
        for r in rows
    ]


def _can_rerun(session: Session, review: Review, project: Project | None) -> bool:
    """A finished commit review may always run again; an MR review only while the MR is open
    and this review is of its current head (older heads are history)."""
    if project is None or not project.tracked or review.status not in ("done", "failed", "skipped"):
        return False
    if review.superseded_by is not None:
        return False
    if review.mr_id is None:
        return True
    mr = session.get(MergeRequest, review.mr_id)
    return bool(mr and mr.state == "opened" and mr.head_sha == review.reviewed_sha)


@router.post("/reviews/{review_id}/rerun", status_code=202)
def rerun_review(
    review_id: int,
    principal: LeadPrincipal,
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
    body: RerunIn | None = None,
) -> dict[str, str]:
    """Re-run a failed or skipped review; with `force` also a finished one, as a new round
    (docs/specs/17 §Lịch sử): the old round is kept and marked superseded. An MR can only be
    reviewed again while it is open and only at its current head."""
    review = _visible_review(session, principal, review_id)
    force = bool(body and body.force)
    if review.status not in ("failed", "skipped") and not (force and review.status == "done"):
        raise HTTPException(
            409, f"review is {review.status}; only failed or skipped can be re-run (force for done)"
        )
    if review.superseded_by is not None:
        raise HTTPException(409, "a newer round of this review exists already")
    project, _, _ = _target(session, review)
    if project is None:
        raise HTTPException(409, "review target is no longer known")
    if not project.tracked:
        raise HTTPException(409, "repo is not tracked; resume tracking first")
    if review.status != "done":
        review.status, review.error = "queued", None
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        if mr is None:
            raise HTTPException(409, "merge request not found")
        if force and (mr.state != "opened" or mr.head_sha != review.reviewed_sha):
            raise HTTPException(409, "only an open MR at its current head can be reviewed again")
        args: tuple[Any, ...] = (project.id, mr.iid, review.reviewed_sha)
        func, job_id = (
            "review_mr_job",
            f"review-mr-{project.id}-{mr.iid}-{review.reviewed_sha[:12]}",
        )
    else:
        args = (project.id, review.reviewed_sha)
        func, job_id = "review_commit_job", f"review-commit-{review.reviewed_sha[:12]}"
    queue.enqueue(
        f"app.workers.review_jobs.{func}",
        *args,
        trigger="rerun",
        requested_by=principal.user_id,
        force=force,
        expected_round=review.round if force else None,
        job_id=f"{job_id}-rerun-{review.id}-{review.round + 1 if force else review.round}",
        job_timeout=review_job_timeout(get_settings()),
        retry=REVIEW_RETRY,
        at_front=True,
    )
    audit(session, principal.user_id, "review.rerun", f"review:{review.id}", force=force)
    session.commit()
    return {"status": "queued"}


@router.get("/findings", response_model=Page[BoardFindingOut])
def list_findings(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    severity: Annotated[list[str] | None, Query()] = None,
    status: Literal[
        "open", "fixed", "disputed", "accepted_risk", "false_positive", "any", "counted"
    ] = "open",
    project_id: int | None = None,
    business_project_id: int | None = None,
    found_from: datetime | None = None,
    found_to: datetime | None = None,
    user_id: int | None = None,
    team_id: int | None = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[Any]:
    """Critical Board (docs/specs/10): findings of the latest review of each MR/commit.

    The dashboards link their counts here: `business_project_id` (a dự án's repos),
    `found_from`/`found_to` (reviews finished in [from, to), the dashboard's period),
    `status=any` (found in the period, whatever became of them since: dự án numbers) and
    `status=counted` (what team and person metrics count, app/scoring/aggregate.py: not a
    false positive, an active author, and `team_id` = the team the author was in that day).
    Those metrics are a snapshot of their last aggregation; this list is live, so a change
    since (a user deactivated, membership edited, a review rerun) shows here first."""
    if user_id is not None and not principal.can_see_user(user_id):
        raise HTTPException(403, "not allowed to see this person's findings")
    if status == "counted" and (found_from is None or found_to is None):
        # Only a period's metrics count this way; without one it matches no number shown.
        raise HTTPException(422, "status=counted needs found_from and found_to")
    query = scoped(
        select(ReviewFinding, Review, User)
        .join(Review, Review.id == ReviewFinding.review_id)
        .outerjoin(User, User.id == Review.author_user_id)
        .where(Review.status == "done"),
        principal,
    )
    if status == "counted":
        query = query.where(
            or_(ReviewFinding.status.is_(None), ReviewFinding.status != "false_positive"),
            Review.author_user_id.in_(select(User.id).where(User.active.is_not(False))),
        )
    elif status != "any":
        query = query.where(ReviewFinding.status == status)
    if found_from is not None:
        query = query.where(Review.finished_at >= found_from)
    if found_to is not None:
        query = query.where(Review.finished_at < found_to)
    if business_project_id is not None:
        repos = select(Project.id).where(Project.business_project_id == business_project_id)
        mr_ids = select(MergeRequest.id).where(MergeRequest.project_id.in_(repos))
        shas = select(Commit.sha).where(Commit.project_id.in_(repos))
        query = query.where(Review.mr_id.in_(mr_ids) | Review.commit_sha.in_(shas))
    if severity:
        query = query.where(ReviewFinding.severity.in_(severity))
    if user_id is not None:
        query = query.where(Review.author_user_id == user_id)
    if team_id is not None:
        if not principal.can_see_team(team_id):
            raise HTTPException(403, "not allowed to see this team")
        if status == "counted":
            # Membership on the review's day (REPORT_TZ), as the team's numbers attribute it.
            day = sql_cast(func.timezone(settings.report_tz, Review.finished_at), Date)
            query = query.where(
                exists().where(
                    TeamMember.team_id == team_id,
                    TeamMember.user_id == Review.author_user_id,
                    TeamMember.joined_at <= day,
                    or_(TeamMember.left_at.is_(None), TeamMember.left_at > day),
                )
            )
        else:
            members = select(TeamMember.user_id).where(
                TeamMember.team_id == team_id, TeamMember.left_at.is_(None)
            )
            query = query.where(Review.author_user_id.in_(members))
    if project_id is not None:
        mr_ids = select(MergeRequest.id).where(MergeRequest.project_id == project_id)
        shas = select(Commit.sha).where(Commit.project_id == project_id)
        query = query.where(Review.mr_id.in_(mr_ids) | Review.commit_sha.in_(shas))
    if (after := decode_cursor(cursor)) is not None:
        query = query.where(ReviewFinding.id < after)
    rows = session.execute(query.order_by(ReviewFinding.id.desc()).limit(limit + 1)).tuples().all()
    now = datetime.now(UTC)
    items = []
    for finding, review, user in rows[:limit]:
        project, url, label = _target(session, review)
        opened = review.finished_at or now
        items.append(
            {
                **FindingOut.model_validate(finding).model_dump(),
                "project": project.path_with_namespace if project else "",
                "author": user.full_name if user else None,
                "author_user_id": review.author_user_id,
                "target": label,
                "web_url": url,
                "age_days": max(0, (now - opened).days),
            }
        )
    more = len(rows) > limit
    return Page(items=items, next_cursor=encode_cursor(rows[limit - 1][0].id) if more else None)
