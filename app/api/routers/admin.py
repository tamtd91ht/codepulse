"""Admin endpoints (docs/specs/09; Admin screen in docs/specs/10). Every change is audited."""

import logging
import secrets
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Annotated, Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from rq import Retry
from sqlalchemy import ColumnElement, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_secret_box, get_session
from app.api.schemas import (
    AccountLink,
    AccountOut,
    BranchBulkIn,
    BranchBulkOut,
    BusinessProjectIn,
    BusinessProjectOut,
    ConnectionIn,
    ConnectionOut,
    ConnectionPatch,
    GroupImportIn,
    GroupImportOut,
    MemberIn,
    MemberOut,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    RepoAssignment,
    RepoRemoval,
    RepoRemovalOut,
    TeamIn,
    TeamOut,
    UserIn,
    UserOut,
    UserPatch,
)
from app.api.security import AdminPrincipal, LeadPrincipal
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.collector.removal import REMOVAL_JOB_TIMEOUT, confirmation_phrase, remove_projects
from app.collector.sync import (
    SYNC_JOB_TIMEOUT,
    WEBHOOK_JOB_TIMEOUT,
    Enqueuer,
    client_factory,
    register_webhook,
)
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.identity.autolink import unmapped_accounts
from app.models import (
    Account,
    AuditLog,
    BusinessProject,
    Connection,
    FindingDispute,
    MergeRequest,
    MetricsUserPeriod,
    Project,
    Review,
    Team,
    TeamMember,
    TelegramRequest,
    User,
)
from app.models.source import ALL_BRANCHES
from app.webhook.jobs import REVIEW_RETRY, review_job_timeout

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])
SessionDep = Annotated[Session, Depends(get_session)]


def _get[M](session: Session, model: type[M], ident: int, name: str) -> M:
    row = session.get(model, ident)
    if row is None:
        raise HTTPException(404, f"{name} not found")
    return row


def _require_reachable_base_url(settings: Settings) -> None:
    if urlsplit(settings.app_base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(409, "APP_BASE_URL points at localhost; GitLab could not reach it")


# -- connections ----------------------------------------------------------------------------------


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(principal: AdminPrincipal, session: SessionDep) -> list[ConnectionOut]:
    repos = (
        select(Project.connection_id, func.count().label("n"))
        .group_by(Project.connection_id)
        .subquery()
    )
    rows = session.execute(
        select(Connection, func.coalesce(repos.c.n, 0))
        .outerjoin(repos, repos.c.connection_id == Connection.id)
        .order_by(Connection.id)
    ).all()
    return [
        ConnectionOut(id=c.id, name=c.name, kind=c.kind, base_url=c.base_url, repos=int(n))
        for c, n in rows
    ]


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def create_connection(
    body: ConnectionIn,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
) -> Connection:
    connection = Connection(
        name=body.name,
        base_url=str(body.base_url).rstrip("/"),
        token_encrypted=box.encrypt(body.token),
        webhook_secret_encrypted=box.encrypt(body.webhook_secret or secrets.token_urlsafe(32)),
    )
    session.add(connection)
    session.flush()
    audit(session, principal.user_id, "connection.create", f"connection:{connection.id}")
    session.commit()
    return connection


@router.patch("/connections/{connection_id}", response_model=ConnectionOut)
def update_connection(
    connection_id: int,
    body: ConnectionPatch,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
) -> Connection:
    connection = _get(session, Connection, connection_id, "connection")
    changed = sorted(body.model_dump(exclude_none=True))  # field names only, never the token
    if not changed:
        raise HTTPException(422, "nothing to update: pass name and/or token")
    if body.name is not None:
        connection.name = body.name
    if body.token is not None:
        # Check the new token before replacing the old one: the operator revokes the old token next.
        try:
            with GitLabClient(connection.base_url, body.token, max_retries=1) as client:
                client.current_user()
        except GitLabError as exc:
            raise HTTPException(422, f"GitLab rejected the token ({exc})") from None
        connection.token_encrypted = box.encrypt(body.token)
    audit(
        session,
        principal.user_id,
        "connection.update",
        f"connection:{connection.id}",
        fields=changed,
    )
    session.commit()
    return connection


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(connection_id: int, principal: AdminPrincipal, session: SessionDep) -> None:
    """Remove a GitLab connection nobody uses; its repos (and their history) must go first."""
    connection = _get(session, Connection, connection_id, "connection")
    repos = session.scalar(
        select(func.count()).select_from(Project).where(Project.connection_id == connection.id)
    )
    if repos:
        raise HTTPException(
            409, f"connection still has {repos} repo(s); remove them first (Dự án & Repo → Gỡ hẳn)"
        )
    session.delete(connection)
    audit(
        session,
        principal.user_id,
        "connection.delete",
        f"connection:{connection_id}",
        name=connection.name,
        base_url=connection.base_url,
    )
    try:
        session.commit()
    except IntegrityError:  # a repo was registered on it meanwhile
        session.rollback()
        raise HTTPException(409, "a repo was just added to this connection; try again") from None


# -- business projects ("dự án") ---------------------------------------------------------------


@router.get("/business-projects", response_model=list[BusinessProjectOut])
def list_business_projects(
    principal: AdminPrincipal, session: SessionDep
) -> list[BusinessProjectOut]:
    repos = (
        select(Project.business_project_id, func.count().label("n"))
        .where(Project.tracked)
        .group_by(Project.business_project_id)
        .subquery()
    )
    rows = session.execute(
        select(BusinessProject, func.coalesce(repos.c.n, 0))
        .outerjoin(repos, repos.c.business_project_id == BusinessProject.id)
        .order_by(BusinessProject.name)
    ).all()
    return [
        BusinessProjectOut(
            id=bp.id,
            name=bp.name,
            description=bp.description,
            repos=int(n),
            claude_repo_connection_id=bp.claude_repo_connection_id,
            claude_repo_path=bp.claude_repo_path,
            claude_repo_ref=bp.claude_repo_ref,
            claude_repo_include=bp.claude_repo_include,
        )
        for bp, n in rows
    ]


@router.post("/business-projects", response_model=BusinessProjectOut, status_code=201)
def create_business_project(
    body: BusinessProjectIn, principal: AdminPrincipal, session: SessionDep
) -> BusinessProject:
    bp = BusinessProject(name=body.name, description=body.description)
    session.add(bp)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "a dự án with this name exists") from None
    audit(session, principal.user_id, "business_project.create", f"business_project:{bp.id}")
    session.commit()
    return bp


@router.patch("/business-projects/{bp_id}", response_model=BusinessProjectOut)
def update_business_project(
    bp_id: int, body: BusinessProjectIn, principal: AdminPrincipal, session: SessionDep
) -> BusinessProject:
    bp = _get(session, BusinessProject, bp_id, "dự án")
    old_name = bp.name
    bp.name, bp.description = body.name, body.description
    rules_changed = "review_rules" in body.model_fields_set
    if rules_changed:
        bp.review_rules = (body.review_rules or "").strip() or None
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "a dự án with this name exists") from None
    audit(
        session,
        principal.user_id,
        "business_project.update",
        f"business_project:{bp.id}",
        old_name=old_name,
        name=bp.name,
        **({"review_rules": len(bp.review_rules or "")} if rules_changed else {}),
    )
    session.commit()
    return bp


@router.delete("/business-projects/{bp_id}", status_code=204)
def delete_business_project(bp_id: int, principal: AdminPrincipal, session: SessionDep) -> None:
    """Only the grouping goes: its repos (and their history) stay, unassigned."""
    bp = _get(session, BusinessProject, bp_id, "dự án")
    session.execute(
        update(Project).where(Project.business_project_id == bp.id).values(business_project_id=None)
    )
    session.delete(bp)
    audit(session, principal.user_id, "business_project.delete", f"business_project:{bp_id}")
    session.commit()


@router.put("/projects/business-project", status_code=204)
def assign_repos(body: RepoAssignment, principal: AdminPrincipal, session: SessionDep) -> None:
    """Put repos into a dự án (or take them out with business_project_id=null)."""
    if body.business_project_id is not None:
        _get(session, BusinessProject, body.business_project_id, "dự án")
    session.execute(
        update(Project)
        .where(Project.id.in_(body.project_ids))
        .values(business_project_id=body.business_project_id)
    )
    audit(
        session,
        principal.user_id,
        "project.assign",
        f"projects:{len(body.project_ids)}",
        project_ids=body.project_ids,
        business_project_id=body.business_project_id,
    )
    session.commit()


# Above this many repos the GitLab check of a bulk branch change is skipped (one call per repo).
BRANCH_CHECK_MAX = 30
BRANCH_CHECK_BUDGET_S = 10.0


@router.put("/projects/review-branch", response_model=BranchBulkOut)
def set_review_branch(
    body: BranchBulkIn,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
) -> BranchBulkOut:
    """Set the review branch of many repos at once ("" = default, "*" = every branch). For a
    named branch and a small batch, GitLab is asked which repos do not have it; they are still
    updated (the branch may be created later) and listed so the admin can fix them."""
    projects = list(
        session.scalars(
            select(Project).where(Project.id.in_(body.project_ids)).order_by(Project.id)
        )
    )
    if len(projects) != len(set(body.project_ids)):
        raise HTTPException(404, "some repos were not found")
    value = body.review_branch or None
    for project in projects:
        project.review_branch = value
    checked = bool(value) and value != ALL_BRANCHES and len(projects) <= BRANCH_CHECK_MAX
    # What the GitLab check needs, read now: it runs after the commit, with no DB connection
    # or row lock held while GitLab answers.
    targets: list[tuple[str, str, int, str]] = []
    if checked:
        connections = {
            c.id: c
            for c in session.scalars(
                select(Connection).where(Connection.id.in_({p.connection_id for p in projects}))
            )
        }
        targets = [
            (c.base_url, c.token_encrypted, p.external_id, p.path_with_namespace)
            for p in projects
            if (c := connections.get(p.connection_id or 0)) is not None
        ]
    audit(
        session,
        principal.user_id,
        "project.review_branch",
        f"projects:{len(projects)}",
        project_ids=[p.id for p in projects],
        review_branch=value or "default",
    )
    session.commit()
    missing: list[str] = []
    if checked and value:
        try:
            missing = _without_branch(box, targets, value)
        except Exception:  # the change is saved; the check is only a hint
            log.warning("review branch check failed", exc_info=True)
            checked = False
    return BranchBulkOut(updated=len(projects), missing=missing, checked=checked)


def _without_branch(
    box: SecretBox, targets: list[tuple[str, str, int, str]], branch: str
) -> list[str]:
    """Paths of the repos where GitLab has no branch with exactly this name. A repo GitLab does
    not answer for is not reported; the whole check stops at BRANCH_CHECK_BUDGET_S."""
    missing: list[str] = []
    deadline = monotonic() + BRANCH_CHECK_BUDGET_S
    by_connection: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for base_url, token, external_id, path in targets:
        by_connection.setdefault((base_url, token), []).append((external_id, path))
    for (base_url, token), items in by_connection.items():
        for external_id, path in items:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return missing
            # Short and without retries: this runs inside an admin's request.
            with GitLabClient(
                base_url, box.decrypt(token), timeout=min(5.0, remaining), max_retries=0
            ) as client:
                try:
                    if not client.branch_exists(external_id, branch):
                        missing.append(path)
                except GitLabError:
                    continue
    return missing


# -- projects -------------------------------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(principal: AdminPrincipal, session: SessionDep) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.id)))


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(
    body: ProjectIn,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
) -> Project:
    connection = _get(session, Connection, body.connection_id, "connection")
    with client_factory(box)(connection) as client:
        info = client.project(body.external_id)
    project = Project(
        connection_id=connection.id,
        external_id=body.external_id,
        path_with_namespace=info["path_with_namespace"],
        default_branch=info.get("default_branch"),
    )
    session.add(project)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "project already registered") from None
    audit(session, principal.user_id, "project.create", f"project:{project.id}")
    session.commit()
    return project


@router.post("/projects/import-group", response_model=GroupImportOut, status_code=201)
def import_group(
    body: GroupImportIn,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    settings: Annotated[Settings, Depends(get_settings)],
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> GroupImportOut:
    """One-off: register every active project of a GitLab group (subgroups included).

    Projects are inserted here; each project's webhook and first sync run as worker jobs, so a
    large group cannot outlast the request. Projects created in the group later are not picked
    up; run the import again (already registered projects are skipped).
    """
    if body.register_webhooks:
        _require_reachable_base_url(settings)
    connection = _get(session, Connection, body.connection_id, "connection")
    if body.business_project_id is not None:
        _get(session, BusinessProject, body.business_project_id, "dự án")
    session.commit()  # no transaction stays open while GitLab is paginated
    try:
        with client_factory(box)(connection) as client:
            listed = {int(p["id"]): p for p in client.group_projects(body.group)}
    except GitLabError as exc:
        if exc.status_code in (403, 404):
            raise HTTPException(404, "group not found, or the bot token cannot see it") from None
        raise
    rows = [
        {
            "connection_id": connection.id,
            "external_id": external_id,
            "path_with_namespace": info["path_with_namespace"],
            "default_branch": info.get("default_branch"),
            "business_project_id": body.business_project_id,
        }
        for external_id, info in listed.items()
    ]
    # ON CONFLICT: a concurrent import or POST /projects may have registered some already.
    added = (
        session.execute(
            pg_insert(Project)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["connection_id", "external_id"])
            .returning(Project.id, Project.path_with_namespace)
        ).all()
        if rows
        else []
    )
    audit(
        session,
        principal.user_id,
        "project.import_group",
        f"connection:{connection.id}",
        group=body.group,
        added=[project_id for project_id, _ in added],
    )
    session.commit()

    not_queued: list[str] = []
    for project_id, path in added:
        try:
            if body.register_webhooks:
                queue.enqueue(
                    "app.collector.sync.register_webhook_job",
                    project_id,
                    settings.app_base_url,
                    job_id=f"webhook-project-{project_id}",
                    job_timeout=WEBHOOK_JOB_TIMEOUT,
                )
            queue.enqueue(
                "app.collector.sync.sync_project_job",
                project_id,
                job_id=f"sync-project-{project_id}",
                job_timeout=SYNC_JOB_TIMEOUT,
            )
        except Exception:
            # The project stays registered; the periodic sync picks it up, the Webhook button
            # registers its hook.
            log.exception(
                "could not queue jobs for imported project", extra={"project_id": project_id}
            )
            not_queued.append(path)
    return GroupImportOut(
        added=[path for _, path in added],
        skipped=len(listed) - len(added),
        not_queued=not_queued,
    )


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int, body: ProjectPatch, principal: AdminPrincipal, session: SessionDep
) -> Project:
    project = _get(session, Project, project_id, "project")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "nothing to update")
    for key, value in changes.items():
        if key in ("review_rules", "review_rules_paths", "review_branch"):
            value = (value or "").strip() or None  # "" clears (review_branch: default branch)
        elif value is None:
            raise HTTPException(422, f"{key} cannot be null")
        setattr(project, key, value)
    audited = {
        k: (len(v) if isinstance(v, str) and k != "review_branch" else v)
        for k, v in changes.items()
    }  # sizes, not text (a branch name is not content)
    audit(session, principal.user_id, "project.update", f"project:{project.id}", **audited)
    session.commit()
    return project


@router.post("/projects/{project_id}/untrack", response_model=ProjectOut)
def untrack_project(project_id: int, principal: AdminPrincipal, session: SessionDep) -> Project:
    """Stop syncing and reviewing a repo; its reviews, scores and history are kept."""
    project = _get(session, Project, project_id, "project")
    if project.tracked:
        project.tracked, project.untracked_at = False, datetime.now(UTC)
        audit(session, principal.user_id, "project.untrack", f"project:{project.id}")
        session.commit()
    return project


@router.post("/projects/{project_id}/track", response_model=ProjectOut)
def track_project(
    project_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> Project:
    """Resume a repo; a sync is queued to catch up on what happened meanwhile."""
    project = _get(session, Project, project_id, "project")
    if not project.tracked:
        project.tracked, project.untracked_at = True, None
        audit(session, principal.user_id, "project.track", f"project:{project.id}")
        session.commit()
        try:
            queue.enqueue(
                "app.collector.sync.sync_project_job",
                project.id,
                job_id=f"sync-project-{project.id}",
                job_timeout=SYNC_JOB_TIMEOUT,
            )
        except Exception:
            # The periodic sync catches up.
            log.exception("could not queue the catch-up sync", extra={"project_id": project.id})
    return project


@router.post("/projects/remove", response_model=RepoRemovalOut)
def remove_repos(
    body: RepoRemoval,
    principal: AdminPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> RepoRemovalOut:
    """Delete repos with their commits, MRs, reviews, findings and disputes (cannot be undone).

    The explicit exception to "review history is never deleted" (see app/collector/removal.py);
    the typed confirmation guards against a slip. Webhooks and metrics are fixed up by jobs.
    """
    paths = list(
        session.scalars(
            select(Project.path_with_namespace)
            .where(Project.id.in_(body.project_ids))
            .order_by(Project.id)
        )
    )
    if len(paths) != len(set(body.project_ids)):
        raise HTTPException(404, "some repos were not found")
    expected = confirmation_phrase(paths)
    if body.confirm.strip().casefold() != expected.casefold():
        raise HTTPException(422, f'type "{expected}" to confirm')
    try:
        result = remove_projects(session, body.project_ids, ZoneInfo(settings.report_tz))
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "a repo is being synced right now; try again shortly") from None
    if not result.paths:  # a concurrent removal got there first
        session.rollback()
        raise HTTPException(404, "repos already removed")
    audit(
        session,
        principal.user_id,
        "project.remove",
        "project:" + ",".join(str(i) for i in result.project_ids),
        paths=result.paths,
        project_ids=result.project_ids,
        gitlab=[list(hook) for hook in result.hooks],
        rows=result.rows,
    )
    session.commit()

    # Each clean-up job is queued on its own: one failure must not skip the others.
    not_queued: list[str] = []
    jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = [
        (
            "app.collector.removal.remove_webhook_job",
            (connection_id, external_id, settings.app_base_url),
            {"job_timeout": REMOVAL_JOB_TIMEOUT, "retry": Retry(max=3, interval=[30, 120, 600])},
        )
        for connection_id, external_id in result.hooks
    ]
    if result.first_day and result.last_day:
        jobs.append(
            (
                "app.scoring.aggregate.recompute_job",
                (result.first_day.isoformat(), result.last_day.isoformat()),
                {"job_timeout": REMOVAL_JOB_TIMEOUT * 6},
            )
        )
    for func_name, args, kwargs in jobs:
        try:
            queue.enqueue(func_name, *args, **kwargs)
        except Exception:
            log.exception("could not queue clean-up after removing repos", extra={"job": func_name})
            not_queued.append(func_name.rsplit(".", 1)[1])
    return RepoRemovalOut(
        removed=result.paths,
        reviews_deleted=result.reviews,
        cleanup_queued=not not_queued,
        not_queued=not_queued,
    )


def _tracked(session: Session, project_id: int) -> Project:
    project = _get(session, Project, project_id, "project")
    if not project.tracked:
        raise HTTPException(409, "repo is not tracked; resume tracking first")
    return project


@router.post("/projects/{project_id}/webhook")
def register_project_webhook(
    project_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Add the CodePulse hook (APP_BASE_URL + webhook path) to the GitLab project."""
    project = _tracked(session, project_id)
    if project.connection_id is None:
        raise HTTPException(409, "project has no GitLab connection")
    connection = _get(session, Connection, project.connection_id, "connection")
    _require_reachable_base_url(settings)
    url, created = register_webhook(
        client_factory(box), box, connection, project, settings.app_base_url
    )
    audit(session, principal.user_id, "project.webhook", f"project:{project.id}", created=created)
    session.commit()
    return {"url": url, "created": created}


@router.post("/projects/{project_id}/sync", status_code=202)
def sync_project_now(
    project_id: int,
    principal: LeadPrincipal,
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, str]:
    _tracked(session, project_id)
    queue.enqueue(
        "app.collector.sync.sync_project_job",
        project_id,
        job_id=f"sync-project-{project_id}",
        job_timeout=SYNC_JOB_TIMEOUT,
    )
    audit(session, principal.user_id, "project.sync", f"project:{project_id}")
    session.commit()
    return {"status": "queued"}


@router.post("/projects/{project_id}/backfill-reviews", status_code=202)
def backfill_reviews(
    project_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    settings: Annotated[Settings, Depends(get_settings)],
    include_open: bool = False,
) -> dict[str, int]:
    """Review MRs with no review yet (costs LLM budget): merged in the range, and with
    include_open also MRs still open that were created in the range."""
    project = _tracked(session, project_id)
    if date_to < date_from:
        raise HTTPException(422, "`to` must not be before `from`")
    # Days are the team's days (REPORT_TZ), like reports; the end is the next day, exclusive.
    tz = ZoneInfo(settings.report_tz)
    start = datetime.combine(date_from, time.min, tz)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tz)
    in_range: ColumnElement[bool] = and_(
        MergeRequest.merged_at >= start, MergeRequest.merged_at < end
    )
    if include_open:
        in_range = or_(
            in_range,
            and_(
                MergeRequest.state == "opened",
                MergeRequest.created_at_src >= start,
                MergeRequest.created_at_src < end,
            ),
        )
    reviewed = select(Review.mr_id).where(Review.mr_id.is_not(None))
    mrs = session.scalars(
        select(MergeRequest).where(
            MergeRequest.project_id == project.id,
            in_range,
            MergeRequest.head_sha.is_not(None),
            MergeRequest.id.not_in(reviewed),
        )
    ).all()
    for mr in mrs:
        if mr.head_sha is None:
            continue
        queue.enqueue(
            "app.workers.review_jobs.review_mr_job",
            project.id,
            mr.iid,
            mr.head_sha,
            trigger="backfill",
            requested_by=principal.user_id,
            job_id=f"review-mr-{project.id}-{mr.iid}-{mr.head_sha[:12]}",
            job_timeout=review_job_timeout(settings),
            retry=REVIEW_RETRY,
        )
    audit(
        session,
        principal.user_id,
        "project.backfill_reviews",
        f"project:{project.id}",
        date_from=str(date_from),
        date_to=str(date_to),
        include_open=include_open,
        queued=len(mrs),
    )
    session.commit()
    return {"queued": len(mrs)}


# -- identity -------------------------------------------------------------------------------------


@router.get("/users", response_model=list[UserOut])
def list_users(principal: AdminPrincipal, session: SessionDep) -> list[User]:
    return list(session.scalars(select(User).order_by(User.id)))


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, principal: AdminPrincipal, session: SessionDep) -> User:
    user = User(
        full_name=body.full_name, primary_email=body.primary_email.lower(), active=body.active
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "a user with this email exists") from None
    audit(session, principal.user_id, "user.create", f"user:{user.id}")
    session.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, body: UserPatch, principal: AdminPrincipal, session: SessionDep
) -> User:
    user = _get(session, User, user_id, "user")
    changes = body.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(user, key, value)
    audit(session, principal.user_id, "user.update", f"user:{user.id}", **changes)
    session.commit()
    return user


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> None:
    """Remove a person created by mistake (outside the spec, requested by the user).

    Review history stays (docs/specs/AGENTS.md): their reviews, disputes and Telegram requests
    lose the author instead, their Git/GitLab/Telegram accounts go back to "not linked" so the
    right person can claim them, and the team numbers of the periods they had reviews in are
    re-aggregated without them."""
    if user_id == principal.user_id:
        raise HTTPException(409, "you cannot remove yourself")
    user = _get(session, User, user_id, "user")
    first, last = session.execute(
        select(func.min(Review.finished_at), func.max(Review.finished_at)).where(
            Review.author_user_id == user_id
        )
    ).one()
    rows = {
        "reviews": session.execute(
            update(Review).where(Review.author_user_id == user_id).values(author_user_id=None)
        ).rowcount,  # type: ignore[attr-defined]
        "accounts": session.execute(
            update(Account).where(Account.user_id == user_id).values(user_id=None)
        ).rowcount,  # type: ignore[attr-defined]
        "team_members": session.execute(
            delete(TeamMember).where(TeamMember.user_id == user_id)
        ).rowcount,  # type: ignore[attr-defined]
        "metrics": session.execute(
            delete(MetricsUserPeriod).where(MetricsUserPeriod.user_id == user_id)
        ).rowcount,  # type: ignore[attr-defined]
    }
    session.execute(
        update(FindingDispute).where(FindingDispute.raised_by == user_id).values(raised_by=None)
    )
    session.execute(
        update(FindingDispute).where(FindingDispute.decided_by == user_id).values(decided_by=None)
    )
    session.execute(
        update(TelegramRequest).where(TelegramRequest.user_id == user_id).values(user_id=None)
    )
    session.execute(
        update(AuditLog).where(AuditLog.actor_user_id == user_id).values(actor_user_id=None)
    )
    audit(
        session,
        principal.user_id,
        "user.delete",
        f"user:{user_id}",
        email=user.primary_email,
        name=user.full_name,
        rows=rows,
    )
    session.delete(user)
    session.commit()
    if first is not None and last is not None:
        tz = ZoneInfo(settings.report_tz)
        try:
            queue.enqueue(
                "app.scoring.aggregate.recompute_job",
                first.astimezone(tz).date().isoformat(),
                last.astimezone(tz).date().isoformat(),
                job_timeout=REMOVAL_JOB_TIMEOUT * 6,
            )
        except Exception:
            log.exception("could not queue the re-aggregation after removing a user")


@router.get("/teams", response_model=list[TeamOut])
def list_teams(principal: AdminPrincipal, session: SessionDep) -> list[Team]:
    return list(session.scalars(select(Team).order_by(Team.name)))


@router.post("/teams", response_model=TeamOut, status_code=201)
def create_team(body: TeamIn, principal: AdminPrincipal, session: SessionDep) -> Team:
    if body.parent_id is not None:
        _get(session, Team, body.parent_id, "parent team")
    team = Team(name=body.name, parent_id=body.parent_id)
    session.add(team)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "team name already used") from None
    audit(session, principal.user_id, "team.create", f"team:{team.id}")
    session.commit()
    return team


@router.get("/teams/{team_id}/members", response_model=list[MemberOut])
def list_members(team_id: int, principal: AdminPrincipal, session: SessionDep) -> list[MemberOut]:
    """Every membership of the team, past ones included (left_at set), current ones first."""
    _get(session, Team, team_id, "team")
    rows = session.execute(
        select(TeamMember, User)
        .join(User, User.id == TeamMember.user_id)
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.left_at.is_not(None), User.full_name, TeamMember.joined_at)
    ).all()
    return [
        MemberOut(
            user_id=user.id,
            full_name=user.full_name,
            primary_email=user.primary_email,
            role=member.role,
            joined_at=member.joined_at,
            left_at=member.left_at,
        )
        for member, user in rows
    ]


@router.put("/teams/{team_id}/members", status_code=204)
def set_member(
    team_id: int, body: MemberIn, principal: AdminPrincipal, session: SessionDep
) -> None:
    _get(session, Team, team_id, "team")
    _get(session, User, body.user_id, "user")
    member = session.get(TeamMember, (team_id, body.user_id, body.joined_at))
    if member is None:
        member = TeamMember(team_id=team_id, user_id=body.user_id, joined_at=body.joined_at)
        session.add(member)
    member.role, member.left_at = body.role, body.left_at
    audit(
        session,
        principal.user_id,
        "team.member.set",
        f"team:{team_id}",
        user_id=body.user_id,
        role=body.role,
        left_at=str(body.left_at) if body.left_at else None,
    )
    session.commit()


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(principal: AdminPrincipal, session: SessionDep) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.kind, Account.external_key)))


@router.put("/accounts/{account_id}/user", response_model=AccountOut)
def link_account(
    account_id: int, body: AccountLink, principal: AdminPrincipal, session: SessionDep
) -> Account:
    account = _get(session, Account, account_id, "account")
    if body.user_id is not None:
        _get(session, User, body.user_id, "user")
    account.user_id = body.user_id
    action = "account.link" if body.user_id else "account.unlink"
    audit(session, principal.user_id, action, f"account:{account.id}", user_id=body.user_id)
    session.commit()
    return account


@router.get("/identity/unmapped", response_model=list[AccountOut])
def list_unmapped(principal: AdminPrincipal, session: SessionDep) -> list[Account]:
    return unmapped_accounts(session)


@router.get("/admin/llm-costs")
def llm_costs(
    principal: AdminPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    months: Annotated[int, Query(ge=1, le=24)] = 6,
) -> dict[str, Any]:
    """LLM spend per month vs MONTHLY_LLM_BUDGET_USD (Admin screen, docs/specs/10)."""
    month = func.date_trunc("month", Review.started_at)
    rows = session.execute(
        select(month, func.coalesce(func.sum(Review.cost_usd), 0), func.count())
        .where(Review.cost_usd.is_not(None))
        .group_by(month)
        .order_by(month.desc())
        .limit(months)
    ).all()
    return {
        "budget_usd": settings.monthly_llm_budget_usd,
        "months": [
            {"month": m.date().isoformat()[:7], "cost_usd": float(cost), "reviews": count}
            for m, cost, count in rows
        ],
    }
