"""The command worker's jobs (queue `commands`): run one command, and clean up what nobody
downloaded. The worker and the api share COMMAND_RUNS_DIR (a volume), so the api can send the
files the worker produced.

A run's row is only ever moved forward with a conditional update (`queued -> running`,
`running -> ended`), so a cancel from the api, the stale sweep and the job itself cannot
overwrite one another's verdict.
"""

import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.admin_settings import effective
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.commands import catalog, container, runner, workspace
from app.commands.store import RunStore, run_dir
from app.config import Settings, get_settings
from app.crypto import secret_box
from app.db import get_redis, get_sessionmaker
from app.models import BusinessProject, CommandRun, Connection, Project
from app.review.credentials import load_stored_claude_token
from app.review.progress import progress_redis
from app.review.providers.claude_code import cli_env, stream_lines
from app.workers.knowledge_jobs import month_spend_usd

log = logging.getLogger(__name__)

# Two clones (workspace.GIT_TIMEOUT_S each) and packaging on top of the command's own time.
CLONE_MARGIN_S = 2 * int(workspace.GIT_TIMEOUT_S) + 10 * 60
# A running row past its own deadline plus this is a worker that died mid-run.
STALE_MARGIN = timedelta(minutes=30)
# A folder younger than this is never swept as an orphan (a run may have just created it).
ORPHAN_MIN_AGE_S = 3600


def job_timeout_s(timeout_s: int) -> int:
    return timeout_s + CLONE_MARGIN_S


def job_id(run_id: int) -> str:
    return f"command-{run_id}"


@dataclass
class Plan:
    """What the job needs from the database, read once (the run itself holds no transaction)."""

    run_id: int
    command: str
    arguments: str
    source: str
    ref: str | None
    repo_token: str
    repo_external_id: int | None
    shared_external_id: int | None
    shared_ref: str | None
    shared_token: str
    base_url: str
    shared_base_url: str


def _now() -> datetime:
    return datetime.now(UTC)


def _connection_token(session: Session, connection_id: int | None) -> tuple[str, str]:
    connection = session.get(Connection, connection_id) if connection_id else None
    if connection is None:
        raise RuntimeError("repo không có kết nối GitLab")
    return connection.base_url, secret_box().decrypt(connection.token_encrypted)


def _plan(session: Session, run: CommandRun, settings: Settings) -> Plan:
    """Checked again here, not only when queued: the repo may have changed since."""
    if not settings.command_runs_enabled:
        raise RuntimeError("chạy command đang tắt (COMMAND_RUNS_ENABLED=false)")
    if settings.llm_provider != "claude_code":
        raise RuntimeError("chạy command cần LLM_PROVIDER=claude_code")
    project = session.get(Project, run.project_id) if run.project_id else None
    if run.project_id and project is None:
        raise RuntimeError("repo không còn")
    if project is not None and project.review_enabled is False:
        raise RuntimeError("repo đang tắt review: không gửi code của repo ra LLM")
    business = (
        session.get(BusinessProject, run.business_project_id) if run.business_project_id else None
    )
    shared_id = business.claude_repo_external_id if business else None
    if (project is None or run.source == "shared") and not shared_id:
        raise RuntimeError("dự án không có repo .claude dùng chung")
    base_url, token = _connection_token(session, project.connection_id) if project else ("", "")
    shared_base, shared_token = (
        _connection_token(session, business.claude_repo_connection_id)
        if business and shared_id
        else ("", "")
    )
    return Plan(
        run_id=run.id,
        command=run.command,
        arguments=run.arguments or "",
        source=run.source,
        ref=run.ref,
        repo_token=token,
        repo_external_id=project.external_id if project else None,
        shared_external_id=shared_id,
        shared_ref=business.claude_repo_ref if business else None,
        shared_token=shared_token,
        base_url=base_url,
        shared_base_url=shared_base,
    )


def _finish(run_id: int, **values: Any) -> bool:
    """End a running run; False when it was no longer running (the stale sweep got it)."""
    with get_sessionmaker()() as session:
        done = session.execute(
            update(CommandRun)
            .where(CommandRun.id == run_id, CommandRun.status == "running")
            .values(**values, finished_at=_now())
        )
        session.commit()
        return bool(done.rowcount)


def command_file(work: Path, command: str) -> Path:
    """Where Claude Code finds `/a:b.c`: .claude/commands/a/b.c.md."""
    *folders, name = command.split(":")
    return work.joinpath(".claude", "commands", *folders, f"{name}.md")


def run_command_job(run_id: int, timeout_s: int | None = None) -> None:
    """Run one queued command (app/api/routers/commands.py queues it with the timeout fixed at
    that moment, the one RQ's job timeout was computed from)."""
    settings = get_settings()
    with get_sessionmaker()() as session:
        run = session.get(CommandRun, run_id)
        if run is None or run.status != "queued":
            return
        limits = effective(session, settings)
        timeout = timeout_s or int(limits["command_timeout_min"]) * 60
        store = RunStore(
            progress_redis(str(settings.redis_url)), int(limits["command_file_ttl_hours"]) * 3600
        )
        error = None
        try:
            plan = _plan(session, run, settings)
        except RuntimeError as exc:
            error = str(exc)
        budget = float(limits["monthly_llm_budget_usd"])
        if error is None and budget > 0 and month_spend_usd(session) >= budget:
            error = "đã hết ngân sách LLM của tháng (MONTHLY_LLM_BUDGET_USD)"
        now = _now()
        values: dict[str, Any] = (
            {"status": "failed", "error": error, "finished_at": now, "files_deleted_at": now}
            if error
            else {
                "status": "running",
                "started_at": now,
                "meta": {**(run.meta or {}), "deadline_s": timeout + CLONE_MARGIN_S},
            }
        )
        claimed = session.execute(
            update(CommandRun)
            .where(CommandRun.id == run_id, CommandRun.status == "queued")
            .values(**values)
        )
        session.commit()
        if error or not claimed.rowcount:
            return  # failed, or cancelled while it waited
    _execute(plan, settings, limits, store, timeout)


def _execute(
    plan: Plan, settings: Settings, limits: dict[str, Any], store: RunStore, timeout_s: int
) -> None:
    # The clone is prepared in this worker's own temporary folder, never on the shared volume;
    # only the one result file is written there (by this worker, under a name it chose).
    private = Path(tempfile.mkdtemp(prefix="codepulse-cmd-"))
    root = run_dir(settings.command_runs_dir, plan.run_id)
    workspace.remove(root)
    work, out = private / workspace.WORK, private / workspace.OUT
    out.mkdir()
    env = cli_env("")
    depth = int(limits["command_clone_depth"])
    max_bytes = int(limits["command_max_output_mb"]) * 1024 * 1024
    meta: dict[str, Any] = {"deadline_s": timeout_s + CLONE_MARGIN_S}
    ended: dict[str, Any] = {"repo_sha": None, "shared_sha": None, "shared_ref": None}

    def note(text: str) -> None:
        store.append(plan.run_id, [{"kind": "stage", "text": text, "at": _now().timestamp()}])

    def check_cancel() -> None:
        if store.cancelled(plan.run_id):
            raise runner.Cancelled

    outcome: runner.Outcome | None = None
    started = False  # the CLI was launched: from here on the run may have cost money
    gate: container.Gate | None = None
    boxed: container.ContainerRun | None = None
    try:
        if plan.repo_external_id is not None:
            with GitLabClient(plan.base_url, plan.repo_token) as client:
                url = str(client.project(plan.repo_external_id)["http_url_to_repo"])
            note(f"Clone repo, nhánh {plan.ref}")
            ended["repo_sha"] = workspace.clone(
                settings.git_bin,
                url,
                str(plan.ref),
                work,
                token=plan.repo_token,
                depth=depth,
                env=env,
            )
            workspace.remove_symlinks(work)
            check_cancel()
        if plan.shared_external_id is not None:
            with GitLabClient(plan.shared_base_url, plan.shared_token) as client:
                info = client.project(plan.shared_external_id)
            shared_ref = plan.shared_ref or str(info["default_branch"])
            ended["shared_ref"] = shared_ref
            shared = private / "shared"
            note(f"Clone repo .claude dùng chung, nhánh {shared_ref}")
            ended["shared_sha"] = workspace.clone(
                settings.git_bin,
                str(info["http_url_to_repo"]),
                shared_ref,
                shared,
                token=plan.shared_token,
                depth=1 if plan.repo_external_id is not None else depth,
                env=env,
            )
            workspace.remove_symlinks(shared)
            if plan.repo_external_id is not None:
                workspace.remove(shared / ".git")
                target = work / ".claude"
                target.mkdir(exist_ok=True)
                meta["replaced"] = workspace.overlay(workspace.shared_claude_dir(shared), target)
                workspace.remove(shared)
            elif (shared / ".claude").is_dir():
                shared.rename(work)
            else:  # the repo *is* the .claude folder: run from a folder that holds it as .claude
                work.mkdir()
                shared.rename(work / ".claude")
            check_cancel()
        path = command_file(work, plan.command)
        if not path.is_file():
            raise workspace.WorkspaceError(f"không thấy .claude/commands của /{plan.command}")
        parsed = catalog.parse(
            _rel(plan.command),
            path.read_text(encoding="utf-8", errors="replace"),
            plan.source,
            ".claude/commands",
        )
        keep_git = parsed is not None and workspace.needs_git(parsed.allowed_tools)
        if keep_git:  # history rebuilt from the cleaned tree: no SENSITIVE_PATHS file in it
            workspace.drop_sensitive(work, settings.sensitive_paths)
            workspace.reset_history(settings.git_bin, work, env)
        else:
            workspace.remove(work / ".git")
        meta["git_history"] = keep_git
        meta["settings_dropped"] = workspace.sanitize_settings(work / ".claude")
        meta["sensitive_removed"] = workspace.drop_sensitive(work, settings.sensitive_paths)
        before = workspace.snapshot(work)
        check_cancel()
        if settings.command_sandbox == "docker":
            gate = container.Gate(settings.command_gate_socket)
            boxed = container.ContainerRun(
                gate,
                run_id=plan.run_id,
                work=work,
                tar_path=private / "work.tar",
                max_output_bytes=max_bytes,
                max_disk_bytes=settings.command_disk_mb * 1024 * 1024,
                cancelled=lambda: store.cancelled(plan.run_id),
            )
        inner = Path(container.CONTAINER_HOME, "run")
        note(f"Chạy /{plan.command}" + (" trong container riêng" if boxed else ""))
        started = True
        outcome = runner.run(
            binary=settings.claude_code_bin,
            workdir=inner / workspace.WORK if boxed else work,
            out_dir=inner / workspace.OUT if boxed else out,
            command=plan.command,
            arguments=plan.arguments,
            model=str(limits["llm_model"]),
            max_turns=int(limits["command_max_turns"]),
            max_budget_usd=float(limits["command_max_budget_usd"]),
            timeout_s=float(timeout_s),
            auth_token=load_stored_claude_token()
            or settings.claude_code_oauth_token.get_secret_value()
            or settings.llm_api_key.get_secret_value(),
            on_entries=lambda entries: store.append(plan.run_id, entries),
            cancelled=lambda: store.cancelled(plan.run_id),
            streamer=boxed or stream_lines,
        )
        stored: tuple[str, int] | None = None
        if boxed is not None:  # what the container reports is shown only, never used as a path
            files = _listed(boxed.summary.get("files"))
            skipped = [str(x)[:300] for x in boxed.summary.get("skipped") or []][:200]
            stored = boxed.fetch(root)
        else:
            collected, download = workspace.finish(private, before, max_bytes)
            files, skipped = collected.files, collected.skipped
            if download is not None:
                name = workspace.safe_file_name(download.name)
                root.mkdir(parents=True, exist_ok=True)
                shutil.move(download, root / name)
                stored = (name, (root / name).stat().st_size)
    except runner.Cancelled:
        _fail(plan, root, "cancelled", None, meta, ended, _spent(outcome, limits, meta, started))
        store.append(plan.run_id, [{"kind": "end", "ok": False, "text": "Đã huỷ"}])
        return
    except (workspace.WorkspaceError, GitLabError, KeyError, OSError) as exc:
        error = workspace.scrub(workspace.scrub(str(exc), plan.repo_token), plan.shared_token)
        _fail(
            plan, root, "failed", error[:500], meta, ended, _spent(outcome, limits, meta, started)
        )
        store.append(plan.run_id, [{"kind": "end", "ok": False, "text": error[:500]}])
        return
    except BaseException:
        _fail(plan, root, "failed", "lỗi không xác định", meta, ended, None)
        raise
    finally:
        if boxed is not None:
            boxed.close()  # the container goes, with every process the command started
        if gate is not None:
            gate.close()
        workspace.remove(private)

    stored_name = stored[0] if stored else None
    meta.update(
        {
            "download": stored_name,
            "recognized": outcome.recognized,
            "denied": sorted(set(outcome.denied)),
            "skipped": skipped,
            "model": outcome.model,
            "sandbox": settings.command_sandbox,
        }
    )
    if stored_name is None:
        workspace.remove(root)
    store.set_result(plan.run_id, outcome.result)
    cost = _spent(outcome, limits, meta, ran=True)
    finished = _finish(
        plan.run_id,
        status="succeeded" if outcome.ok else "failed",
        error=outcome.error or None,
        cost_usd=cost,
        num_turns=outcome.turns,
        files=files,
        files_deleted_at=None if stored_name else _now(),
        meta=meta,
        **ended,
    )
    if not finished:  # the stale sweep already gave up on it: nobody will download these
        workspace.remove(root)


def _listed(raw: Any) -> list[dict[str, int | str]]:
    """The file list a container reported: shown only, never used as a path; kept small."""
    out: list[dict[str, int | str]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and isinstance(item.get("size"), int):
            out.append({"name": str(item.get("name"))[:300], "size": int(item["size"])})
    return out[:200]


def _rel(command: str) -> PurePosixPath:
    *folders, name = command.split(":")
    return PurePosixPath(*folders, f"{name}.md")


def _spent(
    outcome: runner.Outcome | None, limits: dict[str, Any], meta: dict[str, Any], ran: bool
) -> Decimal | None:
    """What the run cost. Without a final result (timeout, cancel) the CLI never said: count the
    cap, so the month's budget is not under-counted, and say it is an estimate."""
    if outcome is not None and outcome.turns is not None:
        return Decimal(str(round(outcome.cost_usd, 4)))
    if not ran:
        return None
    meta["cost_estimated"] = True
    return Decimal(int(limits["command_max_budget_usd"]))


def _fail(
    plan: Plan,
    root: Path,
    status: str,
    error: str | None,
    meta: dict[str, Any],
    ended: dict[str, Any],
    cost: Decimal | None,
) -> None:
    workspace.remove(root)
    try:
        _finish(
            plan.run_id,
            status=status,
            error=error,
            files=[],
            files_deleted_at=_now(),
            meta=meta,
            cost_usd=cost,
            **ended,
        )
    except Exception:  # the stale sweep will close it; the original error matters more
        log.exception("command run not marked ended")


def _job_alive(run_id: int) -> bool:
    """Whether RQ still holds the run's job (queued, scheduled or started)."""
    try:
        job = Job.fetch(job_id(run_id), connection=get_redis())
    except NoSuchJobError:
        return False
    return job.get_status(refresh=False) in (
        JobStatus.QUEUED,
        JobStatus.STARTED,
        JobStatus.SCHEDULED,
        JobStatus.DEFERRED,
    )


def cleanup_runs_job() -> None:
    """Hourly (scheduler): delete files nobody downloaded within COMMAND_FILE_TTL_HOURS, folders
    no run owns any more, containers a dead worker left behind, and mark runs whose worker
    died as failed."""
    settings = get_settings()
    root = Path(settings.command_runs_dir)
    now = _now()
    with get_sessionmaker()() as session:
        limits = effective(session, settings)
        expired = now - timedelta(hours=int(limits["command_file_ttl_hours"]))
        for run in session.scalars(
            select(CommandRun).where(
                CommandRun.files_deleted_at.is_(None),
                CommandRun.finished_at.is_not(None),
                CommandRun.finished_at < expired,
            )
        ):
            workspace.remove(run_dir(root, run.id))
            run.files_deleted_at = now
        session.commit()
        for run in session.scalars(
            select(CommandRun).where(CommandRun.status.in_(("queued", "running")))
        ).all():
            if run.status == "running":
                deadline = int((run.meta or {}).get("deadline_s") or 0) or job_timeout_s(
                    int(limits["command_timeout_min"]) * 60
                )
                started = run.started_at or run.created_at
                if started + timedelta(seconds=deadline) + STALE_MARGIN > now:
                    continue
            elif _job_alive(run.id) or run.created_at > now - STALE_MARGIN:
                continue
            gone = session.execute(
                update(CommandRun)
                .where(CommandRun.id == run.id, CommandRun.status == run.status)
                .values(
                    status="failed",
                    error="worker dừng giữa chừng",
                    finished_at=now,
                    files_deleted_at=now,
                )
            )
            session.commit()
            if gone.rowcount:
                workspace.remove(run_dir(root, run.id))
        keep = set(
            session.scalars(
                select(CommandRun.id).where(
                    CommandRun.files_deleted_at.is_(None)
                    | CommandRun.status.in_(("queued", "running"))
                )
            )
        )
        active = set(
            session.scalars(
                select(CommandRun.id).where(CommandRun.status.in_(("queued", "running")))
            )
        )
    if settings.command_sandbox == "docker":
        _sweep_containers(settings.command_gate_socket, active)
    if root.is_dir():
        for child in root.iterdir():
            if not child.is_dir() or (child.name.isdigit() and int(child.name) in keep):
                continue
            try:
                if time.time() - os.stat(child).st_mtime < ORPHAN_MIN_AGE_S:
                    continue  # a run created after `keep` was read
            except OSError:
                continue
            workspace.remove(child)


def _sweep_containers(socket_path: str, active: set[int]) -> None:
    """Remove the gate's containers of runs that are no longer queued or running (a worker
    killed mid-run leaves its container, with the repo's code and the Claude token in it)."""
    gate = container.Gate(socket_path)
    try:
        for row in gate.runs():
            run_id = str(row.get("run_id") or "")
            if run_id.isdigit() and int(run_id) in active:
                continue
            gate.remove(str(row.get("id")))
    except container.ContainerError as exc:
        log.warning("command containers not swept", extra={"error": str(exc)[:200]})
    finally:
        gate.close()
