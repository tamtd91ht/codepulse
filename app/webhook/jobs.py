"""Background handling of GitLab webhook events (enqueued by app.webhook.router)."""

import logging
from collections.abc import Callable
from typing import Any

from rq import Retry
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabError
from app.collector.sync import SYNC_JOB_TIMEOUT, Enqueuer, client_factory
from app.crypto import secret_box
from app.db import get_redis, get_sessionmaker
from app.models import Connection, Project
from app.review.status import supersede_mr_reviews

log = logging.getLogger(__name__)

REVIEW_JOB_TIMEOUT = 45 * 60  # keep equal to app.workers.review_jobs.REVIEW_JOB_TIMEOUT
REVIEW_ACTIONS = {"open", "reopen"}
NULL_SHA = "0" * 40
# A review that finds its MR not synced yet (sync running) waits and tries again.
REVIEW_RETRY = Retry(max=3, interval=[60, 120, 300])

CommitInMr = Callable[[Project, str], bool]


def _enqueue_review(queue: Enqueuer, func: str, *args: Any, job_id: str) -> None:
    queue.enqueue(
        f"app.workers.review_jobs.{func}",
        *args,
        job_id=job_id,
        job_timeout=REVIEW_JOB_TIMEOUT,
        retry=REVIEW_RETRY,
    )


def handle_event(
    session: Session,
    queue: Enqueuer,
    event: str,
    payload: dict[str, Any],
    commit_in_mr: CommitInMr | None = None,
) -> None:
    external_id = (payload.get("project") or {}).get("id") or payload.get("project_id")
    projects = session.scalars(
        select(Project).where(Project.external_id == external_id, Project.tracked)
    ).all()
    attrs = payload.get("object_attributes") or {}
    for project in projects:
        # Pull the change into the domain layer; same job id as the scheduler, so no pile-up.
        queue.enqueue(
            "app.collector.sync.sync_project_job",
            project.id,
            job_id=f"sync-project-{project.id}",
            job_timeout=SYNC_JOB_TIMEOUT,
        )
        if event == "Merge Request Hook":
            head = (attrs.get("last_commit") or {}).get("id") or ""
            new_commits = attrs.get("action") == "update" and attrs.get("oldrev")
            if new_commits:
                count = supersede_mr_reviews(session, project, int(attrs["iid"]), head)
                log.info("reviews superseded", extra={"project_id": project.id, "count": count})
            if (
                project.review_enabled
                and head
                and (attrs.get("action") in REVIEW_ACTIONS or new_commits)
            ):
                iid = int(attrs["iid"])
                _enqueue_review(
                    queue,
                    "review_mr_job",
                    project.id,
                    iid,
                    head,
                    job_id=f"review-mr-{project.id}-{iid}-{head[:12]}",
                )
        elif event == "Push Hook" and project.review_enabled:
            branch = (payload.get("ref") or "").removeprefix("refs/heads/")
            default = (payload.get("project") or {}).get("default_branch") or project.default_branch
            if branch != default or payload.get("after") == NULL_SHA:
                continue
            # Decision (Phase 0): direct pushes to the default branch are reviewed too; commits
            # that belong to an MR were already reviewed there.
            for commit in payload.get("commits") or []:
                sha = commit.get("id")
                if not sha or (commit_in_mr and commit_in_mr(project, sha)):
                    continue
                _enqueue_review(
                    queue, "review_commit_job", project.id, sha, job_id=f"review-commit-{sha[:12]}"
                )


def gitlab_commit_in_mr(session: Session) -> CommitInMr:
    box = secret_box()
    make_client = client_factory(box)

    def check(project: Project, sha: str) -> bool:
        connection = session.get(Connection, project.connection_id)
        if connection is None:
            return False
        try:
            with make_client(connection) as client:
                return bool(client.commit_merge_requests(project.external_id, sha))
        except GitLabError:
            return False  # when unsure, review it: a duplicate review beats a missed one

    return check


def process_gitlab_event(event: str, payload: dict[str, Any]) -> None:
    """RQ entry point."""
    from rq import Queue

    with get_sessionmaker()() as session, session.begin():
        handle_event(
            session,
            Queue("default", connection=get_redis()),
            event,
            payload,
            commit_in_mr=gitlab_commit_in_mr(session),
        )
