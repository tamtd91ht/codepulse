"""Sync a project: collect -> extract -> convert (backfill, then incremental).

Two ways to run (docs/specs/04 "Giao dịch khi đồng bộ", 2026-11-19):
- in the caller's transaction (`staged=False`, tests, CLI): everything in one unit of work;
- staged (`staged=True`, the RQ job and a review's sync): the repo's lock is a session-level
  advisory lock on its own connection, the GitLab calls run with no transaction open, and the
  database work runs in short transactions: a read before, one write at the end (raw rows,
  convert, identity, last_synced_at). What is stored is the same either way: the write is
  all-or-nothing, so last_synced_at still only advances when everything succeeds.
"""

import logging
import secrets
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import Connection as SqlConnection
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.collector.base import CollectResult
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.collector.gitlab.collect import (
    EP_COMMIT,
    EP_MR_COMMITS,
    GitLabCollector,
    read_known,
    window_start,
)
from app.collector.gitlab.convert import Converter, raw_rows_query
from app.collector.gitlab.extract import extract
from app.config import get_settings
from app.crypto import SecretBox, secret_box
from app.db import get_redis, get_sessionmaker
from app.dpi.task_source import task_source
from app.identity.attribution import Attribution, attribute_reviews, rebuild_after_commit
from app.identity.autolink import PublicEmailLookup, autolink, is_bot
from app.models import Account, Connection, Deployment, Incident, Project, RawGitlab

log = logging.getLogger(__name__)

SYNC_JOB_TIMEOUT = 2 * 60 * 60  # a 90-day backfill of a busy project can take a while
WEBHOOK_JOB_TIMEOUT = 5 * 60
ADVISORY_LOCK_NAMESPACE = 0x5C  # first key of pg_try_advisory_xact_lock(ns, project_id)

WEBHOOK_PATH = "/api/v1/webhooks/gitlab"

ClientFactory = Callable[[Connection], GitLabClient]


@dataclass
class SyncResult:
    project_id: int
    skipped: bool
    window_start: datetime | None = None
    counts: dict[str, int] | None = None
    # Reviews that got their person this sync, and the stored periods to rebuild after commit.
    attribution: Attribution = field(default_factory=Attribution)


class Enqueuer(Protocol):
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> Any: ...


def client_factory(box: SecretBox) -> ClientFactory:
    def make(connection: Connection) -> GitLabClient:
        return GitLabClient(connection.base_url, box.decrypt(connection.token_encrypted))

    return make


def register_webhook(
    make_client: ClientFactory,
    box: SecretBox,
    connection: Connection,
    project: Project,
    base_url: str,
) -> tuple[str, bool]:
    """Add the CodePulse hook to a GitLab project; returns (url, created). Idempotent.

    GitLab never returns a hook's secret, so an existing hook is re-sent the current one: that
    repairs hooks left behind by a secret rotation or by an earlier run that failed to commit.
    """
    if not connection.webhook_secret_encrypted:
        connection.webhook_secret_encrypted = box.encrypt(secrets.token_urlsafe(32))
    url = base_url.rstrip("/") + WEBHOOK_PATH
    secret = box.decrypt(connection.webhook_secret_encrypted)
    with make_client(connection) as client:
        hooks = client.project_hooks(project.external_id)
        existing = next((h for h in hooks if h.get("url") == url), None)
        if existing is not None:
            client.update_project_hook(project.external_id, int(existing["id"]), url, secret)
            return url, False
        client.add_project_hook(project.external_id, url, secret)
    return url, True


def public_email_lookup(client: GitLabClient) -> PublicEmailLookup:
    def lookup(gitlab_user_id: int) -> str | None:
        try:
            return str(client.user(gitlab_user_id).get("public_email") or "")
        except GitLabError:
            return None  # a deleted or hidden user must not fail the sync

    return lookup


@contextmanager
def repo_lock(session: Session, project_id: int) -> Iterator[bool]:
    """The repo's sync lock held across transactions: a session-level advisory lock (same key as
    the transaction-level one of an in-transaction sync, `fetch_commit` and the Insights
    reconvert, which it excludes) on a connection of its own in autocommit, so it holds no
    transaction open. Released at the end, or by Postgres if the worker dies."""
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, SqlConnection) else bind
    if not isinstance(engine, Engine):
        raise TypeError("the session is not bound to an engine")
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        yield bool(
            conn.scalar(select(func.pg_try_advisory_lock(ADVISORY_LOCK_NAMESPACE, project_id)))
        )
    finally:
        # Closed for good, never back to the pool: Postgres drops a session lock with its
        # connection, so the lock goes whatever happened (an unlock interrupted, a signal).
        with suppress(BaseException):
            conn.invalidate()
        with suppress(BaseException):
            conn.close()


def _gitlab_users(payload: Any, out: set[int]) -> None:
    """The ids of the GitLab user objects (`{"id", "username", ...}`) anywhere in a payload."""
    if isinstance(payload, dict):
        if "username" in payload and isinstance(payload.get("id"), int):
            out.add(int(payload["id"]))
        for value in payload.values():
            _gitlab_users(value, out)
    elif isinstance(payload, list):
        for value in payload:
            _gitlab_users(value, out)


def _commit_emails(rows: Sequence[RawGitlab]) -> dict[str, str | None]:
    """The author emails of the commits in this run's rows (what becomes git_email accounts)."""
    out: dict[str, str | None] = {}
    for row in rows:
        if row.endpoint not in (EP_COMMIT, EP_MR_COMMITS):
            continue
        items = row.payload if isinstance(row.payload, list) else [row.payload]
        for item in items:
            if isinstance(item, dict):
                email = str(item.get("author_email") or "").strip().lower()
                if email:
                    out.setdefault(email, item.get("author_name"))
    return out


def _needs_linked(session: Session, rows: Sequence[RawGitlab], domains: Sequence[str]) -> bool:
    """Whether `autolink` may read the public email of the linked GitLab users: some git email
    (stored and unlinked, or new in this run) the rules without a lookup do not settle. When in
    doubt, yes: asking ahead more than needed costs calls, never a different link."""
    from app.identity.autolink import NOREPLY
    from app.models import User

    users = {e.lower() for e in session.scalars(select(User.primary_email)) if e}
    linked = {
        key: (name or "").lower()
        for key, name in session.execute(
            select(Account.external_key, Account.display_name).where(
                Account.kind == "gitlab_user", Account.user_id.is_not(None)
            )
        ).tuples()
    }
    names = set(linked.values())
    wanted = {d.lower() for d in domains}
    stored = {
        key: (name, user_id)
        for key, name, user_id in session.execute(
            select(Account.external_key, Account.display_name, Account.user_id).where(
                Account.kind == "git_email"
            )
        ).tuples()
    }
    candidates = [(k, n) for k, (n, uid) in stored.items() if uid is None]
    candidates += [(e, n) for e, n in _commit_emails(rows).items() if e not in stored]
    for email, name in candidates:
        if is_bot(Account(kind="git_email", external_key=email, display_name=name)):
            continue
        if email in users:
            continue
        if (m := NOREPLY.match(email)) and m["id"] in linked:
            continue
        local, _, domain = email.partition("@")
        if domain in wanted and local in names:
            continue
        return True
    return False


def _lookup_ids(
    session: Session, rows: Sequence[RawGitlab] = (), domains: Sequence[str] = ()
) -> list[int]:
    """The GitLab users `autolink` will ask the public email of: the GitLab accounts not yet
    linked, the ones this run's rows bring in (they are new, so unlinked) and, while some git
    email stays unlinked by the other rules, every linked one (autolink reads theirs to match
    that email). Read before the write so the calls run with no transaction open."""
    seen: set[int] = set()
    for row in rows:
        _gitlab_users(row.payload, seen)
    stored = {
        key
        for key in session.scalars(
            select(Account.external_key).where(Account.kind == "gitlab_user")
        )
    }
    new = {uid for uid in seen if str(uid) not in stored}
    unlinked = [a for a in session.scalars(select(Account).where(Account.user_id.is_(None)))]
    ids = new | {
        int(a.external_key)
        for a in unlinked
        if a.kind == "gitlab_user" and not is_bot(a) and a.external_key.isdigit()
    }
    if _needs_linked(session, rows, domains):
        ids |= {
            int(key)
            for key in session.scalars(
                select(Account.external_key).where(
                    Account.kind == "gitlab_user", Account.user_id.is_not(None)
                )
            )
            if key.isdigit()
        }
    return sorted(ids)


def prefetched_lookup(client: GitLabClient, ids: Sequence[int]) -> PublicEmailLookup:
    """`public_email_lookup` asked ahead for `ids`; a user not asked (an account the write just
    created) is asked then."""
    live = public_email_lookup(client)
    known: dict[int, str | None] = {}
    for uid in ids:
        try:
            known[uid] = live(uid)
        except Exception:  # asked again during the write, as autolink always did
            log.info("public email not prefetched", extra={"gitlab_user_id": uid})

    def lookup(gitlab_user_id: int) -> str | None:
        if gitlab_user_id in known:
            return known[gitlab_user_id]
        return live(gitlab_user_id)

    return lookup


def sync_project(
    session: Session,
    project: Project,
    make_client: ClientFactory,
    *,
    backfill_days: int,
    ignore_paths: list[str],
    allowed_email_domains: Sequence[str] = (),
    now: datetime | None = None,
    full: bool = False,
    attribute_tz: ZoneInfo | None = None,
    incident_labels: Sequence[str] = ("incident",),
    staged: bool = False,
) -> SyncResult:
    """last_synced_at only advances if everything succeeds. `staged=False`: in the caller's
    transaction. `staged=True`: commits its own short transactions (see the module doc); the
    caller's open transaction, if any, is committed first. `full`: collect the whole backfill
    window again (e.g. after a collector fix), not only what changed since the last sync.
    `attribute_tz`: afterwards give every review still without a person the person its author
    account has now (app/identity/attribution.py); the caller queues the metrics rebuild once
    committed."""
    if not project.tracked:
        log.info("sync skipped: repo not tracked", extra={"project_id": project.id})
        return SyncResult(project.id, skipped=True)
    options = _Options(
        backfill_days=backfill_days,
        ignore_paths=ignore_paths,
        allowed_email_domains=allowed_email_domains,
        now=now,
        full=full,
        attribute_tz=attribute_tz,
        incident_labels=incident_labels,
    )
    if staged:
        # Objects read in one short transaction stay loaded across our commits: reading an
        # expired attribute between them would open a transaction again during the GitLab calls.
        expire = session.expire_on_commit
        session.expire_on_commit = False
        try:
            session.commit()  # the caller's reads: nothing stays open through the GitLab calls
            with repo_lock(session, project.id) as locked:
                if not locked:
                    log.info("sync already running, skipped", extra={"project_id": project.id})
                    return SyncResult(project.id, skipped=True)
                try:
                    return _sync_staged(session, project, make_client, options)
                except BaseException:
                    session.rollback()
                    raise
        finally:
            session.expire_on_commit = expire
    acquired = session.scalar(
        select(func.pg_try_advisory_xact_lock(ADVISORY_LOCK_NAMESPACE, project.id))
    )
    if not acquired:
        log.info("sync already running, skipped", extra={"project_id": project.id})
        return SyncResult(project.id, skipped=True)
    return _sync_in_transaction(session, project, make_client, options)


@dataclass
class _Options:
    backfill_days: int
    ignore_paths: list[str]
    allowed_email_domains: Sequence[str]
    now: datetime | None
    full: bool
    attribute_tz: ZoneInfo | None
    incident_labels: Sequence[str]


def _sync_staged(
    session: Session, project: Project, make_client: ClientFactory, o: _Options
) -> SyncResult:
    # 1. Read (short): the window and what the collect step needs from the domain layer.
    session.expire_all()  # as of now, under the lock (the caller may hold older copies)
    project = session.get(Project, project.id) or project
    connection = session.get(Connection, project.connection_id)
    if connection is None:
        raise ValueError(f"project {project.id} has no connection")
    started = o.now or datetime.now(UTC)
    since = (
        started - timedelta(days=o.backfill_days)
        if o.full
        else window_start(project, started, o.backfill_days)
    )
    has_deployments = session.scalar(
        select(func.count()).select_from(Deployment).where(Deployment.project_id == project.id)
    )
    has_incidents = session.scalar(
        select(func.count()).select_from(Incident).where(Incident.project_id == project.id)
    )
    issues = task_source(session, get_settings()) == "gitlab"
    known = read_known(session, project)
    session.commit()
    backfill_start = started - timedelta(days=o.backfill_days)

    with make_client(connection) as client:
        # 2. GitLab, no transaction open.
        rows: list[RawGitlab] = []
        collected = GitLabCollector(client, clock=lambda: started).fetch(
            project,
            since,
            known,
            rows,
            deployments_since=None if has_deployments else backfill_start,
            incidents_since=None if has_incidents else backfill_start,
            incident_labels=o.incident_labels,
            issues=issues,
        )
        ids = _lookup_ids(session, rows, o.allowed_email_domains)
        session.commit()
        lookup = prefetched_lookup(client, ids)

        # 3. Write (one transaction): exactly what the in-transaction sync writes, from rows
        # read in it (an admin may have linked an account during the GitLab calls).
        session.expire_all()
        session.add_all(rows)
        session.flush()
        del rows
        result = _convert_and_finish(session, project, collected, started, since, lookup, o)
    session.commit()
    return result


def _sync_in_transaction(
    session: Session, project: Project, make_client: ClientFactory, o: _Options
) -> SyncResult:
    backfill_days = o.backfill_days

    connection = session.get(Connection, project.connection_id)
    if connection is None:
        raise ValueError(f"project {project.id} has no connection")
    started = o.now or datetime.now(UTC)
    since = (
        started - timedelta(days=backfill_days)
        if o.full
        else window_start(project, started, backfill_days)
    )

    with make_client(connection) as client:
        # Repos synced before deployments were collected get their history once (9.8).
        has_deployments = session.scalar(
            select(func.count()).select_from(Deployment).where(Deployment.project_id == project.id)
        )
        deployments_since = None if has_deployments else started - timedelta(days=backfill_days)
        has_incidents = session.scalar(
            select(func.count()).select_from(Incident).where(Incident.project_id == project.id)
        )
        incidents_since = None if has_incidents else started - timedelta(days=backfill_days)
        collected = GitLabCollector(client, clock=lambda: started).collect(
            session,
            project,
            since,
            deployments_since=deployments_since,
            incidents_since=incidents_since,
            incident_labels=o.incident_labels,
            issues=task_source(session, get_settings()) == "gitlab",
        )
        return _convert_and_finish(
            session, project, collected, started, since, public_email_lookup(client), o
        )


def _convert_and_finish(
    session: Session,
    project: Project,
    collected: CollectResult,
    started: datetime,
    since: datetime | None,
    lookup: PublicEmailLookup,
    o: _Options,
) -> SyncResult:
    """Raw rows of this run (and any newer) -> domain layer, identities, last_synced_at."""
    rows = session.scalars(raw_rows_query(project, fetched_since=collected.started_at)).all()
    converted = Converter(session, project, o.ignore_paths).run(extract(rows))
    linked = autolink(session, o.allowed_email_domains, public_email=lookup)

    project.last_synced_at = started
    session.flush()
    attribution = Attribution()
    if o.attribute_tz is not None and linked.account_ids:
        # Only the accounts this sync tied: one an admin linked by hand with the option off
        # keeps its earlier reviews without a person.
        attribution = attribute_reviews(session, o.attribute_tz, linked.account_ids)
        if attribution.reviews:
            audit(
                session,
                None,
                "identity.attribute",
                f"project:{project.id}",
                accounts=sorted(set(linked.account_ids)),
                reviews_attributed=attribution.reviews,
            )
    counts = {**collected.counts, **{f"domain.{k}": v for k, v in converted.counts.items()}}
    counts["identity.linked"] = sum(linked.linked.values())
    counts["identity.reviews_attributed"] = attribution.reviews
    log.info("project synced", extra={"project_id": project.id, "counts": counts})
    return SyncResult(
        project.id, skipped=False, window_start=since, counts=counts, attribution=attribution
    )


def fetch_commit(
    session: Session,
    project: Project,
    make_client: ClientFactory,
    sha: str,
    *,
    ignore_paths: list[str],
    allowed_email_domains: Sequence[str] = (),
) -> bool | None:
    """Bring one commit into the domain layer without a full sync (a review asked for a commit
    the last sync has not seen). True: stored; False: GitLab does not know it; None: a sync of
    this project is running (try again later). last_synced_at is not touched."""
    locked = session.scalar(
        select(func.pg_try_advisory_xact_lock(ADVISORY_LOCK_NAMESPACE, project.id))
    )
    if not locked:
        return None
    connection = session.get(Connection, project.connection_id)
    if connection is None:
        raise ValueError(f"project {project.id} has no connection")
    with make_client(connection) as client:
        try:
            rows = GitLabCollector(client).collect_commit(session, project, sha)
        except GitLabError as exc:
            if exc.status_code == 404:
                return False
            raise
        Converter(session, project, ignore_paths).run(extract(rows))
        autolink(session, allowed_email_domains, public_email=public_email_lookup(client))
    log.info("commit fetched for review", extra={"project_id": project.id, "sha": sha[:12]})
    return True


def sync_project_job(project_id: int, full: bool = False) -> dict[str, object]:
    """RQ entry point."""
    settings = get_settings()
    with get_sessionmaker()() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        # Staged: the GitLab calls (minutes on a busy repo or a full backfill) hold no
        # transaction open; the write is still all-or-nothing.
        result = sync_project(
            session,
            project,
            client_factory(secret_box()),
            backfill_days=settings.backfill_days,
            ignore_paths=settings.ignore_paths,
            allowed_email_domains=settings.allowed_email_domains,
            full=full,
            attribute_tz=ZoneInfo(settings.report_tz),
            incident_labels=settings.incident_labels,
            staged=True,
        )
    if result.attribution.periods:
        from rq import Queue

        rebuild_after_commit(Queue("default", connection=get_redis()), result.attribution)
    return {"project_id": result.project_id, "skipped": result.skipped, "counts": result.counts}


def register_webhook_job(project_id: int, base_url: str) -> dict[str, object]:
    """RQ entry point (group import): add the CodePulse hook to one project."""
    box = secret_box()
    with get_sessionmaker()() as session, session.begin():
        project = session.get(Project, project_id)
        if project is None or project.connection_id is None:
            raise ValueError(f"project {project_id} not found or has no connection")
        connection = session.get(Connection, project.connection_id)
        if connection is None:
            raise ValueError(f"connection {project.connection_id} not found")
        url, created = register_webhook(client_factory(box), box, connection, project, base_url)
    log.info("webhook registered", extra={"project_id": project_id, "created": created})
    return {"project_id": project_id, "url": url, "created": created}


def enqueue_syncs(session: Session, queue: Enqueuer) -> list[int]:
    """Queue one sync job per project; a fixed job id keeps a slow sync from piling up."""
    project_ids = list(
        session.scalars(select(Project.id).where(Project.tracked).order_by(Project.id))
    )
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
