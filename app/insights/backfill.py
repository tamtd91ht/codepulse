"""Fill the Insights data of repos that were synced before Phase 9 (docs/specs/20 §5, 9.12).

The domain tables Phase 9 added or widened — commit_files, merge_requests.merge/squash_commit_sha
— are filled by the converter, which only reads the rows of the run being synced. Repos synced
earlier have those raw rows already (raw_gitlab, kept RETENTION_DAYS), so they are converted
again here, one past sync run at a time (bounded memory, no GitLab call). Deployments and
incidents need GitLab: a full sync is queued afterwards; their first sync backfills the window.

Idempotent: the converter only upserts. Runs once by itself after an upgrade (the scheduler),
and on demand from Admin or the CLI.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector.gitlab.convert import Converter
from app.collector.gitlab.extract import extract
from app.models import AppSetting, InsightsBackfill, Project, RawGitlab

log = logging.getLogger(__name__)

# app_settings key: the automatic run after the upgrade happened (its value says when).
DONE_KEY = "insights_backfill_v1"
BACKFILL_JOB_TIMEOUT = 2 * 60 * 60
ADVISORY_LOCK_NAMESPACE = 0x5C  # the sync's lock (app/collector/sync.py): never run beside it


@dataclass
class BackfillResult:
    project_id: int
    skipped: bool = False  # a sync of the repo was running: try again later
    runs: int = 0
    counts: dict[str, int] = field(default_factory=dict)


def reconvert(session: Session, project: Project, ignore_paths: list[str]) -> BackfillResult:
    """Convert every stored raw run of `project` again, oldest first, in the caller's
    transaction. A later run wins, as in the original syncs."""
    result = BackfillResult(project.id)
    locked = session.scalar(
        select(func.pg_try_advisory_xact_lock(ADVISORY_LOCK_NAMESPACE, project.id))
    )
    if not locked:
        result.skipped = True
        return result
    runs = list(
        session.scalars(
            select(RawGitlab.fetched_at)
            .where(RawGitlab.project_id == project.id)
            .group_by(RawGitlab.fetched_at)
            .order_by(RawGitlab.fetched_at.asc().nulls_first())
        )
    )
    converter = Converter(session, project, ignore_paths)
    for fetched_at in runs:
        query = select(RawGitlab).where(RawGitlab.project_id == project.id)
        query = query.where(
            RawGitlab.fetched_at.is_(None)
            if fetched_at is None
            else RawGitlab.fetched_at == fetched_at
        )
        rows = session.scalars(query.order_by(RawGitlab.id)).all()
        converted = converter.run(extract(rows))
        for key, value in converted.counts.items():
            result.counts[key] = result.counts.get(key, 0) + value
        result.runs += 1
        session.expunge_all()  # one run's rows at a time in memory
        project = session.get(Project, result.project_id) or project
        converter = Converter(session, project, ignore_paths)
    log.info(
        "insights backfill",
        extra={"project_id": result.project_id, "runs": result.runs, "counts": result.counts},
    )
    return result


# A run that has not moved for this long is taken as dead (its worker stopped): it may be queued
# again, and the progress shows it as failed.
STALE = timedelta(hours=3)
ACTIVE = ("queued", "reconverting", "syncing")


def _mark(project_id: int, **values: Any) -> None:
    """Progress in its own short transaction, so the dashboard sees each step."""
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session, session.begin():
        row = session.get(InsightsBackfill, project_id)
        if row is None:
            row = InsightsBackfill(project_id=project_id, status="queued")
            session.add(row)
        for key, value in values.items():
            setattr(row, key, value)


def backfill_job(project_id: int) -> dict[str, Any]:
    """RQ entry point: convert the repo's raw rows again, then a full sync (deployments,
    incidents and anything raw no longer holds), recording each step in insights_backfills."""
    from app.collector.sync import sync_project_job
    from app.config import get_settings
    from app.db import get_sessionmaker

    settings = get_settings()
    _mark(project_id, status="reconverting", started_at=datetime.now(UTC), error=None)
    try:
        with get_sessionmaker()() as session, session.begin():
            project = session.get(Project, project_id)
            if project is None or not project.tracked:
                raise RuntimeError("repo không còn được theo dõi")
            result = reconvert(session, project, settings.ignore_paths)
        if result.skipped:
            # RQ retries the job a little later (enqueue_backfill's Retry).
            _mark(
                project_id,
                status="queued",
                error="Đang có một lần sync khác của repo; chờ chạy lại",
            )
            raise RuntimeError(f"a sync of project {project_id} is running; retry later")
        _mark(project_id, status="syncing", runs=result.runs, counts=result.counts)
        synced = sync_project_job(project_id, full=True)
        if synced.get("skipped"):
            raise RuntimeError(
                "lần sync đầy đủ bị bỏ qua (repo đang sync, hoặc không còn theo dõi)"
            )
    except Exception as exc:
        if "retry later" not in str(exc):
            _mark(
                project_id,
                status="failed",
                error=str(exc)[:500] or exc.__class__.__name__,
                finished_at=datetime.now(UTC),
            )
        raise
    _mark(project_id, status="done", finished_at=datetime.now(UTC), error=None)
    return {"project_id": project_id, "runs": result.runs, "counts": result.counts}


def enqueue_backfill(
    session: Session,
    queue: Any,
    project_ids: list[int] | None = None,
    requested_by: int | None = None,
) -> dict[str, int]:
    """Queue `backfill_job` for these tracked repos (default: all), except those already being
    filled; returns {"queued": n, "running": m}."""
    from rq import Retry

    query = select(Project.id).where(Project.tracked).order_by(Project.id)
    if project_ids is not None:
        query = query.where(Project.id.in_(project_ids))
    ids = list(session.scalars(query))
    now = datetime.now(UTC)
    rows = {
        r.project_id: r
        for r in session.scalars(
            select(InsightsBackfill).where(InsightsBackfill.project_id.in_(ids))
        )
    }
    queued = running = 0
    for project_id in ids:
        row = rows.get(project_id)
        moved = row.updated_at if row is not None else None
        if row is not None and row.status in ACTIVE and moved and now - moved < STALE:
            running += 1
            continue
        if row is None:
            row = InsightsBackfill(project_id=project_id, status="queued")
            session.add(row)
        row.status, row.error, row.runs, row.counts = "queued", None, None, None
        row.requested_by, row.queued_at = requested_by, now
        row.started_at = row.finished_at = None
        session.flush()
        queue.enqueue(
            "app.insights.backfill.backfill_job",
            project_id,
            job_id=f"insights-backfill-{project_id}",
            job_timeout=BACKFILL_JOB_TIMEOUT,
            retry=Retry(max=3, interval=[300, 900, 1800]),
        )
        queued += 1
    return {"queued": queued, "running": running}


def progress(session: Session, project_ids: list[int] | None = None) -> dict[str, Any]:
    """The latest backfill of each repo (all, or `project_ids`), for the dashboard."""
    now = datetime.now(UTC)
    query = (
        select(InsightsBackfill, Project.path_with_namespace)
        .join(Project, Project.id == InsightsBackfill.project_id)
        .order_by(Project.path_with_namespace)
    )
    if project_ids is not None:
        query = query.where(InsightsBackfill.project_id.in_(project_ids))
    items = []
    summary = dict.fromkeys(("queued", "reconverting", "syncing", "done", "failed"), 0)
    for row, path in session.execute(query).all():
        status = row.status
        if status in ACTIVE and row.updated_at and now - row.updated_at > STALE:
            status = "failed"  # its worker stopped: shown as failed, may be queued again
        summary[status] = summary.get(status, 0) + 1
        items.append(
            {
                "project_id": row.project_id,
                "path_with_namespace": path,
                "status": status,
                "runs": row.runs,
                "commits": (row.counts or {}).get("commits"),
                "error": row.error
                if status != "failed" or row.error
                else "Job dừng giữa chừng (worker khởi động lại?); bấm đồng bộ lại",
                "queued_at": row.queued_at.isoformat() if row.queued_at else None,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            }
        )
    active = summary["queued"] + summary["reconverting"] + summary["syncing"]
    return {"total": len(items), "active": active, **summary, "items": items}


def backfill_once_job() -> dict[str, Any]:
    """Scheduler start-up: after the upgrade to Phase 9, queue the backfill of every repo once."""
    from rq import Queue

    from app.db import get_redis, get_sessionmaker

    with get_sessionmaker()() as session, session.begin():
        if session.get(AppSetting, DONE_KEY) is not None:
            return {"queued": 0, "already": True}
        queued = enqueue_backfill(session, Queue("default", connection=get_redis()))
        session.add(AppSetting(key=DONE_KEY, value={"at": datetime.now(UTC).isoformat()}))
    return queued
