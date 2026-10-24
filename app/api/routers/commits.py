"""Review on request from the dashboard (docs/specs/17 §Yêu cầu review).

Pick a repo and a branch, optionally people and a time window, see the recent commits with
their review state, and queue reviews for the ones you choose. Same permissions as Telegram:
your own work, the work of people you lead, admins anyone; commits whose author is not mapped
to a person need a lead or admin. Reviews go through the normal worker, so scores, findings and
verdicts land in the same tables as webhook reviews.
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_secret_box, get_session
from app.api.schemas import (
    BranchOut,
    CommitListOut,
    CommitRowOut,
    RepoOut,
    RepoProfileOut,
    ReviewCommitsIn,
    ReviewCommitsOut,
    UserOut,
)
from app.api.security import AdminPrincipal, CurrentPrincipal, Principal
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.collector.gitlab.convert import Converter
from app.collector.gitlab.extract import _commit as gl_commit
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.models import Account, BusinessProject, Connection, Project, Review, User
from app.review.rubric import RUBRIC_VERSION
from app.webhook.jobs import REVIEW_JOB_TIMEOUT, REVIEW_RETRY

log = logging.getLogger(__name__)
router = APIRouter(tags=["review-requests"])
SessionDep = Annotated[Session, Depends(get_session)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

SHA = re.compile(r"^[0-9a-f]{7,40}$")
# Request-path GitLab calls: short timeout, one retry; a slow GitLab must not pin API workers.
REQUEST_TIMEOUT_S = 10.0


def _client(box: SecretBox, connection: Connection) -> GitLabClient:
    return GitLabClient(
        connection.base_url,
        box.decrypt(connection.token_encrypted),
        timeout=REQUEST_TIMEOUT_S,
        max_retries=1,
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# A review in one of these states already covers the commit; failed ones may run again.
TAKEN = ("queued", "running", "done", "skipped")
DEFAULT_WINDOW = timedelta(days=7)


def _tracked_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None or not project.tracked:
        raise HTTPException(404, "repo not found")
    return project


def _connection(session: Session, project: Project) -> Connection:
    connection = session.get(Connection, project.connection_id) if project.connection_id else None
    if connection is None:
        raise HTTPException(409, "repo has no GitLab connection")
    return connection


def _gitlab_error(exc: GitLabError, what: str) -> HTTPException:
    # Never the GitLab message itself: it may carry the internal URL (docs/specs/11).
    if exc.status_code == 404:
        return HTTPException(404, f"{what} not found on GitLab")
    log.warning("gitlab call failed", extra={"what": what, "status": exc.status_code})
    return HTTPException(502, f"GitLab did not answer for {what}; try again shortly")


@router.get("/repos", response_model=list[RepoOut])
def list_repos(principal: CurrentPrincipal, session: SessionDep) -> list[RepoOut]:
    """Tracked repos with their dự án; what anyone may ask a review for depends on authors."""
    rows = session.execute(
        select(Project, BusinessProject.name)
        .outerjoin(BusinessProject, BusinessProject.id == Project.business_project_id)
        .where(Project.tracked)
        .order_by(Project.path_with_namespace)
    ).all()
    return [
        RepoOut(
            id=p.id,
            path_with_namespace=p.path_with_namespace,
            default_branch=p.default_branch,
            review_branch=p.review_branch,
            business_project_id=p.business_project_id,
            business_project=name,
            review_enabled=bool(p.review_enabled),
        )
        for p, name in rows
    ]


@router.get("/repos/profiles", response_model=list[RepoProfileOut])
def repo_profiles(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int | None = None,
) -> list[RepoProfileOut]:
    """Project profiles (docs/specs/18): what each repo is, its stack and security model.
    Built from the repository's own documents, so anyone signed in may read them."""
    query = select(Project).where(Project.tracked).order_by(Project.path_with_namespace)
    if business_project_id is not None:
        query = query.where(Project.business_project_id == business_project_id)
    return [
        RepoProfileOut(
            id=p.id,
            path_with_namespace=p.path_with_namespace,
            business_project_id=p.business_project_id,
            deep_review=bool(p.deep_review),
            profile=p.profile,
            profile_at=p.profile_at,
            profile_sha=p.profile_sha,
        )
        for p in session.scalars(query)
    ]


@router.post("/projects/{project_id}/profile/refresh", status_code=202)
def refresh_profile(
    project_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, str]:
    """Rebuild the profile from the default branch (one LLM call, runs in the worker)."""
    project = _tracked_project(session, project_id)
    if project.allow_external_llm is False:
        raise HTTPException(409, "this repo does not allow sending code to an external LLM")
    queue.enqueue(
        "app.workers.review_jobs.refresh_profile_job",
        project.id,
        job_id=f"profile-{project.id}",
        job_timeout=REVIEW_JOB_TIMEOUT,
    )
    audit(session, principal.user_id, "project.profile_refresh", f"project:{project.id}")
    session.commit()
    return {"status": "queued"}


@router.get("/people", response_model=list[UserOut])
def list_people(principal: CurrentPrincipal, session: SessionDep) -> list[User]:
    """People whose work this person may ask a review for (admin: everyone active)."""
    query = select(User).where(User.active.is_not(False)).order_by(User.full_name, User.id)
    visible = principal.visible_users()
    if visible is not None:
        query = query.where(User.id.in_(visible))
    return list(session.scalars(query))


@router.get("/repos/{project_id}/branches", response_model=list[BranchOut])
def list_branches(
    project_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    box: BoxDep,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> list[BranchOut]:
    project = _tracked_project(session, project_id)
    connection = _connection(session, project)
    try:
        with _client(box, connection) as client:
            branches = client.branches(project.external_id, search)
    except GitLabError as exc:
        raise _gitlab_error(exc, "repo") from None
    out = [
        BranchOut(
            name=str(b.get("name")),
            default=bool(b.get("default")),
            last_commit_at=(b.get("commit") or {}).get("committed_date"),
        )
        for b in branches
        if b.get("name")
    ]
    # Default branch first, then most recent activity.
    out.sort(key=lambda b: (0 if b.default else 1, -(_ts(b.last_commit_at))))
    return out


def _ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _authors(session: Session, emails: set[str]) -> dict[str, tuple[int | None, str | None]]:
    """author_email -> (user_id, full name) through git_email accounts."""
    if not emails:
        return {}
    rows = session.execute(
        select(Account.external_key, Account.user_id, User.full_name)
        .outerjoin(User, User.id == Account.user_id)
        .where(Account.kind == "git_email", Account.external_key.in_(emails))
    ).all()
    return {str(key): (uid, name) for key, uid, name in rows}


def _latest_reviews(session: Session, shas: list[str]) -> dict[str, Review]:
    if not shas:
        return {}
    latest: dict[str, Review] = {}
    for review in session.scalars(
        select(Review).where(Review.commit_sha.in_(shas)).order_by(Review.id.desc())
    ):
        if review.commit_sha and review.commit_sha not in latest:
            latest[review.commit_sha] = review
    return latest


def _row(
    raw: dict[str, Any],
    author: tuple[int | None, str | None],
    review: Review | None,
    principal: Principal,
) -> CommitRowOut:
    user_id, user_name = author
    is_merge = len(raw.get("parent_ids") or []) > 1
    stats = raw.get("stats") or {}
    title = raw.get("title") if isinstance(raw.get("title"), str) else ""
    if not title:
        message = raw.get("message")
        lines = message.splitlines() if isinstance(message, str) else []
        title = lines[0] if lines else ""
    reason: str | None = None
    if is_merge:
        reason = "merge commit"
    elif review is not None and review.status in TAKEN:
        reason = {
            "done": "đã review",
            "skipped": "đã bỏ qua (quá lớn hoặc không có file review được)",
        }.get(review.status, "đang trong hàng đợi")
    elif not principal.may_review_author(user_id):
        reason = "không thuộc quyền review của bạn"
    return CommitRowOut(
        sha=str(raw["id"]),
        short_sha=str(raw.get("short_id") or raw["id"][:8]),
        title=title[:200],
        authored_at=raw.get("authored_date") or raw.get("created_at"),
        author_name=raw.get("author_name"),
        user_id=user_id,
        user_name=user_name,
        is_merge=is_merge,
        additions=stats.get("additions"),
        deletions=stats.get("deletions"),
        review_id=review.id if review else None,
        review_status=review.status if review else None,
        score=float(review.score) if review and review.score is not None else None,
        verdict=review.verdict if review else None,
        reviewable=reason is None,
        reason=reason,
    )


@router.get("/repos/{project_id}/commits", response_model=CommitListOut)
def list_commits(
    project_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    ref: Annotated[str, Query(min_length=1, max_length=255)],
    since: datetime | None = None,
    until: datetime | None = None,
    user_id: Annotated[list[int] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> CommitListOut:
    """Recent commits of a branch, straight from GitLab, with who wrote them and whether a
    review exists. People outside your scope are left out (and counted in `hidden`)."""
    project = _tracked_project(session, project_id)
    connection = _connection(session, project)
    limit = min(limit, settings.review_commits_list_max)
    since, until = _aware(since), _aware(until)
    if since is None and until is None:
        since = datetime.now(UTC) - DEFAULT_WINDOW
    if since and until and until < since:
        raise HTTPException(422, "`until` is before `since`")
    try:
        with _client(box, connection) as client:
            raw = client.commits_on_ref(project.external_id, ref, since, until, limit + 1)
    except GitLabError as exc:
        raise _gitlab_error(exc, "branch") from None
    truncated = len(raw) > limit
    raw = raw[:limit]
    authors = _authors(session, {str(c.get("author_email") or "").lower() for c in raw})
    reviews = _latest_reviews(session, [str(c["id"]) for c in raw])
    wanted = set(user_id) if user_id else None
    rows: list[CommitRowOut] = []
    hidden = 0
    for c in raw:
        author = authors.get(str(c.get("author_email") or "").lower(), (None, None))
        uid = author[0]
        if wanted is not None and uid not in wanted:
            continue
        # Strangers' commits are not even listed for members; leads/admins see the unmapped.
        if not principal.may_review_author(uid):
            hidden += 1
            continue
        rows.append(_row(c, author, reviews.get(str(c["id"])), principal))
    return CommitListOut(
        ref=ref,
        commits=rows,
        hidden=hidden,
        truncated=truncated,
        batch_max=settings.review_batch_max,
    )


@router.post("/repos/{project_id}/review-commits", response_model=ReviewCommitsOut, status_code=202)
def review_commits(
    project_id: int,
    body: ReviewCommitsIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> ReviewCommitsOut:
    """Queue a review for each chosen commit (costs LLM budget per commit)."""
    project = _tracked_project(session, project_id)
    if not project.review_enabled:
        raise HTTPException(409, "review is switched off for this repo")
    if len(body.shas) > settings.review_batch_max:
        raise HTTPException(422, f"at most {settings.review_batch_max} commits per request")
    connection = _connection(session, project)
    converter = Converter(session, project, settings.ignore_paths)
    queued: list[str] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    with _client(box, connection) as client:
        for short in body.shas:
            if short in seen:
                continue
            seen.add(short)
            try:
                raw = client.commit(project.external_id, short)
            except GitLabError as exc:
                if exc.status_code == 404:
                    skipped.append({"sha": short, "reason": "không tìm thấy trên GitLab"})
                    continue
                # GitLab is in trouble: stop here rather than retrying 50 times on the request
                # path; what was queued so far stays queued.
                session.commit()
                raise _gitlab_error(exc, "commit") from None
            commit = gl_commit(raw)
            if commit.is_merge:
                skipped.append({"sha": commit.sha, "reason": "merge commit"})
                continue
            account_id = converter.email_account(commit.author_email, commit.author_name)
            author_user = session.get(Account, account_id).user_id if account_id else None  # type: ignore[union-attr]
            if not principal.may_review_author(author_user):
                skipped.append({"sha": commit.sha, "reason": "không thuộc quyền review của bạn"})
                continue
            existing = _latest_reviews(session, [commit.sha]).get(commit.sha)
            if existing is not None and existing.status in TAKEN:
                skipped.append({"sha": commit.sha, "reason": f"đã có review ({existing.status})"})
                continue
            converter.commit(commit, only_if_missing=True)
            # The queued row exists before the worker runs, so the list shows "trong hàng
            # đợi" at once and a second request for the same sha is refused. The worker's
            # _claim() takes this row over (same unique key) and keeps trigger/requester.
            session.execute(
                insert(Review)
                .values(
                    target_type="commit",
                    commit_sha=commit.sha,
                    reviewed_sha=commit.sha,
                    rubric_version=RUBRIC_VERSION,
                    author_user_id=author_user,
                    status="queued",
                    trigger="dashboard",
                    requested_by_user_id=principal.user_id,
                )
                .on_conflict_do_nothing(constraint="uq_reviews_target")
            )
            session.flush()
            queue.enqueue(
                "app.workers.review_jobs.review_commit_job",
                project.id,
                commit.sha,
                trigger="dashboard",
                requested_by=principal.user_id,
                job_id=f"review-commit-{commit.sha[:12]}",
                job_timeout=REVIEW_JOB_TIMEOUT,
                retry=REVIEW_RETRY,
                at_front=True,
            )
            queued.append(commit.sha)
    audit(
        session,
        principal.user_id,
        "project.review_commits",
        f"project:{project.id}",
        ref=body.ref,
        queued=len(queued),
        skipped=len(skipped),
    )
    session.commit()
    return ReviewCommitsOut(queued=queued, skipped=skipped)
