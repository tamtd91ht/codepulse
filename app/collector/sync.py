"""Sync a project: collect -> extract -> convert in one transaction (backfill, then incremental)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.collector.gitlab.collect import GitLabCollector, window_start
from app.collector.gitlab.convert import Converter, raw_rows_query
from app.collector.gitlab.extract import extract
from app.config import get_settings
from app.crypto import SecretBox, secret_box
from app.db import get_redis, get_sessionmaker
from app.models import Connection, Project

log = logging.getLogger(__name__)

SYNC_JOB_TIMEOUT = 2 * 60 * 60  # a 90-day backfill of a busy project can take a while
ADVISORY_LOCK_NAMESPACE = 0x5C  # first key of pg_try_advisory_xact_lock(ns, project_id)

ClientFactory = Callable[[Connection], GitLabClient]


@dataclass
class SyncResult:
    project_id: int
    skipped: bool
    window_start: datetime | None = None
    counts: dict[str, int] | None = None


class Enqueuer(Protocol):
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> Any: ...


def client_factory(box: SecretBox) -> ClientFactory:
    def make(connection: Connection) -> GitLabClient:
        return GitLabClient(connection.base_url, box.decrypt(connection.token_encrypted))

    return make


def sync_project(
    session: Session,
    project: Project,
    make_client: ClientFactory,
    *,
    backfill_days: int,
    ignore_paths: list[str],
    now: datetime | None = None,
) -> SyncResult:
    """Run inside the caller's transaction; last_synced_at only advances if everything succeeds."""
    locked = session.scalar(
        select(func.pg_try_advisory_xact_lock(ADVISORY_LOCK_NAMESPACE, project.id))
    )
    if not locked:
        log.info("sync already running, skipped", extra={"project_id": project.id})
        return SyncResult(project.id, skipped=True)

    connection = session.get(Connection, project.connection_id)
    if connection is None:
        raise ValueError(f"project {project.id} has no connection")
    started = now or datetime.now(UTC)
    since = window_start(project, started, backfill_days)

    with make_client(connection) as client:
        collected = GitLabCollector(client, clock=lambda: started).collect(session, project, since)
    rows = session.scalars(raw_rows_query(project, fetched_since=collected.started_at)).all()
    converted = Converter(session, project, ignore_paths).run(extract(rows))

    project.last_synced_at = started
    session.flush()
    counts = {**collected.counts, **{f"domain.{k}": v for k, v in converted.counts.items()}}
    log.info("project synced", extra={"project_id": project.id, "counts": counts})
    return SyncResult(project.id, skipped=False, window_start=since, counts=counts)


def sync_project_job(project_id: int) -> dict[str, object]:
    """RQ entry point."""
    settings = get_settings()
    with get_sessionmaker()() as session, session.begin():
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        result = sync_project(
            session,
            project,
            client_factory(secret_box()),
            backfill_days=settings.backfill_days,
            ignore_paths=settings.ignore_paths,
        )
    return {"project_id": result.project_id, "skipped": result.skipped, "counts": result.counts}


def enqueue_syncs(session: Session, queue: Enqueuer) -> list[int]:
    """Queue one sync job per project; a fixed job id keeps a slow sync from piling up."""
    project_ids = list(session.scalars(select(Project.id).order_by(Project.id)))
    for project_id in project_ids:
        queue.enqueue(
            "app.collector.sync.sync_project_job",
            project_id,
            job_id=f"sync-project-{project_id}",
            job_timeout=SYNC_JOB_TIMEOUT,
        )
    return project_ids


def enqueue_syncs_job() -> None:
    """Scheduler entry point."""
    from rq import Queue

    with get_sessionmaker()() as session:
        enqueue_syncs(session, Queue("default", connection=get_redis()))
