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
from app.review.knowledge import active_knowledge
from app.review.profile import build_profile
from app.review.progress import ProgressSink, RedisProgress, progress_redis
from app.review.providers import (
    AgenticProvider,
    LLMProvider,
    build_agentic_provider,
    build_provider,
)
from app.review.service import VERIFY_TIMEOUT_S, ReviewService, ReviewSettings

log = logging.getLogger(__name__)

# How long a killed job's row stays `running` past the job timeout before a claim takes it over.
STALE_GRACE_S = 5 * 60


def review_job_timeout(settings: Settings) -> int:
    """RQ `job_timeout` of a review job, from the settings: the agentic pass may take up to
    three chunks of REVIEW_TIMEOUT_S (the chunk count is unknown when the job is queued), the
    verification up to VERIFY_MAX_FINDINGS calls of 120 s, plus ten minutes for the sync, the
    checkout and the no-tool fallback. `ReviewSettings.stale_running` is this plus
    STALE_GRACE_S, so a review the worker was killed in is always taken over."""
    return int(
        settings.review_timeout_s * 3 + settings.verify_max_findings * VERIFY_TIMEOUT_S + 600
    )


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
        agent_review=settings.review_agent,
        agent_min_loc=settings.review_agent_min_loc,
        agent_max_turns=settings.review_max_turns,
        agent_timeout_s=float(settings.review_timeout_s),
        agent_max_archive_bytes=settings.deep_review_max_archive_mb * 1024 * 1024,
        chunk_tokens=settings.review_chunk_tokens,
        verify=settings.verify,
        verify_max_findings=settings.verify_max_findings,
        verify_max_turns=settings.verify_max_turns,
        verify_min_confidence_unverified=settings.verify_min_confidence_unverified,
        knowledge_tokens=settings.knowledge_tokens,
        stale_running=timedelta(seconds=review_job_timeout(settings) + STALE_GRACE_S),
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
    """Review one MR; `after` sees the finished review (notifications run once it is committed).

    Transactions (docs/specs/19 §4.6): given a session with no transaction open, the review
    runs in short ones of its own (the sync's rows are committed first, then the claim, the
    preparation and the save), so no lock outlives a step. Given a session already in a
    transaction (the integration tests, or a caller that wants one unit of work) everything
    stays in that single transaction and the caller commits or rolls back."""
    short = not session.in_transaction()
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
        if short:
            session.commit()  # the sync's rows (and its advisory lock) do not wait for the review
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
            short_transactions=short,
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
    """Review one commit; transactions as in `run_mr_review`."""
    short = not session.in_transaction()
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
        if short:
            session.commit()
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
            short_transactions=short,
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


def _ensure_knowledge(project_id: int, settings: Settings) -> None:
    """docs/specs/19 §3.4: a repo without review knowledge gets its first RK built before its
    first review (own sessions, own cost row). Not more than once an hour per repo, and not
    while the repo's build job is queued or running (`first_build_allowed`: a failed build
    leaves a row and that row counts). Any failure leaves the review to the .claude guidance
    fallback; it never blocks the review."""
    if not settings.review_agent:
        return
    from app.workers.knowledge_jobs import build_knowledge_job, first_build_allowed

    try:
        with get_sessionmaker()() as session:
            project = session.get(Project, project_id)
            if project is None or not project.tracked or project.allow_external_llm is False:
                return
            row, _stale = active_knowledge(session, project)
            if row is not None:
                return
            redis = get_redis()
            if not first_build_allowed(
                session, project_id, redis=redis, queue=Queue("default", connection=redis)
            ):
                log.info("first review knowledge build held off", extra={"project_id": project_id})
                return
    except Exception:  # Redis or the database not answering: no build, the review still runs
        log.exception("first review knowledge not decided", extra={"project_id": project_id})
        return
    try:
        result = build_knowledge_job(project_id, built_by="first_review")
        log.info("first review knowledge built", extra={"project_id": project_id, **result})
    except Exception:  # the review itself must still run
        log.exception("first review knowledge not built", extra={"project_id": project_id})


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
    _ensure_knowledge(project_id, settings)
    finished: list[Review] = []
    with get_sessionmaker()() as session:
        # The model an admin picked on the Admin screen, if any (app/admin_settings).
        tuned = with_overrides(session, settings)
        session.commit()  # end that read: the review runs in short transactions of its own
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
    _ensure_knowledge(project_id, settings)
    finished: list[Review] = []
    with get_sessionmaker()() as session:
        # The model an admin picked on the Admin screen, if any (app/admin_settings).
        tuned = with_overrides(session, settings)
        session.commit()  # end that read: the review runs in short transactions of its own
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
