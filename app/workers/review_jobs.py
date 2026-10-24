"""RQ entry points for reviews (task 3.5). Enqueued by app.webhook.jobs."""

import logging
from collections.abc import Callable

from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.sync import ClientFactory, client_factory, sync_project
from app.config import Settings, get_settings
from app.crypto import secret_box
from app.db import get_redis, get_sessionmaker
from app.models import Commit, Connection, MergeRequest, Project, Review
from app.notifications.jobs import enqueue_review_notifications
from app.review.providers import LLMProvider, build_provider
from app.review.service import ReviewService, ReviewSettings

log = logging.getLogger(__name__)

REVIEW_JOB_TIMEOUT = 30 * 60


class NotSyncedYet(Exception):
    """The target is not in the domain layer yet (a sync is running); RQ retries the job."""


def review_settings(settings: Settings) -> ReviewSettings:
    return ReviewSettings(
        ignore_paths=settings.ignore_paths,
        sensitive_paths=settings.sensitive_paths,
        max_review_loc=settings.max_review_loc,
        max_tokens_per_chunk=settings.max_tokens_per_chunk,
        min_confidence=settings.min_confidence,
        monthly_budget_usd=settings.monthly_llm_budget_usd,
        scoring_weights=settings.scoring_weights,
    )


def _ensure_synced(
    session: Session, project: Project, make_client: ClientFactory, settings: Settings
) -> None:
    result = sync_project(
        session,
        project,
        make_client,
        backfill_days=settings.backfill_days,
        ignore_paths=settings.ignore_paths,
        allowed_email_domains=settings.allowed_email_domains,
    )
    if result.skipped:
        raise NotSyncedYet(f"project {project.id} is being synced")


def run_mr_review(
    session: Session,
    project_id: int,
    iid: int,
    head_sha: str,
    make_client: ClientFactory,
    provider: LLMProvider,
    settings: Settings,
    after: Callable[[Review], None] = lambda review: None,
    trigger: str = "webhook",
    requested_by: int | None = None,
) -> str:
    """Review one MR; `after` sees the finished review (notifications run once it is committed)."""
    project = session.get(Project, project_id)
    if project is None:
        return "project not found"
    if not project.tracked:
        return "repo not tracked"

    def load() -> MergeRequest | None:
        return session.scalars(
            select(MergeRequest).where(
                MergeRequest.project_id == project.id, MergeRequest.iid == iid
            )
        ).first()

    mr = load()
    if mr is None or mr.head_sha != head_sha:
        _ensure_synced(session, project, make_client, settings)
        mr = load()
    if mr is None:
        return "merge request not found after sync"
    connection = session.get(Connection, project.connection_id)
    if connection is None:
        raise ValueError(f"project {project.id} has no connection")
    with make_client(connection) as client:
        service = ReviewService(session, client, provider, review_settings(settings))
        review = service.review_mr(
            project, mr, head_sha, trigger=trigger, requested_by=requested_by
        )
    if review is not None:
        after(review)
    return review.status if review else "not reviewed"


def run_commit_review(
    session: Session,
    project_id: int,
    sha: str,
    make_client: ClientFactory,
    provider: LLMProvider,
    settings: Settings,
    after: Callable[[Review], None] = lambda review: None,
    trigger: str = "webhook",
    requested_by: int | None = None,
) -> str:
    project = session.get(Project, project_id)
    if project is None:
        return "project not found"
    if not project.tracked:
        return "repo not tracked"
    commit = session.get(Commit, sha)
    if commit is None:
        _ensure_synced(session, project, make_client, settings)
        commit = session.get(Commit, sha)
    if commit is None:
        return "commit not found after sync"
    connection = session.get(Connection, project.connection_id)
    if connection is None:
        raise ValueError(f"project {project.id} has no connection")
    with make_client(connection) as client:
        service = ReviewService(session, client, provider, review_settings(settings))
        review = service.review_commit(project, commit, trigger=trigger, requested_by=requested_by)
    if review is not None:
        after(review)
    return review.status if review else "not reviewed"


def _notify(finished: list[Review]) -> None:
    """After the review transaction committed: queue the notification webhooks, if any."""
    if not finished:
        return
    with get_sessionmaker()() as session, session.begin():
        queue = Queue("default", connection=get_redis())
        for review in finished:
            enqueue_review_notifications(session, queue, review)


def review_mr_job(
    project_id: int,
    iid: int,
    head_sha: str,
    *,
    trigger: str = "webhook",
    requested_by: int | None = None,
) -> str:
    """`trigger` / `requested_by` (docs/specs/17) say what asked for the review: webhook,
    dashboard, telegram, backfill or rerun, and which person, when one did."""
    settings = get_settings()
    finished: list[Review] = []
    with get_sessionmaker()() as session, session.begin():
        status = run_mr_review(
            session,
            project_id,
            iid,
            head_sha,
            client_factory(secret_box()),
            build_provider(settings),
            settings,
            after=finished.append,
            trigger=trigger,
            requested_by=requested_by,
        )
    _notify(finished)
    return status


def review_commit_job(
    project_id: int, sha: str, *, trigger: str = "webhook", requested_by: int | None = None
) -> str:
    settings = get_settings()
    finished: list[Review] = []
    with get_sessionmaker()() as session, session.begin():
        status = run_commit_review(
            session,
            project_id,
            sha,
            client_factory(secret_box()),
            build_provider(settings),
            settings,
            after=finished.append,
            trigger=trigger,
            requested_by=requested_by,
        )
    _notify(finished)
    return status
