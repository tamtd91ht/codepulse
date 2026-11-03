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
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector.gitlab.convert import Converter
from app.collector.gitlab.extract import extract
from app.models import AppSetting, Project, RawGitlab

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


def backfill_job(project_id: int) -> dict[str, Any]:
    """RQ entry point: convert the repo's raw rows again, then queue a full sync (deployments,
    incidents and anything raw no longer holds)."""
    from rq import Queue

    from app.collector.sync import SYNC_JOB_TIMEOUT
    from app.config import get_settings
    from app.db import get_redis, get_sessionmaker

    settings = get_settings()
    with get_sessionmaker()() as session, session.begin():
        project = session.get(Project, project_id)
        if project is None or not project.tracked:
            return {"project_id": project_id, "skipped": True}
        result = reconvert(session, project, settings.ignore_paths)
    if result.skipped:
        raise RuntimeError(f"a sync of project {project_id} is running; retry later")
    Queue("default", connection=get_redis()).enqueue(
        "app.collector.sync.sync_project_job",
        project_id,
        full=True,
        job_id=f"sync-project-{project_id}",
        job_timeout=SYNC_JOB_TIMEOUT,
    )
    return {"project_id": project_id, "runs": result.runs, "counts": result.counts}


def enqueue_backfill(session: Session, queue: Any, project_ids: list[int] | None = None) -> int:
    """Queue `backfill_job` for these tracked repos (default: all); returns how many."""
    from rq import Retry

    query = select(Project.id).where(Project.tracked).order_by(Project.id)
    if project_ids is not None:
        query = query.where(Project.id.in_(project_ids))
    ids = list(session.scalars(query))
    for project_id in ids:
        queue.enqueue(
            "app.insights.backfill.backfill_job",
            project_id,
            job_id=f"insights-backfill-{project_id}",
            job_timeout=BACKFILL_JOB_TIMEOUT,
            retry=Retry(max=3, interval=[300, 900, 1800]),
        )
    return len(ids)


def backfill_once_job() -> dict[str, Any]:
    """Scheduler start-up: after the upgrade to Phase 9, queue the backfill of every repo once."""
    from rq import Queue

    from app.db import get_redis, get_sessionmaker

    with get_sessionmaker()() as session, session.begin():
        if session.get(AppSetting, DONE_KEY) is not None:
            return {"queued": 0, "already": True}
        queued = enqueue_backfill(session, Queue("default", connection=get_redis()))
        session.add(AppSetting(key=DONE_KEY, value={"at": datetime.now(UTC).isoformat()}))
    return {"queued": queued}
