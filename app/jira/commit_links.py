"""Jira issue <-> commit links by the keys in the commit message (docs/specs/27 §19, user
2026-11-09).

The standard (Hướng dẫn > Tích hợp Jira):

- subject in Conventional Commits with the key at the end: `fix(export): căn đúng cột [CRM-415]`;
- the commit that fixes an issue says so in a footer line: `Fixes: CRM-415` (also `Closes:`,
  `Resolves:`), or in the subject (`fix(...)` / `hotfix(...)` naming the key, "fixes CRM-415");
- a commit that only touches an issue's work: `Refs: CRM-412` (or the key anywhere).

So a link is `fixes` or `refs`. A merge commit is never linked (its "Merge branch
'feature/ABC-1…'" is the MR's, linked through the MR); a revert is `refs` at most.

Only keys of a registered Jira project are linked, as for MRs (§8). Both sides call in: the
GitLab convert for the commits it wrote (`link_commits`), the Jira sync for the issues it wrote
(`link_issue_commits`).
"""

import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, false, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.jira.links import _registered, extract_keys
from app.jira.scopes import jira_issue_in
from app.jira.settings import task_source
from app.models import AppSetting, Commit, Issue, IssueCommitLink

log = logging.getLogger(__name__)

_KEY = r"[A-Z][A-Z0-9_]{1,9}-[1-9]\d{0,8}"
_KEYS = rf"#?{_KEY}(?:[ \t]*(?:,|&|\band\b)[ \t]*#?{_KEY})*"
_VERB = r"(?i:fix(?:e[sd])?|close[sd]?|resolve[sd]?)"
# A footer line: "Fixes: ABC-1, ABC-2" (git trailer style; "Fix", "Closes", "Resolved" too).
FIX_LINE = re.compile(rf"^[ \t]*{_VERB}[ \t]*:[ \t]*(.+)$", re.MULTILINE)
# "Fixes ABC-1", "Closes #ABC-1 and ABC-2" at the start of a line or of a sentence only:
# "does not fix ABC-1", "partial fix ABC-1" or "will fix ABC-1 later" are not fixes.
FIX_WORD = re.compile(
    rf"(?:^|[.;!?][ \t]+)[ \t]*{_VERB}[ \t]*:?[ \t]+({_KEYS})(?![A-Za-z0-9])", re.MULTILINE
)
# A Conventional Commits subject of a fix: "fix: …", "fix(api)!: …", "hotfix(x): …".
FIX_SUBJECT = re.compile(r"^(?i:fix|hotfix|bugfix)(?:\([^)\n]*\))?!?:")
REVERT_SUBJECT = re.compile(r'^Revert "', re.IGNORECASE)

MAX_SHOWN = 20  # Jira issues shown for one commit (review page)
CHUNK = 200
BATCH = 1000
DONE_KEY = "jira_commit_keys_backfill_v1"
JOB_ID = "jira-commit-keys-backfill-once"
JOB_TIMEOUT = 2 * 60 * 60
# pg advisory locks: every writer of issue_commit_links in turn (the GitLab convert and the Jira
# sync would otherwise deadlock, or each miss the other's uncommitted rows); one backfill run.
LINKS_LOCK = 7_310_219
BACKFILL_LOCK = 7_310_220


def commit_keys(message: str | None) -> list[str]:
    """The key-looking tokens of a commit message (upper case only, first seen first)."""
    return extract_keys(message)


def link_kinds(message: str | None) -> dict[str, str]:
    """key -> "fixes" | "refs" for the keys a commit message names."""
    text = message or ""
    keys = commit_keys(text)
    if not keys:
        return {}
    subject = text.split("\n", 1)[0].strip()
    if REVERT_SUBJECT.match(subject):
        return dict.fromkeys(keys, "refs")
    fixed: set[str] = set()
    for line in FIX_LINE.finditer(text):
        fixed.update(extract_keys(line.group(1)))
    for words in FIX_WORD.finditer(text):
        fixed.update(extract_keys(words.group(1)))
    if FIX_SUBJECT.match(subject):
        fixed.update(extract_keys(subject))
    return {k: "fixes" if k in fixed else "refs" for k in keys}


# -- writing links --------------------------------------------------------------------------------


def _lock(session: Session) -> None:
    """Writers of issue_commit_links take turns, until their transaction ends. Taken before
    reading, so the second one sees what the first committed (a new issue and a new commit
    naming it, written at the same time, still get linked)."""
    session.execute(select(func.pg_advisory_xact_lock(LINKS_LOCK)))


def _write(session: Session, wanted: dict[tuple[int, str], str]) -> int:
    for (issue_id, sha), kind in sorted(wanted.items()):
        stmt = insert(IssueCommitLink).values(issue_id=issue_id, sha=sha, kind=kind)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["issue_id", "sha"],
                set_={"kind": stmt.excluded.kind},
                # A link made by hand keeps what its maker chose.
                where=(IssueCommitLink.kind != stmt.excluded.kind)
                & IssueCommitLink.manual.is_(False),
            )
        )
    return len(wanted)


def link_commits(session: Session, shas: Iterable[str]) -> int:
    """Re-derive the Jira links of these commits from their messages: links they no longer
    name go, the others are written. The keys are stored by the convert; a commit whose stored
    keys differ (one stored before §19) gets them here. Returns the links wanted."""
    wanted_shas = sorted({s for s in shas if s})
    if not wanted_shas:
        return 0
    _lock(session)
    registered = _registered(session)
    written = 0
    for start in range(0, len(wanted_shas), CHUNK):
        chunk = wanted_shas[start : start + CHUNK]
        rows = session.execute(
            select(Commit.sha, Commit.message, Commit.is_merge, Commit.jira_keys).where(
                Commit.sha.in_(chunk)
            )
        ).all()
        stale = [
            {"sha": r.sha, "jira_keys": commit_keys(r.message)}
            for r in rows
            if r.jira_keys != commit_keys(r.message)
        ]
        if stale:
            session.execute(update(Commit).execution_options(synchronize_session=False), stale)
        kinds = {r.sha: ({} if r.is_merge else link_kinds(r.message)) for r in rows}
        wanted = _resolve(session, kinds, registered)
        keep = [
            and_(IssueCommitLink.issue_id == i, IssueCommitLink.sha == sha) for i, sha in wanted
        ]
        session.execute(
            delete(IssueCommitLink).where(
                IssueCommitLink.sha.in_(chunk),
                IssueCommitLink.manual.is_(False),  # a link made by hand stays (§20)
                *([~or_(*keep)] if keep else []),
            )
        )
        written += _write(session, wanted)
    return written


def _resolve(
    session: Session, kinds: dict[str, dict[str, str]], registered: set[str]
) -> dict[tuple[int, str], str]:
    keys = {k for per in kinds.values() for k in per if k.rsplit("-", 1)[0] in registered}
    if not keys:
        return {}
    issue_of = {
        str(k): int(i)
        for k, i in session.execute(
            select(Issue.key, Issue.id).where(Issue.source == "jira", Issue.key.in_(keys))
        )
    }
    return {
        (issue_of[k], sha): kind
        for sha, per in kinds.items()
        for k, kind in per.items()
        if k in issue_of
    }


def link_issue_commits(session: Session, issue_ids: Iterable[int]) -> int:
    """Link these Jira issues to every commit whose stored keys name them (GIN-indexed)."""
    ids = sorted(set(issue_ids))
    if not ids:
        return 0
    _lock(session)
    issue_of = {
        str(key): int(issue_id)
        for issue_id, key in session.execute(
            select(Issue.id, Issue.key).where(
                Issue.id.in_(ids), Issue.source == "jira", Issue.key.is_not(None)
            )
        )
    }
    keys = sorted(issue_of)
    wanted: dict[tuple[int, str], str] = {}
    for start in range(0, len(keys), CHUNK):
        chunk = keys[start : start + CHUNK]
        rows = session.execute(
            select(Commit.sha, Commit.message, Commit.is_merge).where(
                or_(*(Commit.jira_keys.contains([k]) for k in chunk))
            )
        )
        for r in rows:
            if r.is_merge:
                continue
            for key, kind in link_kinds(r.message).items():
                if key in issue_of:
                    wanted[(issue_of[key], str(r.sha))] = kind
    return _write(session, wanted)


# -- the one-off backfill of commits stored before §19 ------------------------------------------


def backfill_batch(session: Session, after: str = "", batch: int = BATCH) -> tuple[str, int, int]:
    """Keys and links of up to `batch` commits after sha `after` whose keys were never read;
    the caller commits. Keyset-paged, so each batch starts where the last ended. Returns (the
    last sha seen, commits read (0: none left), links written)."""
    shas = list(
        session.scalars(
            select(Commit.sha)
            .where(Commit.jira_keys.is_(None), Commit.sha > after)
            .order_by(Commit.sha)
            .limit(batch)
        )
    )
    if not shas:
        return after, 0, 0
    links = link_commits(session, shas)
    session.flush()
    return shas[-1], len(shas), links


def backfill_once_job() -> dict[str, Any]:
    """RQ entry point, queued by the scheduler at start-up: runs once per database (and one run
    at a time: a second one queued while the first runs returns at once)."""
    from app.db import get_sessionmaker

    out = {"commits": 0, "links": 0}
    with get_sessionmaker()() as session:
        if session.get(AppSetting, DONE_KEY) is not None:
            return {"already": True}
        got = session.scalar(select(func.pg_try_advisory_lock(BACKFILL_LOCK)))
        if not got:
            return {"running": True}
        try:
            after = ""
            while True:
                after, seen, links = backfill_batch(session, after)
                if not seen:
                    break
                session.commit()
                out["commits"] += seen
                out["links"] += links
            value = {"at": datetime.now(UTC).isoformat(), **out}
            session.execute(
                pg_insert(AppSetting)
                .values(key=DONE_KEY, value=value)
                .on_conflict_do_nothing(index_elements=["key"])
            )
            session.commit()
        finally:
            session.execute(select(func.pg_advisory_unlock(BACKFILL_LOCK)))
            session.commit()
    log.info("jira commit keys backfill done", extra=out)
    return out


# -- the Jira side of one commit (its review page) ----------------------------------------------


def commit_issues(
    session: Session, settings: Settings, sha: str, business_project_id: int | None
) -> dict[str, Any]:
    """As `app.jira.links.mr_issues`, for a commit review: the issues it fixes / names in the
    repo's dự án, the keys of other dự án (key only), the keys not synced, and whether keys are
    expected. `link` is "closes" for a fix, "mention" for a mere reference."""
    in_scope = jira_issue_in([business_project_id]) if business_project_id is not None else false()
    rows = session.execute(
        select(
            Issue.key,
            Issue.title,
            Issue.state,
            Issue.type,
            Issue.story_points,
            Issue.web_url,
            IssueCommitLink.kind,
            in_scope.label("in_scope"),
        )
        .join(IssueCommitLink, IssueCommitLink.issue_id == Issue.id)
        .where(IssueCommitLink.sha == sha, Issue.source == "jira")
        .order_by((IssueCommitLink.kind == "refs").asc(), Issue.jira_project_id, Issue.iid)
        .limit(MAX_SHOWN)
    ).all()
    issues = [
        {
            "key": r.key,
            "title": r.title,
            "state": r.state,
            "type": r.type,
            "story_points": float(r.story_points) if r.story_points is not None else None,
            "url": r.web_url,
            "link": "closes" if r.kind == "fixes" else "mention",
        }
        for r in rows
        if r.in_scope
    ]
    others = [str(r.key) for r in rows if not r.in_scope]
    message = session.scalar(select(Commit.message).where(Commit.sha == sha))
    registered = _registered(session)
    named = [k for k in commit_keys(message) if k.rsplit("-", 1)[0] in registered]
    synced = (
        set(session.scalars(select(Issue.key).where(Issue.source == "jira", Issue.key.in_(named))))
        if named
        else set()
    )
    return {
        "issues": issues,
        "other_keys": others,
        "missing_keys": [k for k in named if k not in synced][:MAX_SHOWN],
        "expected": task_source(session, settings) == "jira",
        "target": "commit",
    }
