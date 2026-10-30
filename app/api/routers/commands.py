"""Admin > Chạy command: list and run a repo's `.claude/commands` (outside the spec, requested by
the user 2026-10-30; see PROGRESS.md and app/commands). Admins only (ADMIN_EMAILS): a command
can run tools on the command worker with the repo's code in front of the model.

The files a run hands back are downloaded once: the download deletes them (and the clone is
already gone). Nobody downloads: the hourly clean-up deletes them after COMMAND_FILE_TTL_HOURS.
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from rq import Queue
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.admin_settings import effective
from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.security import AdminPrincipal
from app.collector.gitlab.client import GitLabClient
from app.commands import catalog
from app.commands.jobs import job_id, job_timeout_s
from app.commands.store import QUEUE, RunStore, run_dir
from app.commands.workspace import ZIP_NAME, remove
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.db import get_redis
from app.models import BusinessProject, CommandRun, Connection, Project, User

log = logging.getLogger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_enabled(settings: SettingsDep) -> None:
    """COMMAND_RUNS_ENABLED=false: the feature is not there at all (404, not 403)."""
    if not settings.command_runs_enabled:
        raise HTTPException(404, "Not Found")


router = APIRouter(tags=["admin"], dependencies=[Depends(require_enabled)])
SessionDep = Annotated[Session, Depends(get_session)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]

REQUEST_TIMEOUT_S = 30.0
MAX_ARGUMENTS = 4000
REF = re.compile(r"^[^\s\-~^:?*\[\\][^\s~^:?*\[\\]{0,254}$")  # a git branch name, roughly
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def get_command_queue() -> Queue:
    return Queue(QUEUE, connection=get_redis())


def get_run_store(session: SessionDep, settings: SettingsDep) -> RunStore:
    return RunStore(get_redis(), int(effective(session, settings)["command_file_ttl_hours"]) * 3600)


QueueDep = Annotated[Queue, Depends(get_command_queue)]
StoreDep = Annotated[RunStore, Depends(get_run_store)]


class CommandOut(BaseModel):
    name: str
    source: str
    path: str
    description: str
    argument_hint: str
    allowed_tools: list[str]
    model: str
    body: str
    overridden: bool


class CommandListOut(BaseModel):
    ref: str | None
    shared_repo: str | None
    shared_ref: str | None
    commands: list[CommandOut]
    repo_error: str | None = None
    shared_error: str | None = None


class CommandRunIn(BaseModel):
    project_id: int | None = None
    business_project_id: int | None = None  # only for a shared command run without a repo
    ref: str | None = Field(default=None, max_length=255)
    source: Literal["repo", "shared"]
    command: str = Field(min_length=1, max_length=200)
    arguments: str = Field(default="", max_length=MAX_ARGUMENTS)


class CommandRunOut(BaseModel):
    id: int
    status: str
    source: str
    command: str
    arguments: str
    project_id: int | None
    repo: str | None
    business_project_id: int | None
    business_project: str | None
    ref: str | None
    repo_sha: str | None
    shared_ref: str | None
    shared_sha: str | None
    error: str | None
    cost_usd: float | None
    num_turns: int | None
    files: list[dict[str, Any]]
    download: str | None  # the file name the download sends; None when nothing is left
    files_deleted: bool
    meta: dict[str, Any]
    started_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class CommandRunDetailOut(CommandRunOut):
    log: list[dict[str, Any]]
    log_next: int  # pass as ?after= to get only newer entries
    result: str | None


def _client(session: Session, box: SecretBox, connection_id: int | None) -> GitLabClient:
    connection = session.get(Connection, connection_id) if connection_id else None
    if connection is None:
        raise HTTPException(409, "repo has no GitLab connection")
    return GitLabClient(
        connection.base_url,
        box.decrypt(connection.token_encrypted),
        timeout=REQUEST_TIMEOUT_S,
        max_retries=1,
    )


def _where(
    session: Session, project_id: int | None, business_project_id: int | None
) -> tuple[Project | None, BusinessProject | None]:
    project = session.get(Project, project_id) if project_id else None
    if project_id and project is None:
        raise HTTPException(404, "repo not found")
    bp_id = project.business_project_id if project else business_project_id
    business = session.get(BusinessProject, bp_id) if bp_id else None
    if project is None and business is None:
        raise HTTPException(422, "choose a repo, or a dự án with a shared .claude repo")
    return project, business


def _has_shared(business: BusinessProject | None) -> bool:
    return bool(business and business.claude_repo_external_id and business.claude_repo_path)


def _check_ref(ref: str) -> str:
    if not REF.match(ref) or ".." in ref:
        raise HTTPException(422, "invalid branch name")
    return ref


@router.get("/admin/commands")
def list_commands(
    principal: AdminPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    project_id: int | None = None,
    business_project_id: int | None = None,
    ref: Annotated[str | None, Query(max_length=255)] = None,
) -> CommandListOut:
    """The commands of the repo at `ref` (default: its default branch) and of the dự án's
    shared .claude repo; with only a dự án, the shared ones."""
    project, business = _where(session, project_id, business_project_id)
    own: list[catalog.Command] = []
    shared: list[catalog.Command] = []
    repo_error = shared_error = None
    branch = None
    if project is not None:
        branch = _check_ref(ref or project.default_branch or "main")
        with _client(session, box, project.connection_id) as client:
            own, repo_error = catalog.safe_listing(
                client,
                project.external_id,
                branch,
                catalog.repo_commands,
                settings.sensitive_paths,
            )
    shared_ref = None
    if business is not None and _has_shared(business):
        assert business.claude_repo_external_id is not None  # noqa: S101 - _has_shared
        with _client(session, box, business.claude_repo_connection_id) as client:
            try:
                shared_ref = business.claude_repo_ref or str(
                    client.project(business.claude_repo_external_id)["default_branch"]
                )
            except Exception as exc:  # GitLab down / repo gone: the repo's commands still list
                shared_error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
            if shared_ref:
                shared, shared_error = catalog.safe_listing(
                    client,
                    business.claude_repo_external_id,
                    shared_ref,
                    catalog.shared_commands,
                    settings.sensitive_paths,
                )
    commands = catalog.merge(own, shared)
    return CommandListOut(
        ref=branch,
        shared_repo=business.claude_repo_path if business and _has_shared(business) else None,
        shared_ref=shared_ref,
        commands=[CommandOut(**c.to_dict()) for c in commands],
        repo_error=repo_error,
        shared_error=shared_error,
    )


def _out(session: Session, run: CommandRun) -> dict[str, Any]:
    project = session.get(Project, run.project_id) if run.project_id else None
    business = (
        session.get(BusinessProject, run.business_project_id) if run.business_project_id else None
    )
    user = session.get(User, run.started_by_user_id) if run.started_by_user_id else None
    meta = dict(run.meta or {})
    available = run.files_deleted_at is None and bool(meta.get("download"))
    return {
        "id": run.id,
        "status": run.status,
        "source": run.source,
        "command": run.command,
        "arguments": run.arguments or "",
        "project_id": run.project_id,
        "repo": project.path_with_namespace if project else None,
        "business_project_id": run.business_project_id,
        "business_project": business.name if business else None,
        "ref": run.ref,
        "repo_sha": run.repo_sha,
        "shared_ref": run.shared_ref,
        "shared_sha": run.shared_sha,
        "error": run.error,
        "cost_usd": float(run.cost_usd) if run.cost_usd is not None else None,
        "num_turns": run.num_turns,
        "files": list(run.files or []),
        "download": _download_name(run, str(meta["download"])) if available else None,
        "files_deleted": run.files_deleted_at is not None,
        "meta": {k: v for k, v in meta.items() if k != "download"},
        "started_by": (user.full_name or user.primary_email) if user else None,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _download_name(run: CommandRun, stored: str) -> str:
    """The stored file (a plain name the worker chose), or a friendlier name for the zip."""
    if stored != ZIP_NAME:
        return stored
    return f"{SAFE_NAME.sub('-', run.command).strip('-') or 'command'}-{run.id}.zip"


@router.post("/admin/command-runs", status_code=202)
def start_run(
    body: CommandRunIn,
    principal: AdminPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
) -> CommandRunOut:
    """Queue a run on the command worker. One queued/running run per repo (409)."""
    if not catalog.COMMAND_NAME.match(body.command):
        raise HTTPException(422, "invalid command name")
    if settings.llm_provider != "claude_code":
        raise HTTPException(409, "running commands needs LLM_PROVIDER=claude_code")
    project, business = _where(session, body.project_id, body.business_project_id)
    if body.source == "shared" and not _has_shared(business):
        raise HTTPException(422, "this dự án has no shared .claude repo")
    if project is None and body.source != "shared":
        raise HTTPException(422, "a repo command needs a repo")
    if project is not None and project.review_enabled is False:
        # AGENTS.md: no source code to an outside LLM for a repo kept out of review.
        raise HTTPException(409, "review is off for this repo: its code is not sent to the LLM")
    ref = _check_ref(body.ref or project.default_branch or "main") if project else None
    active = select(CommandRun.id).where(CommandRun.status.in_(("queued", "running")))
    if project is not None:
        active = active.where(CommandRun.project_id == project.id)
    else:
        assert business is not None  # noqa: S101 - _where
        active = active.where(
            CommandRun.project_id.is_(None), CommandRun.business_project_id == business.id
        )
    if session.scalar(active.limit(1)) is not None:
        raise HTTPException(409, "a command is already running here; wait for it or cancel it")
    run = CommandRun(
        project_id=project.id if project else None,
        business_project_id=business.id if business else None,
        source=body.source,
        command=body.command,
        arguments=body.arguments.strip(),
        ref=ref,
        status="queued",
        started_by_user_id=principal.user_id,
    )
    session.add(run)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if "uq_command_runs_active" in str(exc.orig):
            raise HTTPException(409, "a command is already running here") from None
        raise HTTPException(409, "the repo or dự án changed meanwhile; reload and retry") from None
    audit(
        session,
        principal.user_id,
        "command.run",
        f"project:{project.id}" if project else f"business_project:{run.business_project_id}",
        command=body.command,
        source=body.source,
        ref=ref,
        arguments=body.arguments.strip()[:500] or None,
        run_id=run.id,
    )
    session.commit()
    # Fixed now and passed along: RQ's job timeout and the CLI's must come from the same value.
    timeout_s = int(effective(session, settings)["command_timeout_min"]) * 60
    try:
        queue.enqueue(
            "app.commands.jobs.run_command_job",
            run.id,
            timeout_s,
            job_id=job_id(run.id),
            job_timeout=job_timeout_s(timeout_s),
        )
    except Exception:
        log.exception("command run not queued")
        run.status, run.error, run.finished_at = "failed", "không xếp hàng được", datetime.now(UTC)
        session.commit()
        raise HTTPException(503, "the job queue is not reachable") from None
    return CommandRunOut(**_out(session, run))


@router.get("/admin/command-runs")
def list_runs(
    principal: AdminPrincipal,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
) -> list[CommandRunOut]:
    runs = session.scalars(select(CommandRun).order_by(CommandRun.id.desc()).limit(limit))
    return [CommandRunOut(**_out(session, run)) for run in runs]


def _run(session: Session, run_id: int) -> CommandRun:
    run = session.get(CommandRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@router.get("/admin/command-runs/{run_id}")
def get_run(
    run_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    store: StoreDep,
    after: Annotated[int, Query(ge=0)] = 0,
) -> CommandRunDetailOut:
    """The run, its live log from entry `after` on, and its final answer."""
    run = _run(session, run_id)
    entries = store.entries(run_id, after)
    return CommandRunDetailOut(
        **_out(session, run),
        log=entries,
        log_next=after + len(entries),
        result=store.result(run_id),
    )


@router.post("/admin/command-runs/{run_id}/cancel")
def cancel_run(
    run_id: int, principal: AdminPrincipal, session: SessionDep, store: StoreDep
) -> CommandRunOut:
    run = _run(session, run_id)
    if run.status not in ("queued", "running"):
        raise HTTPException(409, "the run has already ended")
    store.cancel(run_id)  # a running one stops at its next event
    now = datetime.now(UTC)
    # Only while still queued: the worker may have just claimed it (then the flag stops it).
    session.execute(
        update(CommandRun)
        .where(CommandRun.id == run_id, CommandRun.status == "queued")
        .values(status="cancelled", finished_at=now, files_deleted_at=now)
    )
    audit(session, principal.user_id, "command.cancel", f"command_run:{run_id}")
    session.commit()
    session.refresh(run)
    return CommandRunOut(**_out(session, run))


@router.get("/admin/command-runs/{run_id}/download")
def download(
    run_id: int, principal: AdminPrincipal, session: SessionDep, settings: SettingsDep
) -> FileResponse:
    """The run's output (the only file, or a zip of them all), then it is deleted."""
    run = _run(session, run_id)
    stored = str((run.meta or {}).get("download") or "")
    root = run_dir(settings.command_runs_dir, run.id)
    path = (root / stored).resolve()
    if (
        run.files_deleted_at is not None
        or not stored
        or root.is_symlink()
        or not path.is_relative_to(Path(settings.command_runs_dir).resolve() / str(run.id))
        or not path.is_file()
    ):
        raise HTTPException(410, "the files are gone (already downloaded or expired)")
    # Marked gone before it is sent, atomically: of two clicks only one gets the file (the
    # other 410), and the folder is not deleted under a second download still streaming.
    claimed = session.execute(
        update(CommandRun)
        .where(CommandRun.id == run_id, CommandRun.files_deleted_at.is_(None))
        .values(files_deleted_at=datetime.now(UTC))
    )
    if not claimed.rowcount:  # type: ignore[attr-defined]
        session.rollback()
        raise HTTPException(410, "the files are gone (already downloaded or expired)")
    audit(session, principal.user_id, "command.download", f"command_run:{run_id}")
    session.commit()
    return FileResponse(
        path, filename=_download_name(run, stored), background=BackgroundTask(remove, root)
    )
