"""RQ entry points for reviews (task 3.5). Enqueued by app.webhook.jobs."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin_settings import effective, with_overrides
from app.collector.gitlab.client import GitLabError
from app.collector.sync import ClientFactory, client_factory, fetch_commit, sync_project
from app.config import Settings, get_settings
from app.crypto import secret_box
from app.db import get_redis, get_sessionmaker
from app.models import Commit, Connection, MergeRequest, Project, Review
from app.notifications.jobs import enqueue_review_notifications
from app.review.checkout import checkout
from app.review.discover import discover
from app.review.profile import build_profile
from app.review.progress import ProgressSink, RedisProgress, progress_redis
from app.review.providers import (
    AgenticProvider,
    LLMProvider,
    build_agentic_provider,
    build_provider,
)
from app.review.service import ReviewService, ReviewSettings

log = logging.getLogger(__name__)

# Fast pass + profile + deep pass (DEEP_REVIEW_TIMEOUT_S) must fit; STALE_RUNNING is longer.
REVIEW_JOB_TIMEOUT = 45 * 60


class NotSyncedYet(Exception):
    """The target is not in the domain layer yet (a sync is running); RQ retries the job."""


def review_settings(settings: Settings, session: Session | None = None) -> ReviewSettings:
    """From the environment, with what an admin set on the Admin screen on top (app_settings)."""
    edited = effective(session, settings) if session is not None else {}
    return ReviewSettings(
        ignore_paths=settings.ignore_paths,
        sensitive_paths=settings.sensitive_paths,
        max_review_loc=int(edited.get("max_review_loc", settings.max_review_loc)),
        max_tokens_per_chunk=settings.max_tokens_per_chunk,
        min_confidence=settings.min_confidence,
        monthly_budget_usd=settings.monthly_llm_budget_usd,
        claude_file_chars=int(edited.get("claude_file_chars", settings.claude_file_chars)),
        fast_guidance_chars=int(edited.get("fast_guidance_chars", settings.fast_guidance_chars)),
        deep_instructions_chars=int(
            edited.get("deep_instructions_chars", settings.deep_instructions_chars)
        ),
        scoring_weights=settings.scoring_weights,
        deep_review=settings.deep_review,
        deep_max_turns=settings.deep_review_max_turns,
        deep_timeout_s=float(settings.deep_review_timeout_s),
        deep_max_archive_bytes=settings.deep_review_max_archive_mb * 1024 * 1024,
        deep_min_loc=settings.deep_review_min_loc,
        profile_ttl=timedelta(days=settings.profile_ttl_days),
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
    agent: AgenticProvider | None = None,
    force: bool = False,
    expected_round: int | None = None,
    progress: ProgressSink | None = None,
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
        service = ReviewService(
            session,
            client,
            provider,
            review_settings(settings, session),
            agent=agent,
            progress=progress,
        )
        review = service.review_mr(
            project,
            mr,
            head_sha,
            trigger=trigger,
            requested_by=requested_by,
            force=force,
            expected_round=expected_round,
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
    agent: AgenticProvider | None = None,
    force: bool = False,
    expected_round: int | None = None,
    progress: ProgressSink | None = None,
) -> str:
    project = session.get(Project, project_id)
    if project is None:
        return "project not found"
    if not project.tracked:
        return "repo not tracked"
    commit = session.get(Commit, sha)
    if commit is None:
        # Only this commit, not a whole-project sync: one broken MR elsewhere in the repo must
        # not block the review of a commit picked on another branch. (The RQ job prefetches it
        # in a separate transaction; this covers direct callers.)
        _prefetch_commit(session, project.id, sha, make_client, settings)
        commit = session.get(Commit, sha)
    if commit is None:
        return "commit not found on GitLab"
    connection = session.get(Connection, project.connection_id)
    if connection is None:
        raise ValueError(f"project {project.id} has no connection")
    with make_client(connection) as client:
        service = ReviewService(
            session,
            client,
            provider,
            review_settings(settings, session),
            agent=agent,
            progress=progress,
        )
        review = service.review_commit(
            project,
            commit,
            trigger=trigger,
            requested_by=requested_by,
            force=force,
            expected_round=expected_round,
        )
    if review is not None:
        after(review)
    return review.status if review else "not reviewed"


def _prefetch_commit(
    session: Session, project_id: int, sha: str, make_client: ClientFactory, settings: Settings
) -> None:
    project = session.get(Project, project_id)
    if project is None or not project.tracked or session.get(Commit, sha) is not None:
        return
    fetched = fetch_commit(
        session,
        project,
        make_client,
        sha,
        ignore_paths=settings.ignore_paths,
        allowed_email_domains=settings.allowed_email_domains,
    )
    if fetched is None:
        raise NotSyncedYet(f"project {project.id} is being synced")


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
    force: bool = False,
    expected_round: int | None = None,
) -> str:
    """`trigger` / `requested_by` (docs/specs/17) say what asked for the review: webhook,
    dashboard, telegram, backfill or rerun, and which person, when one did. `force` reviews a
    finished head again as a new round."""
    settings = get_settings()
    finished: list[Review] = []
    with get_sessionmaker()() as session, session.begin():
        # The model an admin picked on the Admin screen, if any (app/admin_settings).
        tuned = with_overrides(session, settings)
        status = run_mr_review(
            session,
            project_id,
            iid,
            head_sha,
            client_factory(secret_box()),
            build_provider(tuned),
            settings,
            after=finished.append,
            trigger=trigger,
            requested_by=requested_by,
            agent=build_agentic_provider(tuned),
            force=force,
            expected_round=expected_round,
            progress=RedisProgress(progress_redis(str(settings.redis_url))),
        )
    _notify(finished)
    return status


def review_commit_job(
    project_id: int,
    sha: str,
    *,
    trigger: str = "webhook",
    requested_by: int | None = None,
    force: bool = False,
    expected_round: int | None = None,
) -> str:
    settings = get_settings()
    # A commit the last sync has not seen is fetched in its own short transaction, so the
    # project's sync lock is not held through the (minutes long) review below.
    with get_sessionmaker()() as session, session.begin():
        _prefetch_commit(session, project_id, sha, client_factory(secret_box()), settings)
    finished: list[Review] = []
    with get_sessionmaker()() as session, session.begin():
        # The model an admin picked on the Admin screen, if any (app/admin_settings).
        tuned = with_overrides(session, settings)
        status = run_commit_review(
            session,
            project_id,
            sha,
            client_factory(secret_box()),
            build_provider(tuned),
            settings,
            after=finished.append,
            trigger=trigger,
            requested_by=requested_by,
            agent=build_agentic_provider(tuned),
            force=force,
            expected_round=expected_round,
            progress=RedisProgress(progress_redis(str(settings.redis_url))),
        )
    _notify(finished)
    return status


def refresh_profile_job(project_id: int) -> dict[str, Any]:
    """Admin button: rebuild the project profile from the default branch (docs/specs/18)."""
    settings = get_settings()
    with get_sessionmaker()() as session, session.begin():
        project = session.get(Project, project_id)
        if project is None or not project.tracked:
            return {"built": False, "reason": "repo not tracked"}
        if project.allow_external_llm is False:
            return {"built": False, "reason": "external LLM disabled for this repo"}
        connection = session.get(Connection, project.connection_id)
        if connection is None:
            return {"built": False, "reason": "no connection"}
        ref = project.default_branch or "HEAD"
        with client_factory(secret_box())(connection) as client:
            try:
                head = str(client.commit(project.external_id, ref).get("id") or ref)
            except GitLabError:
                head = ref
            with checkout(
                client,
                project.external_id,
                head,
                max_archive_bytes=settings.deep_review_max_archive_mb * 1024 * 1024,
                sensitive_paths=settings.sensitive_paths,
            ) as (root, _stats):
                try:
                    profile, usage = build_profile(
                        build_provider(with_overrides(session, settings)),
                        discover(root),
                        project.path_with_namespace,
                    )
                except ValueError as exc:
                    log.warning("profile refresh: unusable answer", extra={"error": str(exc)[:120]})
                    return {"built": False, "reason": "unusable answer"}
        project.profile, project.profile_sha = profile, head
        project.profile_at = datetime.now(UTC)
        project.profile["cost_usd"] = round(usage.cost_usd, 4)
        return {"built": True, "confidence": profile.get("confidence")}
