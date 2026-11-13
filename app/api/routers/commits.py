"""Review on request from the dashboard (docs/specs/17 §Yêu cầu review).

Pick a repo and a branch, optionally people and a time window, see the recent commits with
their review state, and queue reviews for the ones you choose. Only repos of a dự án you are
granted (docs/specs/09 "Phân quyền theo dự án"); there, as on Telegram: your own work, the work
of people you lead, a manager of the dự án or an admin anyone's; commits whose author is not
mapped to a person need a lead, a manager or an admin. Reviews go through the normal worker,
so scores, findings and verdicts land in the same tables as webhook reviews.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.access import (
    may_request_review,
    may_rerun,
    reviewable_people,
    seen_people,
    sees_work,
    visible_repo,
)
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
from app.api.security import CurrentPrincipal, Principal, require
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.collector.gitlab.convert import Converter
from app.collector.gitlab.extract import _commit as gl_commit
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.models import Account, BusinessProject, Connection, Project, Review, User
from app.models.review import CHANGE_TIME_UNKNOWN
from app.review.rubric import RUBRIC_VERSION
from app.webhook.jobs import REVIEW_RETRY, review_job_timeout
from app.workers.queues import ASKED_TRIGGERS, review_queue

log = logging.getLogger(__name__)
router = APIRouter(tags=["review-requests"])
SessionDep = Annotated[Session, Depends(get_session)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# Yêu cầu review (docs/specs/21): the branches, commits and the request itself.
RequestPrincipal = Annotated[Principal, require("review.request")]

SHA = re.compile(r"^[0-9a-f]{7,40}$")
# Request-path GitLab calls: short timeout, one retry; a slow GitLab must not pin API workers.
REQUEST_TIMEOUT_S = 10.0
# GET /people: a manager's list also holds whoever committed in their dự án this long ago.
PEOPLE_ACTIVE_DAYS = 90


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
    """Tracked repos of the dự án this person sees, with their dự án; what anyone may ask a
    review for depends on authors."""
    query = (
        select(Project, BusinessProject.name)
        .outerjoin(BusinessProject, BusinessProject.id == Project.business_project_id)
        .where(Project.tracked)
        .order_by(Project.path_with_namespace)
    )
    visible = principal.visible_projects()
    if visible is not None:
        query = query.where(Project.business_project_id.in_(visible))
    rows = session.execute(query).all()
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
    principal: Annotated[Principal, require("knowledge.view")],
    session: SessionDep,
    business_project_id: int | None = None,
) -> list[RepoProfileOut]:
    """Project profiles (docs/specs/18): what each repo is, its stack and security model.
    Built from the repository's own documents, so anyone who sees the dự án may read them,
    like the review knowledge (`knowledge.view`, docs/specs/21)."""
    query = select(Project).where(Project.tracked).order_by(Project.path_with_namespace)
    visible = principal.visible_projects()
    if visible is not None:
        query = query.where(Project.business_project_id.in_(visible))
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
    principal: Annotated[Principal, require("knowledge.shared")],
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, str]:
    """Rebuild the profile from the default branch (one LLM call, runs in the worker).
    `knowledge.shared` (docs/specs/21 §4): no built-in set holds it, so as before spec 21 only
    admins, unless a set gives it."""
    project = _tracked_project(session, project_id)
    if project.allow_external_llm is False:
        raise HTTPException(409, "this repo does not allow sending code to an external LLM")
    queue.enqueue(
        "app.workers.review_jobs.refresh_profile_job",
        project.id,
        job_id=f"profile-{project.id}",
        job_timeout=review_job_timeout(get_settings()),
    )
    audit(session, principal.user_id, "project.profile_refresh", f"project:{project.id}")
    session.commit()
    return {"status": "queued"}


@router.get("/people", response_model=list[UserOut])
def list_people(
    principal: CurrentPrincipal,
    session: SessionDep,
    purpose: Annotated[Literal["view", "review"], Query(alias="for")] = "view",
) -> list[User]:
    """`for=view` (default, filters of review lists): people whose results this person sees:
    yourself, the people you lead, everyone in a dự án you manage; admin everyone.
    `for=review` (Yêu cầu review): people whose work you may ask a review for: yourself; a
    manager of a dự án also the people granted it and its recent authors; admin everyone."""
    if purpose == "review":
        since = datetime.now(UTC) - timedelta(days=PEOPLE_ACTIVE_DAYS)
        return reviewable_people(session, principal, None, since)
    query = select(User).where(User.active.is_not(False)).order_by(User.full_name, User.id)
    # Everyone in a dự án you manage: granted on it or an author in its repos.
    if (visible := seen_people(session, principal)) is not None:
        query = query.where(User.id.in_(visible))
    return list(session.scalars(query))


@router.get("/repos/{project_id}/branches", response_model=list[BranchOut])
def list_branches(
    project_id: int,
    principal: RequestPrincipal,
    session: SessionDep,
    box: BoxDep,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> list[BranchOut]:
    project = visible_repo(session, principal, project_id)
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


def _new_round(session: Session, review_id: int, values: dict[str, Any]) -> Review | None:
    """Round N+1 of a finished review, queued, as the worker's forced claim makes it
    (docs/specs/17 §Lịch sử): round N keeps counting until N+1 is done and supersedes it.
    None when another request started a new round first."""
    previous = session.scalars(
        select(Review)
        .where(Review.id == review_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if previous.status != "done" or previous.superseded_by is not None:
        return None
    fresh = Review(**values, round=previous.round + 1)
    session.add(fresh)
    session.flush()
    previous.superseded_by = fresh.id
    return fresh


def _row(
    raw: dict[str, Any],
    author: tuple[int | None, str | None],
    review: Review | None,
    principal: Principal,
    project: Project,
) -> CommitRowOut:
    user_id, user_name = author
    # Its state shows (it blocks a second request); the result only to whoever may see it.
    seen = review if sees_work(principal, user_id, project.business_project_id) else None
    is_merge = len(raw.get("parent_ids") or []) > 1
    stats = raw.get("stats") or {}
    title = raw.get("title") if isinstance(raw.get("title"), str) else ""
    if not title:
        message = raw.get("message")
        lines = message.splitlines() if isinstance(message, str) else []
        title = lines[0] if lines else ""
    reason: str | None = None
    again = False
    if is_merge:
        reason = "merge commit"
    elif (
        review is not None
        and review.status == "done"
        and may_rerun(principal, user_id, project.business_project_id)
    ):
        again = True  # pickable, as a new round: the page warns before it is sent
    elif review is not None and review.status in TAKEN:
        reason = {
            "done": "đã review",
            "skipped": "đã bỏ qua (quá lớn hoặc không có file review được)",
        }.get(review.status, "đang trong hàng đợi")
    elif not may_request_review(principal, user_id, project.business_project_id):
        reason = "không thuộc quyền review của bạn"
    return CommitRowOut(
        sha=str(raw["id"]),
        short_sha=str(raw.get("short_id") or raw["id"][:8]),
        web_url=raw.get("web_url") if isinstance(raw.get("web_url"), str) else None,
        title=title[:200],
        authored_at=raw.get("authored_date") or raw.get("created_at"),
        author_name=raw.get("author_name"),
        # For the admin to link an unrecognised author to a person; nobody else needs it.
        author_email=(raw.get("author_email") or None) if principal.is_admin else None,
        user_id=user_id,
        user_name=user_name,
        is_merge=is_merge,
        additions=stats.get("additions"),
        deletions=stats.get("deletions"),
        review_id=seen.id if seen else None,
        review_status=review.status if review else None,
        review_error=seen.error if seen and seen.status in ("failed", "skipped") else None,
        score=float(seen.score) if seen and seen.score is not None else None,
        verdict=seen.verdict if seen else None,
        reviewable=reason is None,
        reason=reason,
        again=again,
    )


# "Bỏ commit đã review": a latest review in these states counts as reviewed; GitLab is read up
# to EXCLUDE_READ_FACTOR times the list size (at most EXCLUDE_READ_MAX) to fill the list.
REVIEWED_OR_RUNNING = ("done", "queued", "running")
EXCLUDE_READ_FACTOR = 5
EXCLUDE_READ_MAX = 5000


@router.get("/repos/{project_id}/commits", response_model=CommitListOut)
def list_commits(
    project_id: int,
    principal: RequestPrincipal,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    ref: Annotated[str, Query(min_length=1, max_length=255)],
    since: datetime | None = None,
    until: datetime | None = None,
    user_id: Annotated[list[int] | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=2000)] = None,
    exclude_reviewed: bool = False,
) -> CommitListOut:
    """Recent commits of a branch, straight from GitLab, with who wrote them and whether a
    review exists. People outside your scope are left out (and counted in `hidden`).
    `limit`: at most REVIEW_COMMITS_LIST_MAX (Admin > Cấu hình review), its default.
    `exclude_reviewed`: commits already reviewed or being reviewed are left out (counted in
    `reviewed_hidden`), and more are read from GitLab to fill the list."""
    project = visible_repo(session, principal, project_id)
    connection = _connection(session, project)
    from app.admin_settings import effective

    configured = effective(session, settings).get("review_commits_list_max")
    most = int(configured or settings.review_commits_list_max)
    limit = min(limit or most, most)
    since, until = _aware(since), _aware(until)
    if since is None and until is None:
        since = datetime.now(UTC) - DEFAULT_WINDOW
    if since and until and until < since:
        raise HTTPException(422, "`until` is before `since`")
    # Leaving reviewed commits out, read further back so the list still fills (bounded).
    fetch = min(limit * EXCLUDE_READ_FACTOR, EXCLUDE_READ_MAX) if exclude_reviewed else limit
    try:
        with _client(box, connection) as client:
            raw = client.commits_on_ref(project.external_id, ref, since, until, fetch + 1)
    except GitLabError as exc:
        raise _gitlab_error(exc, "branch") from None
    more_on_gitlab = len(raw) > fetch
    raw = raw[:fetch]
    reviews = _latest_reviews(session, [str(c["id"]) for c in raw])
    reviewed_hidden = 0
    if exclude_reviewed:
        kept = []
        for c in raw:
            latest = reviews.get(str(c["id"]))
            if latest is not None and latest.status in REVIEWED_OR_RUNNING:
                reviewed_hidden += 1
            else:
                kept.append(c)
        raw = kept
    truncated = more_on_gitlab or len(raw) > limit
    raw = raw[:limit]
    authors = _authors(session, {str(c.get("author_email") or "").lower() for c in raw})
    wanted = set(user_id) if user_id else None
    rows: list[CommitRowOut] = []
    hidden = 0
    for c in raw:
        author = authors.get(str(c.get("author_email") or "").lower(), (None, None))
        uid = author[0]
        if wanted is not None and uid not in wanted:
            continue
        # Other people's commits are not even listed for members; managers/admins see all.
        if not may_request_review(principal, uid, project.business_project_id):
            hidden += 1
            continue
        rows.append(_row(c, author, reviews.get(str(c["id"])), principal, project))
    return CommitListOut(
        ref=ref,
        commits=rows,
        hidden=hidden,
        truncated=truncated,
        batch_max=settings.review_batch_max,
        limit=limit,
        reviewed_hidden=reviewed_hidden,
    )


@dataclass
class QueueOutcome:
    """What queueing a set of commits did: the shas queued (with their review rows), those given
    a new round, and the ones left out with why."""

    queued: list[str] = field(default_factory=list)
    again: list[str] = field(default_factory=list)  # each one spends LLM budget again
    skipped: list[dict[str, str]] = field(default_factory=list)
    review_ids: dict[str, int] = field(default_factory=dict)


def queue_commit(
    session: Session,
    principal: Principal,
    project: Project,
    converter: Converter,
    raw: dict[str, Any],
    *,
    again: bool,
    queue: Enqueuer,
    settings: Settings,
    trigger: str,
    outcome: QueueOutcome,
) -> None:
    """One commit as GitLab returned it: checked (merge, whose work, an existing review) and
    queued, its review row inserted first so its state shows at once. Shared by "Yêu cầu
    review" and the MCP tool `request_review` (docs/specs/26 §22): same rules either way.
    Commits the session when it starts a new round (see below); the caller commits otherwise.
    HTTPException 503 when the job queue is down after a new round was committed."""
    commit = gl_commit(raw)
    if commit.is_merge:
        outcome.skipped.append({"sha": commit.sha, "reason": "merge commit"})
        return
    account_id = converter.email_account(commit.author_email, commit.author_name)
    author_user = session.get(Account, account_id).user_id if account_id else None  # type: ignore[union-attr]
    if not may_request_review(principal, author_user, project.business_project_id):
        outcome.skipped.append({"sha": commit.sha, "reason": "không thuộc quyền review của bạn"})
        return
    existing = _latest_reviews(session, [commit.sha]).get(commit.sha)
    redo = again and existing is not None and existing.status == "done"
    if existing is not None and existing.status in TAKEN and not redo:
        outcome.skipped.append({"sha": commit.sha, "reason": f"đã có review ({existing.status})"})
        if existing.id is not None:
            outcome.review_ids[commit.sha] = existing.id
        return
    if redo and not may_rerun(principal, author_user, project.business_project_id):
        outcome.skipped.append(
            {"sha": commit.sha, "reason": "chỉ quản lý dự án hoặc admin được review lại"}
        )
        return
    converter.commit(commit, only_if_missing=True)
    values: dict[str, Any] = {
        "target_type": "commit",
        "commit_sha": commit.sha,
        "reviewed_sha": commit.sha,
        "changed_at": commit.authored_at or CHANGE_TIME_UNKNOWN,
        "rubric_version": RUBRIC_VERSION,
        "author_user_id": author_user,
        "status": "queued",
        "trigger": trigger,
        "requested_by_user_id": principal.user_id,
    }
    job_id = f"review-commit-{commit.sha[:12]}"
    fresh: Review | None = None
    if redo and existing is not None and existing.rubric_version == RUBRIC_VERSION:
        fresh = _new_round(session, existing.id, values)
        if fresh is None:
            outcome.skipped.append({"sha": commit.sha, "reason": "đang được review lại"})
            return
        job_id += f"-again-{fresh.round}"
        outcome.again.append(commit.sha)
        # Committed before the job exists: the worker's claim cannot see an uncommitted
        # round N+1 (its own insert does not conflict with it), would find round N done
        # and leave N+1 queued for good. It also lets go of round N's lock at once.
        session.commit()
    else:
        # The queued row exists before the worker runs, so the list shows "trong hàng
        # đợi" at once and a second request for the same sha is refused. The worker's
        # _claim() takes this row over (same unique key) and keeps trigger/requester.
        # A done review under an older rubric is redone the same way: the current
        # rubric makes it a row of its own.
        session.execute(
            insert(Review).values(**values).on_conflict_do_nothing(constraint="uq_reviews_target")
        )
    session.flush()
    try:
        review_queue(
            queue, asked=trigger in ASKED_TRIGGERS, bp_id=project.business_project_id
        ).enqueue(
            "app.workers.review_jobs.review_commit_job",
            project.id,
            commit.sha,
            trigger=trigger,
            requested_by=principal.user_id,
            job_id=job_id,
            job_timeout=review_job_timeout(settings),
            retry=REVIEW_RETRY,
        )
    except Exception:
        if fresh is None:
            raise
        # The new round is committed already: left queued it would block the commit
        # for good; failed, it may be asked for again.
        log.exception("review round not queued", extra={"review_id": fresh.id})
        fresh.status, fresh.error = "failed", "job queue unavailable"
        session.commit()
        raise HTTPException(503, "job queue unavailable; try again") from None
    outcome.queued.append(commit.sha)
    row = fresh or _latest_reviews(session, [commit.sha]).get(commit.sha)
    if row is not None:
        outcome.review_ids[commit.sha] = row.id


@router.post("/repos/{project_id}/review-commits", response_model=ReviewCommitsOut, status_code=202)
def review_commits(
    project_id: int,
    body: ReviewCommitsIn,
    principal: RequestPrincipal,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> ReviewCommitsOut:
    """Queue a review for each chosen commit (costs LLM budget per commit)."""
    project = visible_repo(session, principal, project_id)
    if not project.review_enabled:
        raise HTTPException(409, "review is switched off for this repo")
    if len(body.shas) > settings.review_batch_max:
        raise HTTPException(422, f"at most {settings.review_batch_max} commits per request")
    connection = _connection(session, project)
    converter = Converter(session, project, settings.ignore_paths)
    outcome = QueueOutcome()
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
                    outcome.skipped.append({"sha": short, "reason": "không tìm thấy trên GitLab"})
                    continue
                # GitLab is in trouble: stop here rather than retrying 50 times on the request
                # path; what was queued so far stays queued.
                session.commit()
                raise _gitlab_error(exc, "commit") from None
            queue_commit(
                session,
                principal,
                project,
                converter,
                raw,
                again=body.again,
                queue=queue,
                settings=settings,
                trigger="dashboard",
                outcome=outcome,
            )
    audit(
        session,
        principal.user_id,
        "project.review_commits",
        f"project:{project.id}",
        ref=body.ref,
        queued=len(outcome.queued),
        skipped=len(outcome.skipped),
        **({"again": outcome.again} if outcome.again else {}),
    )
    session.commit()
    return ReviewCommitsOut(queued=outcome.queued, skipped=outcome.skipped)
