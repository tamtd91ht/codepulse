"""Jira -> `issues` by JQL polling (docs/specs/27 §3, §7). Read only on Jira's side.

Per registered, enabled project: the issues updated since its last whole sync (plus 5 minutes of
overlap; the first time JIRA_BACKFILL_DAYS back), upserted by id, their assignees as
`jira_user` accounts (linked to a person by email), production defects kept as the GitLab
convert keeps them, then the links to MRs naming their keys. `synced_until` only moves once the
whole project went through; a failure is kept in `last_error` and the next run starts again.
One project's failure does not stop the others; each project is committed on its own, under a
per-project advisory lock (a second sync of a busy project is skipped). Afterwards every
jira_user account still without a person is retried with its stored email and name.

A refused login (JiraAuthError) stops the whole run at once: every project left keeps the error,
and the scheduled syncs wait (`record_auth_failure`, checked by the tick and again by a
scheduled `sync_all`) until the credentials change, a test passes or "Đồng bộ ngay" (manual)
succeeds. Jira Server locks an account behind a CAPTCHA after a few
failed logins, so a wrong password must not be tried again and again.
"""

import logging
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.identity.autolink import BOT_PATTERNS, is_bot
from app.jira.client import JiraAuthError, JiraClient, JiraError
from app.jira.commit_links import link_issue_commits
from app.jira.links import link_issues
from app.jira.mapping import FieldIds, JiraUser, map_issue, resolve_fields, search_fields
from app.jira.scopes import recompute, route, rules_of
from app.jira.settings import (
    JiraConnection,
    JiraMapping,
    clear_auth_failure,
    connection,
    login_paused,
    mapping,
    record_auth_failure,
)
from app.models import Account, Issue, JiraProject, ProdDefect, User

log = logging.getLogger(__name__)

OVERLAP_MIN = 5
JOB_ID = "jira-sync"
JOB_TIMEOUT = 60 * 60
DUE_KEY = "jira:sync:due"  # set for one interval after the scheduler queued a sync
LOCK_NAMESPACE = 0x7A  # first key of pg_try_advisory_xact_lock(ns, jira_projects.id)


class JiraSource(Protocol):
    def fields(self) -> list[dict[str, Any]]: ...

    def search_all(self, jql: str, fields: Sequence[str]) -> Iterator[dict[str, Any]]: ...


def window_minutes(
    project: JiraProject, now: datetime, backfill_days: int, *, full: bool = False
) -> int:
    """§3: minutes since the last whole sync plus the overlap; never synced, or `full` (§16:
    "Đồng bộ lại từ đầu", components to fetch): the backfill."""
    if full or project.synced_until is None:
        return backfill_days * 1440
    elapsed = max((now - project.synced_until).total_seconds(), 0.0)
    return math.ceil(elapsed / 60) + OVERLAP_MIN


def jql(project: JiraProject, minutes: int) -> str:
    # Relative time: no dependence on the service account's time zone. Newest first: an issue
    # updated while the pages are read moves to the front, so later pages only repeat one
    # (harmless, upsert by id) instead of skipping one as ASC + startAt would.
    return (
        f'project = "{project.key}" AND issuetype != Epic AND updated >= -{minutes}m '
        "ORDER BY updated DESC"
    )


@dataclass
class People:
    """jira_user accounts written by one sync, and how to find their person (§7)."""

    session: Session
    domains: list[str]

    def __post_init__(self) -> None:
        self._ids: dict[str, int] = {}
        self._users: dict[str, int] | None = None

    def _by_email(self) -> dict[str, int]:
        if self._users is None:
            self._users = {
                email.lower(): uid
                for uid, email in self.session.execute(
                    select(User.id, User.primary_email).where(User.primary_email.is_not(None))
                )
                if email
            }
        return self._users

    def account(self, user: JiraUser) -> int:
        if user.key in self._ids:
            return self._ids[user.key]
        # The email and name are kept, so an account not linked now is retried later
        # (`relink_unlinked`) without waiting for its issues to be updated again.
        stmt = insert(Account).values(
            kind="jira_user",
            external_key=user.key,
            display_name=user.display_name or user.name,
            email=user.email,
            login=user.name,
        )
        account_id: int = self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=["kind", "external_key"],
                set_={
                    "display_name": func.coalesce(stmt.excluded.display_name, Account.display_name),
                    "email": func.coalesce(stmt.excluded.email, Account.email),
                    "login": func.coalesce(stmt.excluded.login, Account.login),
                    "updated_at": func.now(),
                },
            ).returning(Account.id)
        ).scalar_one()
        self._ids[user.key] = account_id
        account = self.session.get(Account, account_id)
        if account is not None:
            self.link(account, user.email, user.name, bots=True)
        return account_id

    def link(
        self, account: Account, email: str | None, name: str | None, *, bots: bool = False
    ) -> bool:
        """Email, else <name>@<allowed domain>; with `bots`, a bot gets an inactive person (as
        autolink does). An existing link is never changed. True when the account got one."""
        if account.user_id is not None:
            return False
        by_name = bool(name) and any(p.search(name or "") for p in BOT_PATTERNS)
        if bots and (is_bot(account) or by_name):
            bot = User(full_name=account.display_name or account.external_key, active=False)
            self.session.add(bot)
            self.session.flush()
            account.user_id = bot.id
            return True
        users = self._by_email()
        candidates = [email] if email else []
        if name:
            candidates += [f"{name}@{domain}" for domain in self.domains]
        for candidate in candidates:
            if (uid := users.get(candidate.lower())) is not None:
                account.user_id = uid
                return True
        return False


def relink_unlinked(session: Session, domains: Sequence[str]) -> int:
    """§7: every jira_user account still without a person tries again with its stored email
    and name (a person added since, or an email corrected, links it). The caller commits.
    Returns the accounts linked."""
    people = People(session, list(domains))
    linked = 0
    for account in session.scalars(
        select(Account).where(
            Account.kind == "jira_user",
            Account.user_id.is_(None),
            or_(Account.email.is_not(None), Account.login.is_not(None)),
        )
    ):
        linked += people.link(account, account.email, account.login)
    session.flush()
    return linked


def _upsert_issue(session: Session, row: dict[str, Any]) -> None:
    stmt = insert(Issue).values(**row)
    updates: dict[str, Any] = {k: stmt.excluded[k] for k in row if k != "id"}
    updates["updated_at"] = func.now()
    session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=updates))


def _defect(session: Session, issue_id: int, severity: str | None) -> None:
    """As the GitLab convert does (app/collector/gitlab/convert.py `_issues`)."""
    if not severity:
        # No longer a production bug: an unconfirmed defect goes, a confirmed one is
        # withdrawn (out of B2, kept for the record).
        session.execute(
            delete(ProdDefect).where(
                ProdDefect.issue_id == issue_id, ProdDefect.rca_status == "pending"
            )
        )
        session.execute(
            update(ProdDefect)
            .where(ProdDefect.issue_id == issue_id, ProdDefect.rca_status != "pending")
            .values(rca_status="withdrawn")
        )
        return
    stmt = insert(ProdDefect).values(issue_id=issue_id, severity=severity)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["issue_id"],
            set_={"severity": stmt.excluded.severity, "updated_at": func.now()},
        )
    )


def sync_project(
    session: Session,
    client: JiraSource,
    project: JiraProject,
    settings: Settings,
    now: datetime,
    *,
    rules: JiraMapping | None = None,
    ids: FieldIds | None = None,
    conn: JiraConnection | None = None,
    full: bool = False,
) -> int | None:
    """Fetch and store one project's updated issues; the caller commits. Returns the issues
    written, None when another sync of the project holds its lock (skipped, nothing changed).
    Raises on any failure (nothing of the project's marks then moves). `full`: the backfill
    window whatever `synced_until` says (a job argument, so a sync running meanwhile and
    setting `synced_until` cannot cancel it)."""
    # Two syncs of a project (the scheduler's and "Đồng bộ ngay") never run side by side: the
    # lock is held until the caller's commit or rollback.
    locked = session.scalar(select(func.pg_try_advisory_xact_lock(LOCK_NAMESPACE, project.id)))
    if not locked:
        log.info("jira project sync already running, skipped", extra={"jira_project": project.key})
        return None
    conn = conn if conn is not None else connection(session, settings)
    rules = rules if rules is not None else mapping(session)
    ids = ids if ids is not None else resolve_fields(client.fields(), rules)
    people = People(session, list(settings.allowed_email_domains))
    scopes = rules_of(session, project.id)  # the dự án each issue counts for (§15)
    query = jql(project, window_minutes(project, now, conn.backfill_days, full=full))
    written: list[int] = []
    for raw in client.search_all(query, search_fields(ids)):
        mapped = map_issue(raw, rules, ids, conn.base_url, project.id)
        row = dict(mapped.row)
        row["assignee_account_ids"] = [people.account(mapped.assignee)] if mapped.assignee else []
        row["business_project_ids"] = route(scopes, row.get("components"), row.get("labels"))
        _upsert_issue(session, row)
        _defect(session, int(row["id"]), row["prod_severity"])
        written.append(int(row["id"]))
    link_issues(session, written)
    link_issue_commits(session, written)
    if rules_of(session, project.id) != scopes:
        # The scopes were saved while this ran (§15): what was written with the old ones is
        # routed again, so nothing keeps a stale dự án until the next scope change.
        recompute(session, project.id)
    project.synced_until = now
    project.last_synced_at = now
    project.last_error = None
    project.issues_count = int(
        session.scalar(select(func.count()).where(Issue.jira_project_id == project.id)) or 0
    )
    session.flush()
    return len(written)


def _error_text(exc: Exception) -> str:
    """Jira's own message, else only the exception's class: its text may quote request data
    (a UnicodeEncodeError would echo the characters of a header, i.e. the secret)."""
    if isinstance(exc, JiraError):
        return exc.message[:500]
    return exc.__class__.__name__


def sync_all(
    session: Session,
    settings: Settings,
    client: JiraSource | None = None,
    *,
    manual: bool = False,
    keys: Sequence[str] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """Every enabled project, each committed on its own. No-op unless Jira is configured (the
    connection in effect: saved on the web, else JIRA_*) and some project is enabled (whatever
    the task source: data is ready before a switch). A scheduled run (not `manual`, i.e. not
    "Đồng bộ ngay") is skipped while the last login was refused with these credentials.
    `keys`: only these projects (enabled ones), as "Đồng bộ" on one project's row (§16)."""
    conn = connection(session, settings)
    if not conn.configured:
        return {"skipped": "not_configured"}
    if not manual:
        paused = login_paused(session, conn)
        session.commit()  # a marker of other credentials dropped
        if paused:
            return {"skipped": "paused"}
    query = select(JiraProject.key).where(JiraProject.enabled.is_(True))
    if keys is not None:
        query = query.where(JiraProject.key.in_(list(keys)))
    todo = list(session.scalars(query.order_by(JiraProject.key)))
    if not todo:
        return {"skipped": "no_project"}
    own = client is None
    if client is not None:
        source: JiraSource = client
    else:
        try:
            source = JiraClient.from_connection(conn)
        except Exception as exc:  # e.g. the CA saved on the web: kept on every project
            return _all_failed(session, todo, _error_text(exc))
    rules = mapping(session)
    ids: FieldIds | None = None
    out: dict[str, Any] = {"projects": {}}
    refused: str | None = None
    try:
        for index, key in enumerate(todo):
            project = session.scalars(select(JiraProject).where(JiraProject.key == key)).one()
            now = datetime.now(UTC)
            try:
                if ids is None:
                    ids = resolve_fields(source.fields(), rules)
                count = sync_project(
                    session,
                    source,
                    project,
                    settings,
                    now,
                    rules=rules,
                    ids=ids,
                    conn=conn,
                    full=full,
                )
                session.commit()
                if count is None:  # another sync of it is running: not an error
                    out["projects"][key] = {"skipped": "busy"}
                    continue
                out["projects"][key] = {"issues": count}
                log.info("jira project synced", extra={"jira_project": key, "issues": count})
            except JiraAuthError as exc:  # the login itself was refused: stop, try no more
                session.rollback()
                refused = _error_text(exc)
                log.warning("jira login refused, sync stopped", extra={"jira_project": key})
                _all_failed(session, todo[index:], refused, out=out)
                record_auth_failure(session, refused, conn)
                session.commit()
                out["auth_failed"] = True
                break
            except Exception as exc:  # one project's failure must not stop the others
                session.rollback()
                error = _error_text(exc)
                log.warning("jira project sync failed", extra={"jira_project": key, "error": error})
                failed = session.scalars(select(JiraProject).where(JiraProject.key == key)).one()
                failed.last_error = error
                failed.last_synced_at = now
                session.commit()
                out["projects"][key] = {"error": error}
    finally:
        if own and isinstance(source, JiraClient):
            source.close()
    if refused is not None:
        return out
    # Logged in again (Jira answered at least once): the scheduled syncs resume. A run where
    # every call failed (network, 5xx) or every project was busy proves nothing.
    if ids is not None and clear_auth_failure(session):
        session.commit()
    try:
        out["relinked"] = relink_unlinked(session, settings.allowed_email_domains)
        session.commit()
    except Exception:  # the issues are in; a link retry failing waits for the next run
        session.rollback()
        log.exception("jira account relink failed")
    return out


def _all_failed(
    session: Session, keys: list[str], error: str, *, out: dict[str, Any] | None = None
) -> dict[str, Any]:
    """No client could be built, or the login was refused: these projects keep the error,
    nothing else moves."""
    if out is None:
        log.warning("jira client unavailable", extra={"error": error})
    now = datetime.now(UTC)
    for project in session.scalars(select(JiraProject).where(JiraProject.key.in_(keys))):
        project.last_error = error
        project.last_synced_at = now
    session.commit()
    result = out if out is not None else {"projects": {}}
    result["projects"].update({key: {"error": error} for key in keys})
    return result


def sync_all_job(
    manual: bool = False, keys: list[str] | None = None, full: bool = False
) -> dict[str, Any]:
    """RQ entry point (the scheduler's tick once per sync interval, and "Đồng bộ ngay" with
    `manual`: it runs even while a refused login pauses the schedule; `keys`: one project's
    row)."""
    with get_sessionmaker()() as session:
        return sync_all(session, get_settings(), manual=manual, keys=keys, full=full)


class DueStore(Protocol):
    """The bit of Redis the tick uses."""

    def set(self, name: str, value: Any, *, ex: int | None = None, nx: bool = False) -> Any: ...

    def ttl(self, name: str) -> Any: ...

    def expire(self, name: str, time: int) -> Any: ...

    def delete(self, *names: str) -> Any: ...


class Enqueue(Protocol):
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> Any: ...


def tick(session: Session, settings: Settings, redis: DueStore, queue: Enqueue) -> str:
    """The scheduler's 1-minute tick (§3): the connection is read anew each time (it can change
    on the web), and `sync_all_job` is queued once per interval: `SET jira:sync:due NX EX`, so
    the interval holds across scheduler restarts. A shorter interval saved meanwhile cuts the
    current wait down to it."""
    conn = connection(session, settings)
    if not conn.configured:
        return "not configured"
    paused = login_paused(session, conn)
    session.commit()  # a marker of other credentials (e.g. JIRA_TOKEN fixed) is dropped
    if paused:
        # The last sync's login was refused: trying again on schedule would lock the account
        # (CAPTCHA). Admin > Jira says so; a credentials change or "Đồng bộ ngay" resumes.
        return "paused: login refused"
    seconds = conn.interval_min * 60
    if not redis.set(DUE_KEY, "1", ex=seconds, nx=True):
        left = redis.ttl(DUE_KEY)
        if isinstance(left, int) and left > seconds:
            redis.expire(DUE_KEY, seconds)
        return "not due"
    try:
        queue.enqueue("app.jira.sync.sync_all_job", job_id=JOB_ID, job_timeout=JOB_TIMEOUT)
    except Exception:
        redis.delete(DUE_KEY)  # not queued: the next tick tries again
        raise
    return "queued"


def tick_job() -> str:
    """Scheduler entry point (app/scheduler): cheap, runs in the scheduler itself."""
    from rq import Queue

    from app.db import get_redis

    redis = get_redis()
    with get_sessionmaker()() as session:
        return tick(session, get_settings(), redis, Queue("default", connection=redis))
