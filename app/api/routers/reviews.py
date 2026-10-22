"""Reviews and findings (docs/specs/09), scoped by role (developer: own; lead: team; admin: all)."""

from datetime import UTC, date, datetime, time
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.schemas import (
    BoardFindingOut,
    FindingOut,
    Page,
    ReviewDetail,
    ReviewOut,
    decode_cursor,
    encode_cursor,
)
from app.api.security import CurrentPrincipal, LeadPrincipal, Principal
from app.collector.sync import Enqueuer
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
from app.webhook.jobs import REVIEW_JOB_TIMEOUT, REVIEW_RETRY

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


@router.get("/reviews", response_model=Page[ReviewOut])
def list_reviews(
    principal: CurrentPrincipal,
    session: SessionDep,
    user_id: int | None = None,
    project_id: int | None = None,
    severity: str | None = None,
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
    return Page(items=rows[:limit], next_cursor=encode_cursor(rows[limit - 1].id) if more else None)


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
    }


@router.post("/reviews/{review_id}/rerun", status_code=202)
def rerun_review(
    review_id: int,
    principal: LeadPrincipal,
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, str]:
    """Re-run a failed or skipped review. A finished review is final for its sha and rubric:
    a new push or a new rubric version produces a new review instead."""
    review = _visible_review(session, principal, review_id)
    if review.status not in ("failed", "skipped"):
        raise HTTPException(409, f"review is {review.status}; only failed or skipped can be re-run")
    project, _, _ = _target(session, review)
    if project is None:
        raise HTTPException(409, "review target is no longer known")
    review.status, review.error = "queued", None
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        if mr is None:
            raise HTTPException(409, "merge request not found")
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
        job_id=f"{job_id}-rerun-{review.id}",
        job_timeout=REVIEW_JOB_TIMEOUT,
        retry=REVIEW_RETRY,
    )
    audit(session, principal.user_id, "review.rerun", f"review:{review.id}")
    session.commit()
    return {"status": "queued"}


@router.get("/findings", response_model=Page[BoardFindingOut])
def list_findings(
    principal: CurrentPrincipal,
    session: SessionDep,
    severity: Annotated[list[str] | None, Query()] = None,
    status: str = "open",
    project_id: int | None = None,
    user_id: int | None = None,
    team_id: int | None = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[Any]:
    """Critical Board (docs/specs/10): findings of the latest review of each MR/commit."""
    if user_id is not None and not principal.can_see_user(user_id):
        raise HTTPException(403, "not allowed to see this person's findings")
    query = scoped(
        select(ReviewFinding, Review, User)
        .join(Review, Review.id == ReviewFinding.review_id)
        .outerjoin(User, User.id == Review.author_user_id)
        .where(Review.status == "done", ReviewFinding.status == status),
        principal,
    )
    if severity:
        query = query.where(ReviewFinding.severity.in_(severity))
    if user_id is not None:
        query = query.where(Review.author_user_id == user_id)
    if team_id is not None:
        if not principal.can_see_team(team_id):
            raise HTTPException(403, "not allowed to see this team")
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
