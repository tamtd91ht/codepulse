"""Background handling of GitLab webhook events (enqueued by app.webhook.router)."""

import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.collector.sync import SYNC_JOB_TIMEOUT, Enqueuer
from app.db import get_redis, get_sessionmaker
from app.models import MergeRequest, Project, Review

log = logging.getLogger(__name__)

ACTIVE_REVIEW_STATUSES = ("queued", "running", "done")


def supersede_mr_reviews(session: Session, project: Project, iid: int, head_sha: str) -> int:
    """New push to an MR: earlier reviews are kept for history but marked superseded."""
    mr_id = session.scalar(
        select(MergeRequest.id).where(
            MergeRequest.project_id == project.id, MergeRequest.iid == iid
        )
    )
    if mr_id is None:
        return 0
    result = session.execute(
        update(Review)
        .where(
            Review.target_type == "mr",
            Review.mr_id == mr_id,
            Review.reviewed_sha != head_sha,
            Review.status.in_(ACTIVE_REVIEW_STATUSES),
        )
        .values(status="superseded")
    )
    return int(getattr(result, "rowcount", 0) or 0)


def handle_event(session: Session, queue: Enqueuer, event: str, payload: dict[str, Any]) -> None:
    external_id = (payload.get("project") or {}).get("id") or payload.get("project_id")
    projects = session.scalars(select(Project).where(Project.external_id == external_id)).all()
    for project in projects:
        attrs = payload.get("object_attributes") or {}
        if (
            event == "Merge Request Hook"
            and attrs.get("action") == "update"
            and attrs.get("oldrev")
        ):
            head = (attrs.get("last_commit") or {}).get("id") or ""
            count = supersede_mr_reviews(session, project, int(attrs["iid"]), head)
            log.info("reviews superseded", extra={"project_id": project.id, "count": count})
        # Pull the change into the domain layer; same job id as the scheduler, so no pile-up.
        queue.enqueue(
            "app.collector.sync.sync_project_job",
            project.id,
            job_id=f"sync-project-{project.id}",
            job_timeout=SYNC_JOB_TIMEOUT,
        )


def process_gitlab_event(event: str, payload: dict[str, Any]) -> None:
    """RQ entry point."""
    from rq import Queue

    with get_sessionmaker()() as session, session.begin():
        handle_event(session, Queue("default", connection=get_redis()), event, payload)
