"""Background handling of GitLab webhook events (enqueued by app.webhook.router)."""

import logging
from collections.abc import Callable
from typing import Any

from rq import Retry
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabError
from app.collector.sync import SYNC_JOB_TIMEOUT, Enqueuer, client_factory
from app.config import get_settings
from app.crypto import secret_box
from app.db import get_redis, get_sessionmaker
from app.models import Connection, Project
from app.review.status import supersede_mr_reviews
from app.workers.knowledge_jobs import KNOWLEDGE_JOB_TIMEOUT
from app.workers.review_jobs import review_job_timeout

log = logging.getLogger(__name__)

__all__ = ["REVIEW_RETRY", "handle_event", "review_job_timeout"]

REVIEW_ACTIONS = {"open", "reopen"}
NULL_SHA = "0" * 40
# docs/specs/19 §3.4: a push to the default branch that touches the Claude configuration
# rebuilds the review knowledge when the repo opted in, at most once an hour per repo.
KNOWLEDGE_THROTTLE_TTL_S = 3600
KNOWLEDGE_THROTTLE_KEY = "cp:knowledge:auto:{project_id}"
KNOWLEDGE_JOB_ID = "knowledge-{project_id}"  # same id as the "Rà soát lại" button
# A review that finds its MR not synced yet (sync running) waits and tries again.
REVIEW_RETRY = Retry(max=3, interval=[60, 120, 300])

CommitInMr = Callable[[Project, str], bool]


def touches_claude(payload: dict[str, Any]) -> bool:
    """Whether any file added, modified or removed by the push is the Claude configuration
    (`.claude/**` or `CLAUDE.md`, at the repo root)."""
    for commit in payload.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        for key in ("added", "modified", "removed"):
            paths = commit.get(key) or []
            for path in paths if isinstance(paths, list) else []:
                text = str(path).lstrip("/")
                if text == "CLAUDE.md" or text.startswith(".claude/"):
                    return True
    return False


def maybe_refresh_knowledge(
    project: Project, payload: dict[str, Any], queue: Enqueuer, redis: Any | None
) -> bool:
    """Queue `build_knowledge_job` for a default-branch push that touches `.claude/` or
    CLAUDE.md when the repo has `knowledge_auto_refresh`; otherwise only log. The Redis
    throttle key (TTL one hour) keeps it to one build per hour per repo; without Redis
    (tests) there is no throttle. Returns whether a job was queued."""
    if not touches_claude(payload):
        return False
    extra = {"project_id": project.id, "after": str(payload.get("after") or "")[:12]}
    if not project.knowledge_auto_refresh:
        log.info("claude config changed, knowledge auto refresh is off", extra=extra)
        return False
    job_id = KNOWLEDGE_JOB_ID.format(project_id=project.id)
    if knowledge_job_active(queue, job_id):
        # Re-enqueueing an id that is running would reset that job's state (RQ) and race a
        # second build of the same repo; the manual button refuses the same way (409).
        log.info("knowledge auto refresh skipped, a build is active", extra=extra)
        return False
    throttle = KNOWLEDGE_THROTTLE_KEY.format(project_id=project.id)
    if redis is not None and not redis.set(throttle, 1, nx=True, ex=KNOWLEDGE_THROTTLE_TTL_S):
        log.info("knowledge auto refresh throttled", extra=extra)
        return False
    try:
        queue.enqueue(
            "app.workers.knowledge_jobs.build_knowledge_job",
            project.id,
            built_by="auto",
            job_id=job_id,
            job_timeout=KNOWLEDGE_JOB_TIMEOUT,
        )
    except Exception:
        if redis is not None:  # nothing was queued: do not lock the repo out for an hour
            redis.delete(throttle)
        raise
    log.info("knowledge auto refresh queued", extra=extra)
    return True


def knowledge_job_active(queue: Any, job_id: str) -> bool:
    """Whether an RQ job with this id is waiting or running; False when the queue cannot say
    (a fake queue in tests, Redis trouble). Mirrors app.api.routers.knowledge._job_active."""
    fetch = getattr(queue, "fetch_job", None)
    if fetch is None:
        return False
    try:
        job = fetch(job_id)
        if job is None:
            return False
        status = job.get_status(refresh=False)
    except Exception as exc:
        log.warning("knowledge job status not readable", extra={"error": exc.__class__.__name__})
        return False
    return str(getattr(status, "value", status)) in ("queued", "started", "deferred", "scheduled")


def _enqueue_review(queue: Enqueuer, func: str, *args: Any, job_id: str) -> None:
    queue.enqueue(
        f"app.workers.review_jobs.{func}",
        *args,
        job_id=job_id,
        job_timeout=review_job_timeout(get_settings()),
        retry=REVIEW_RETRY,
        at_front=True,  # reviews before syncs: people wait for them
    )


def handle_event(
    session: Session,
    queue: Enqueuer,
    event: str,
    payload: dict[str, Any],
    commit_in_mr: CommitInMr | None = None,
    redis: Any | None = None,
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
        elif event == "Push Hook":
            branch = (payload.get("ref") or "").removeprefix("refs/heads/")
            default = (payload.get("project") or {}).get("default_branch") or project.default_branch
            if branch != default or payload.get("after") == NULL_SHA:
                continue
            maybe_refresh_knowledge(project, payload, queue, redis)
            if not project.review_enabled:
                continue
            # Decision (Phase 0): direct pushes to the default branch are reviewed too; commits
            # that belong to an MR were already reviewed there.
            commits = payload.get("commits")
            for commit in commits if isinstance(commits, list) else []:
                if not isinstance(commit, dict):
                    continue
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

    redis = get_redis()
    with get_sessionmaker()() as session, session.begin():
        handle_event(
            session,
            Queue("default", connection=redis),
            event,
            payload,
            commit_in_mr=gitlab_commit_in_mr(session),
            redis=redis,
        )
