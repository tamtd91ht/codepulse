"""RQ entry point that builds a repo's review knowledge (docs/specs/19 §3.2): checkout of the
default branch, the document layer (one LLM call without tools, batched when large), the
dự án's shared .claude repo compiled once per sha, the merge, and a new `review_knowledge` row.
The code probe (layer 2, app/review/knowledge_probe.py) runs after the document layer when an
agentic provider is available; its failure keeps layer 1 (`meta.code_probe` says why).

Progress goes to the same Redis records as reviews (kind "knowledge", a negative id so it
never collides with a review id), stages docs → compile → probe → save.

Money: every answered model call lands in a `CostLedger` before its answer is parsed, and a
build that fails after paying stores a `failed` row carrying that cost (`data.meta.cost_usd`,
`data.meta.error`), so `month_spend_usd` never misses what was spent and a repo whose build
just failed is not rebuilt by every review (`first_build_allowed`).
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from rq.job import Job
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin_settings import with_overrides
from app.collector.gitlab.client import ArchiveTooLarge, GitLabClient, GitLabError
from app.collector.sync import client_factory
from app.config import Settings, get_settings
from app.crypto import secret_box
from app.db import get_sessionmaker
from app.models import (
    BusinessProject,
    CommandRun,
    Connection,
    Project,
    Review,
    ReviewKnowledge,
    User,
)
from app.review.checkout import CheckoutError, checkout
from app.review.claude_kit import scan
from app.review.claude_repo import SharedConfig, load_shared_config
from app.review.discover import Doc, discover
from app.review.knowledge import (
    MAX_OMITTED,
    SCHEMA_VERSION,
    Knowledge,
    KnowledgeBudgetError,
    claude_tree_sha,
    compile_documents,
    merge,
    select_sources,
)
from app.review.knowledge_probe import merge_probe, probe_code
from app.review.progress import ProgressSink, RedisProgress, progress_redis
from app.review.providers import (
    AgenticProvider,
    LLMProvider,
    build_agentic_provider,
    build_provider,
)
from app.review.providers.base import LLMError, ReviewResult

log = logging.getLogger(__name__)

KNOWLEDGE_JOB_TIMEOUT = 30 * 60
STAGES = ("docs", "compile", "probe", "save")
JOB_ID = "knowledge-{project_id}"  # the RQ job id of a repo's build: button, webhook, review
ACTIVE_JOB_STATES = frozenset({"queued", "started", "deferred", "scheduled"})
# A review builds a repo's first knowledge at most once an hour (docs/specs/19 §3.4): the newest
# row of the repo (any status, failed included) and this Redis NX key both say "not again yet".
FIRST_BUILD_KEY = "cp:knowledge:first:{project_id}"
FIRST_BUILD_TTL_S = 3600
RECENT_BUILD = timedelta(seconds=FIRST_BUILD_TTL_S)


def job_id_for(project_id: int) -> str:
    return JOB_ID.format(project_id=project_id)


def job_active(queue: Any, job_id: str) -> bool:
    """Whether an RQ job with this id is waiting or running; False when the queue cannot say
    (a fake queue in tests, Redis trouble)."""
    fetch = getattr(queue, "fetch_job", None)
    if fetch is None:
        return False
    try:
        job = fetch(job_id)
        if job is None:
            return False
        status = job.get_status(refresh=False)
    except Exception as exc:  # the queue is a bonus here, never an error
        log.warning("knowledge job status not readable", extra={"error": exc.__class__.__name__})
        return False
    return str(getattr(status, "value", status)) in ACTIVE_JOB_STATES


def _status(status: object) -> str:
    return str(getattr(status, "value", status))


def jobs_active(queue: Any, job_ids: list[str]) -> set[str]:
    """`job_active` for many ids in one Redis round trip (RQ's `Job.fetch_many` pipelines the
    reads and, unlike `Queue.fetch_job`, writes nothing for a missing job). Empty when the queue
    cannot say; a queue without a Redis connection (tests) is asked one id at a time."""
    if not job_ids:
        return set()
    connection = getattr(queue, "connection", None)
    if connection is None:
        return {j for j in job_ids if job_active(queue, j)}
    try:
        jobs = Job.fetch_many(job_ids, connection=connection)
        return {
            job.id
            for job in jobs
            if job is not None and _status(job.get_status(refresh=False)) in ACTIVE_JOB_STATES
        }
    except Exception as exc:  # the queue is a bonus here, never an error
        log.warning("knowledge job status not readable", extra={"error": exc.__class__.__name__})
        return set()


@dataclass
class CostLedger:
    """What a build has paid so far, written as each model call is answered."""

    calls: list[float] = field(default_factory=list)

    def add(self, result: ReviewResult) -> None:
        self.calls.append(float(result.cost_usd or 0))

    @property
    def total(self) -> float:
        return round(sum(self.calls), 4)


def progress_id(project_id: int) -> int:
    """The progress record id of a knowledge build: negative, so it never names a review."""
    return -project_id


@dataclass
class _Target:
    """What the job needs from the database, copied out so no transaction stays open while
    GitLab and the model are called."""

    project_id: int
    external_id: int
    path: str
    default_branch: str
    connection_id: int
    business_project_id: int | None
    business_name: str | None


def month_spend_usd(session: Session, now: datetime | None = None) -> float:
    """The month's LLM spend: reviews' `cost_usd` plus every knowledge row's `meta.cost_usd`,
    whatever its status (active, superseded, failed: all were paid), plus the .claude command
    runs (app/commands). The one budget figure they all share (docs/specs/19 §6)."""
    now = now or datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    reviews = session.scalar(
        select(func.coalesce(func.sum(Review.cost_usd), 0)).where(Review.started_at >= month_start)
    )
    commands = session.scalar(
        select(func.coalesce(func.sum(CommandRun.cost_usd), 0)).where(
            CommandRun.created_at >= month_start
        )
    )
    builds = 0.0
    for data in session.scalars(
        select(ReviewKnowledge.data).where(ReviewKnowledge.created_at >= month_start)
    ):
        try:
            builds += float((data or {}).get("meta", {}).get("cost_usd") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
    return round(float(reviews or 0) + float(commands or 0) + builds, 4)


def newest_build_at(session: Session, project_id: int) -> datetime | None:
    """When the repo's newest knowledge row (any status) was created; None without one."""
    return session.scalar(
        select(func.max(ReviewKnowledge.created_at)).where(
            ReviewKnowledge.scope == "project", ReviewKnowledge.project_id == project_id
        )
    )


def first_build_allowed(
    session: Session,
    project_id: int,
    *,
    redis: Any | None,
    queue: Any | None,
    now: datetime | None = None,
) -> bool:
    """Whether a review may build the repo's first knowledge now (docs/specs/19 §3.4). No when
    a row (any status, a failed one too) is younger than an hour, when the repo's build job is
    queued or running, or when the Redis NX key of the hour is already set. Setting that key
    is the claim: the caller that gets True builds."""
    now = now or datetime.now(UTC)
    newest = newest_build_at(session, project_id)
    if newest is not None:
        if newest.tzinfo is None:  # a dialect without time zones (SQLite in unit tests)
            newest = newest.replace(tzinfo=UTC)
        if newest >= now - RECENT_BUILD:
            return False
    if queue is not None and job_active(queue, job_id_for(project_id)):
        return False
    if redis is not None:
        key = FIRST_BUILD_KEY.format(project_id=project_id)
        if not redis.set(key, 1, nx=True, ex=FIRST_BUILD_TTL_S):
            return False
    return True


def _refused(reason: str) -> dict[str, Any]:
    return {"built": False, "reason": reason}


# Why a build did not start, as the live panel says it (keys: the reasons `_refused` returns).
# An untracked repo is left out: the panel never lists repos CodePulse does not track.
REFUSED_LABEL = {
    "external LLM disabled for this repo": "repo không cho gửi mã ra LLM bên ngoài",
    "no connection": "repo chưa gắn kết nối GitLab",
    "monthly LLM budget reached": "đã hết ngân sách LLM của tháng",
}


def _note_refused(
    progress: ProgressSink | None, project_id: int, reason: str, built_by: str
) -> None:
    """A build refused before it starts still ends in the live panel's recent list with why,
    so "Rà soát lại" never ends in silence. `built_by` tells a click from a webhook or a
    first review."""
    if progress is not None and reason in REFUSED_LABEL:
        progress.note_ended(
            progress_id(project_id),
            {
                "kind": "knowledge",
                "project_id": project_id,
                "title": "Rà soát lại dự án",
                "trigger": built_by,
            },
            "skipped",
            REFUSED_LABEL[reason],
        )


def _load_target(session: Session, project_id: int) -> _Target | dict[str, Any]:
    project = session.get(Project, project_id)
    if project is None or not project.tracked:
        return _refused("repo not tracked")
    if project.allow_external_llm is False:
        return _refused("external LLM disabled for this repo")
    if project.connection_id is None:
        return _refused("no connection")
    business = (
        session.get(BusinessProject, project.business_project_id)
        if project.business_project_id
        else None
    )
    return _Target(
        project_id=project.id,
        external_id=project.external_id,
        path=project.path_with_namespace,
        default_branch=project.default_branch or "HEAD",
        connection_id=project.connection_id,
        business_project_id=project.business_project_id,
        business_name=business.name if business else None,
    )


def _active(session: Session, scope: str, column: Any, ident: int) -> ReviewKnowledge | None:
    return session.scalars(
        select(ReviewKnowledge)
        .where(ReviewKnowledge.scope == scope, column == ident, ReviewKnowledge.status == "active")
        .order_by(ReviewKnowledge.version.desc())
    ).first()


def _next_version(session: Session, scope: str, column: Any, ident: int) -> int:
    latest = session.scalar(
        select(func.max(ReviewKnowledge.version)).where(
            ReviewKnowledge.scope == scope, column == ident
        )
    )
    return int(latest or 0) + 1


def _lock_scope(session: Session, scope: str, ident: int) -> None:
    """Serialise writers of one scope for the rest of the transaction (PostgreSQL advisory
    lock), so two builds of the same repo pick different versions and only one stays active.
    Other dialects (none in production) rely on the unique constraints alone."""
    dialect = getattr(getattr(session.bind, "dialect", None), "name", "")
    if dialect != "postgresql":
        return
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"review_knowledge:{scope}:{ident}")))
    )


def store_knowledge(
    session: Session,
    *,
    scope: str,
    project_id: int | None,
    business_project_id: int | None,
    knowledge: Knowledge | None,
    sources: dict[str, Any],
    built_by: str,
    status: str = "active",
    data: dict[str, Any] | None = None,
) -> ReviewKnowledge:
    """A new row of the scope, versioned after the newest one. `active` (the default) makes the
    previous active row superseded; `failed` keeps a build that paid and then broke (`data`
    then carries `meta.cost_usd` / `meta.error` instead of a Knowledge). When a concurrent
    build wins the unique constraints anyway, the winner's active row is returned."""
    column = (
        ReviewKnowledge.project_id if scope == "project" else ReviewKnowledge.business_project_id
    )
    ident = project_id if scope == "project" else business_project_id
    assert ident is not None  # noqa: S101 - caller's contract
    _lock_scope(session, scope, ident)
    previous = _active(session, scope, column, ident)
    if data is None:
        data = knowledge.model_dump(mode="json") if knowledge is not None else {}
    row = ReviewKnowledge(
        scope=scope,
        project_id=project_id if scope == "project" else None,
        business_project_id=business_project_id if scope == "business_project" else None,
        version=_next_version(session, scope, column, ident),
        schema_version=SCHEMA_VERSION,
        data=data,
        sources=sources,
        status=status,
        built_by=built_by,
    )
    try:
        # Supersede and insert inside one savepoint: when the insert loses, the rollback also
        # takes the supersede back, so the winner's row stays the active one.
        with session.begin_nested():
            if status == "active" and previous is not None:
                previous.status = "superseded"
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        log.warning(
            "review knowledge row lost a race; keeping the winner",
            extra={"scope": scope, "ident": ident, "error": str(exc.orig)[:160]},
        )
        if row in session:  # never flush the loser again
            session.expunge(row)
        session.expire_all()
        winner = _active(session, scope, column, ident)
        if winner is None:
            raise
        return winner
    return row


def record_failure(
    session: Session,
    *,
    scope: str,
    project_id: int | None,
    business_project_id: int | None,
    cost_usd: float,
    error: str,
    built_by: str,
) -> ReviewKnowledge:
    """The `failed` row of a build that broke: its cost stays in the month's spend and its
    time keeps the next review from trying again within the hour."""
    return store_knowledge(
        session,
        scope=scope,
        project_id=project_id,
        business_project_id=business_project_id,
        knowledge=None,
        sources={},
        built_by=built_by,
        status="failed",
        data={
            "schema_version": SCHEMA_VERSION,
            "meta": {
                "cost_usd": round(cost_usd, 4),
                "error": error[:400],
                "built_at": datetime.now(UTC).isoformat(),
                "notes": [f"build failed: {error[:200]}"],
            },
        },
    )


def _shared_knowledge(
    session: Session,
    provider: LLMProvider,
    shared: SharedConfig | None,
    target: _Target,
    *,
    max_cost_usd: float,
    built_by: str,
) -> tuple[Knowledge | None, str | None, float]:
    """The dự án's shared knowledge: reused when a row for the same sha exists, else compiled
    now and stored at business_project scope. (knowledge, sha, cost of this call)."""
    if shared is None or shared.docs is None or target.business_project_id is None:
        return None, None, 0.0
    sha = str(shared.info.get("sha") or "")
    if not sha:
        return None, None, 0.0
    with session.begin():
        row = _active(
            session,
            "business_project",
            ReviewKnowledge.business_project_id,
            target.business_project_id,
        )
        if row is not None and str((row.sources or {}).get("shared_sha") or "")[:12] == sha[:12]:
            return Knowledge.model_validate(row.data), sha, 0.0
    ledger = CostLedger()
    shared_kept, shared_left_out = select_sources(shared.files)
    try:
        knowledge, usage = compile_documents(
            provider,
            shared.docs,
            [Doc(f.path, f.text) for f in shared_kept],
            target.business_name or str(shared.info.get("repo") or "shared"),
            max_cost_usd=max_cost_usd,
            on_call=ledger.add,
        )
    except Exception as exc:
        if ledger.calls:  # paid for nothing: the shared row keeps the shared cost
            with session.begin():
                record_failure(
                    session,
                    scope="business_project",
                    project_id=None,
                    business_project_id=target.business_project_id,
                    cost_usd=ledger.total,
                    error=f"{exc.__class__.__name__}: {exc}",
                    built_by=built_by,
                )
        raise
    knowledge.meta.built_at = datetime.now(UTC).isoformat()
    knowledge.meta.sources = {"shared_sha": sha, "shared_ref": shared.info.get("ref")}
    knowledge.omitted = (shared_left_out + knowledge.omitted)[:MAX_OMITTED]
    with session.begin():
        store_knowledge(
            session,
            scope="business_project",
            project_id=None,
            business_project_id=target.business_project_id,
            knowledge=knowledge,
            sources={"shared_sha": sha, "shared_ref": shared.info.get("ref")},
            built_by=built_by,
        )
    return knowledge, sha, usage.cost_usd


def build_knowledge_job(
    project_id: int, *, requested_by: int | None = None, built_by: str = "manual"
) -> dict[str, Any]:
    """Build (or rebuild) the review knowledge of one repo from its default branch."""
    settings = get_settings()
    progress = RedisProgress(progress_redis(str(settings.redis_url)))
    with get_sessionmaker()() as session:
        return run_build(
            session,
            project_id,
            settings,
            requested_by=requested_by,
            built_by=built_by,
            progress=progress,
        )


def run_build(
    session: Session,
    project_id: int,
    settings: Settings,
    *,
    requested_by: int | None = None,
    built_by: str = "manual",
    progress: ProgressSink | None = None,
    provider: LLMProvider | None = None,
    make_client: Any = None,
    agent: AgenticProvider | None = None,
    probe: bool = True,
) -> dict[str, Any]:
    """The build with its collaborators injectable (tests). `agent` runs the code probe
    (docs/specs/19 §3.2 tầng mã nguồn); None (or `probe=False`) keeps the document layer
    only. `session` must not be inside a transaction: the job opens short ones around its
    reads and its save."""
    with session.begin():
        loaded = _load_target(session, project_id)
        if isinstance(loaded, dict):
            _note_refused(progress, project_id, str(loaded["reason"]), built_by)
            return loaded
        target = loaded
        budget = settings.monthly_llm_budget_usd
        spent = month_spend_usd(session)
        if budget > 0 and spent >= budget:
            log.error(
                "monthly LLM budget reached, knowledge build refused",
                extra={"spent_usd": spent, "budget_usd": budget, "project_id": project_id},
            )
            record_failure(
                session,
                scope="project",
                project_id=project_id,
                business_project_id=None,
                cost_usd=0.0,
                error=f"monthly LLM budget reached: {spent:.2f} / {budget:.2f} USD",
                built_by=built_by,
            )
            _note_refused(progress, project_id, "monthly LLM budget reached", built_by)
            return _refused("monthly LLM budget reached")
        connection = session.get(Connection, target.connection_id)
        if connection is None:
            _note_refused(progress, project_id, "no connection", built_by)
            return _refused("no connection")
        business = (
            session.get(BusinessProject, target.business_project_id)
            if target.business_project_id
            else None
        )
        requester = session.get(User, requested_by) if requested_by else None
        requester_name = requester.full_name if requester else None
        tuned = with_overrides(session, settings)
        session.expunge_all()
    provider = provider or build_provider(
        tuned, timeout_s=float(settings.knowledge_compile_timeout_s), max_retries=1
    )
    if agent is None and probe:
        agent = build_agentic_provider(tuned)
    make_client = make_client or client_factory(secret_box())
    pid = progress_id(target.project_id)
    sink: ProgressSink | None = progress
    if sink is not None:
        sink.start(
            pid,
            {
                "kind": "knowledge",
                "project_id": target.project_id,
                "project": target.path,
                "ref": target.default_branch,
                "sha": "",
                "title": "Rà soát lại dự án",
                "author_user_id": None,
                "author": None,
                "requested_by": requester_name,
                "trigger": built_by,
                "round": 1,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
    status: str | None = None
    why: str | None = None
    ledger = CostLedger()

    def failed(reason: str, exc: BaseException) -> dict[str, Any]:
        # The failed row keeps the money in the month's spend and holds the next first-review
        # build off for an hour, whatever the reason.
        error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
        try:
            with session.begin():
                record_failure(
                    session,
                    scope="project",
                    project_id=target.project_id,
                    business_project_id=None,
                    cost_usd=ledger.total,
                    error=error,
                    built_by=built_by,
                )
        except Exception:
            log.exception("knowledge build: failure not recorded", extra={"project_id": project_id})
        return {**_refused(reason), "cost_usd": ledger.total}

    try:
        result = _build(
            session,
            target,
            business,
            connection,
            make_client,
            provider,
            settings,
            built_by=built_by,
            sink=sink,
            agent=agent if probe else None,
            pid=pid,
            ledger=ledger,
        )
        status = "done" if result.get("built") else "skipped"
        why = None if result.get("built") else str(result.get("reason") or "")
        return result
    except (GitLabError, ArchiveTooLarge, CheckoutError) as exc:
        log.warning("knowledge build: repository not read", extra={"error": exc.__class__.__name__})
        status, why = "failed", f"không đọc được repo: {exc.__class__.__name__}: {str(exc)[:160]}"
        return failed(f"repository not read: {exc.__class__.__name__}", exc)
    except (LLMError, ValueError) as exc:
        log.warning("knowledge build: unusable answer", extra={"error": str(exc)[:160]})
        status, why = "failed", f"LLM: {exc.__class__.__name__}: {str(exc)[:160]}"
        return failed("unusable answer", exc)
    except KnowledgeBudgetError as exc:
        log.warning("knowledge build: cost cap", extra={"error": str(exc)[:160]})
        status, why = "failed", f"vượt trần chi phí một lần rà soát: {str(exc)[:120]}"
        return failed("cost cap reached", exc)
    except Exception as exc:
        status, why = "failed", f"{exc.__class__.__name__}: {str(exc)[:160]}"
        failed("error", exc)
        raise
    finally:
        if sink is not None:
            sink.end(pid, status, why)


def _stage(sink: ProgressSink | None, pid: int, stage: str, detail: str = "") -> None:
    if sink is not None:
        sink.stage(pid, stage, detail)


def _build(
    session: Session,
    target: _Target,
    business: BusinessProject | None,
    connection: Connection,
    make_client: Any,
    provider: LLMProvider,
    settings: Settings,
    *,
    built_by: str,
    sink: ProgressSink | None,
    pid: int,
    agent: AgenticProvider | None = None,
    ledger: CostLedger | None = None,
) -> dict[str, Any]:
    ledger = ledger if ledger is not None else CostLedger()
    cap = settings.knowledge_max_cost_usd
    probe_report: dict[str, Any] = {"skipped": "no agentic provider"}
    probe_cost = 0.0
    client: GitLabClient
    with make_client(connection) as client:
        _stage(sink, pid, "docs", "tải repo")
        ref = target.default_branch
        try:
            head = str(client.commit(target.external_id, ref).get("id") or ref)
        except GitLabError:
            head = ref
        tree_sha = claude_tree_sha(client, target.external_id, head)
        with checkout(
            client,
            target.external_id,
            head,
            max_archive_bytes=settings.deep_review_max_archive_mb * 1024 * 1024,
            sensitive_paths=settings.sensitive_paths,
        ) as (root, stats):
            docs = discover(root)
            kit, left_out = select_sources(
                scan(root / ".claude", ".claude", "repo", settings.claude_file_chars)
            )
            extra = [Doc(f.path, f.text) for f in kit]
            shared = load_shared_config(
                client, business, connection.id, sensitive_paths=settings.sensitive_paths
            )
            _stage(sink, pid, "compile", "biên dịch tài liệu")
            shared_knowledge, shared_sha, shared_cost = _shared_knowledge(
                session, provider, shared, target, max_cost_usd=cap, built_by=built_by
            )
            if cap > 0 and shared_cost >= cap:
                raise KnowledgeBudgetError("shared compile used the whole cap")
            repo_knowledge, usage = compile_documents(
                provider,
                docs,
                extra,
                target.path,
                max_cost_usd=(cap - shared_cost) if cap > 0 else None,
                on_call=ledger.add,
            )
            repo_knowledge.omitted = (left_out + repo_knowledge.omitted)[:MAX_OMITTED]
            spent = shared_cost + usage.cost_usd
            if agent is None:
                _stage(sink, pid, "probe", "bỏ qua (không có agent)")
            elif cap > 0 and spent >= cap:
                probe_report = {"skipped": "cost cap reached"}
                _stage(sink, pid, "probe", "bỏ qua (hết trần chi phí)")
            else:
                _stage(sink, pid, "probe", "agent đang thăm dò mã nguồn")
                try:
                    found, call = probe_code(
                        agent,
                        root,
                        repo_knowledge.model_dump(mode="json"),
                        target.path,
                        max_turns=settings.knowledge_max_turns,
                        timeout_s=float(settings.knowledge_timeout_s),
                    )
                    repo_knowledge = Knowledge.model_validate(
                        merge_probe(repo_knowledge.model_dump(mode="json"), found)
                    )
                    ledger.add(call)
                    probe_cost = call.cost_usd
                    probe_report = {
                        "conventions": len(found.get("code_conventions") or []),
                        "modules": len(found.get("critical_modules") or []),
                        "turns": (call.raw or {}).get("num_turns"),
                        "cost_usd": round(call.cost_usd, 4),
                    }
                except (LLMError, ValueError) as exc:
                    probe_report = {"error": exc.__class__.__name__, "detail": str(exc)[:200]}
                    log.warning(
                        "knowledge code probe failed; document layer kept",
                        extra={"project_id": target.project_id, "error": exc.__class__.__name__},
                    )
    knowledge = merge(shared_knowledge, repo_knowledge)
    sources: dict[str, Any] = {
        "default_branch": target.default_branch,
        "default_branch_sha": head,
        "claude_tree_sha": tree_sha,
        "shared_sha": shared_sha,
        "shared_status": shared.info.get("status") if shared else None,
    }
    knowledge.meta.sources = sources
    knowledge.meta.built_at = datetime.now(UTC).isoformat()
    knowledge.meta.model = usage.model
    # This row's own money only: the shared compile is on the business_project row, so the
    # month's spend counts it once.
    knowledge.meta.cost_usd = round(usage.cost_usd + probe_cost, 4)
    knowledge.meta.files_read = len(docs.instructions) + len(extra) + (1 if docs.readme else 0)
    knowledge.meta.code_probe = probe_report
    if stats.skipped:
        knowledge.meta.notes = [f"checkout skipped: {stats.skipped}"]
    _stage(sink, pid, "save", "lưu")
    with session.begin():
        row = store_knowledge(
            session,
            scope="project",
            project_id=target.project_id,
            business_project_id=None,
            knowledge=knowledge,
            sources=sources,
            built_by=built_by,
        )
        version, row_id = row.version, row.id
    log.info(
        "review knowledge built",
        extra={
            "project_id": target.project_id,
            "knowledge_id": row_id,
            "version": version,
            "cost_usd": knowledge.meta.cost_usd,
            "calls": knowledge.meta.calls,
        },
    )
    return {
        "built": True,
        "knowledge_id": row_id,
        "version": version,
        "cost_usd": knowledge.meta.cost_usd,
        "shared_cost_usd": round(shared_cost, 4),
        "shared": shared_sha is not None,
        **knowledge.counts(),
    }
