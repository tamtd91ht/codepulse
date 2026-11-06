"""Jira issue <-> GitLab MR links by key (docs/specs/27 §8).

The GitLab collector stores every key-looking token of an MR (title, description, source
branch) in `merge_requests.jira_keys`; only keys of a registered Jira project are linked. A key
in the title or the branch is a `closes` link (the branch `feature/ABC-123-...` is that issue's
work), one only in the description a `mention`.

Both sides call in: the GitLab convert for the MRs it just wrote (`link_merge_requests`), the
Jira sync for the issues it just wrote (`link_issues`).
"""

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import case, delete, false, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.jira.scopes import jira_issue_in
from app.jira.settings import JIRA_ID_OFFSET, task_source
from app.models import Issue, IssueMrLink, JiraProject, MergeRequest

# A key ends before a letter or digit; `_` may follow (`feature/ABC-123_login`).
_PATTERN = r"(?<![A-Za-z0-9_-])([A-Z][A-Z0-9_]{1,9})-([1-9]\d{0,8})(?![A-Za-z0-9])"
KEY = re.compile(_PATTERN)
# Branch names are often lower case (`feature/abc-123-login`): matched in any case, upper-cased.
BRANCH_KEY = re.compile(_PATTERN, re.IGNORECASE)
MAX_KEYS = 50  # per MR: a description listing more is not about each of them
CHUNK = 200  # keys per MR query in `link_issues`
RANK = {"mention": 1, "branch": 2, "closes": 3}


def extract_keys(*texts: str | None, branch: str | None = None) -> list[str]:
    """Key-looking tokens (ABC-123) of `texts` (upper case only) then of `branch` (any case,
    upper-cased): first seen first, no duplicates, at most MAX_KEYS."""
    out: list[str] = []
    found = [(KEY, text) for text in texts] + [(BRANCH_KEY, branch)]
    for pattern, text in found:
        for project, number in pattern.findall(text or ""):
            key = f"{project.upper()}-{number}"
            if key not in out:
                out.append(key)
                if len(out) >= MAX_KEYS:
                    return out
    return out


def _source(key: str, title: str | None, branch: str | None) -> str:
    return "closes" if key in extract_keys(title, branch=branch) else "mention"


def _registered(session: Session) -> set[str]:
    return set(session.scalars(select(JiraProject.key)))


def _write(session: Session, wanted: dict[tuple[int, int], str]) -> int:
    for (issue_id, mr_id), source in wanted.items():
        stmt = insert(IssueMrLink).values(issue_id=issue_id, mr_id=mr_id, source=source)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["issue_id", "mr_id"],
                set_={"source": stmt.excluded.source},
                # Keep the strongest source ever seen (as the GitLab links do).
                where=IssueMrLink.source != "closes",
            )
        )
    return len(wanted)


_MR_COLUMNS = (
    MergeRequest.id,
    MergeRequest.title,
    MergeRequest.source_branch,
    MergeRequest.jira_keys,
)


def link_merge_requests(session: Session, mr_ids: Iterable[int]) -> int:
    """Re-derive the Jira links of these MRs from their stored keys. A `mention` link whose key
    left the MR goes; a `closes` one stays (as GitLab's do). Returns the links written."""
    ids = sorted(set(mr_ids))
    if not ids:
        return 0
    mrs = session.execute(select(*_MR_COLUMNS).where(MergeRequest.id.in_(ids))).all()
    session.execute(
        delete(IssueMrLink).where(
            IssueMrLink.mr_id.in_(ids),
            IssueMrLink.source != "closes",
            IssueMrLink.issue_id >= JIRA_ID_OFFSET,  # Jira issues' links only
        )
    )
    registered = _registered(session)
    keys = {k for mr in mrs for k in mr.jira_keys or [] if k.rsplit("-", 1)[0] in registered}
    if not keys:
        return 0
    issue_of = {
        str(k): int(i)
        for k, i in session.execute(
            select(Issue.key, Issue.id).where(Issue.source == "jira", Issue.key.in_(keys))
        )
    }
    wanted: dict[tuple[int, int], str] = {}
    for mr in mrs:
        for key in mr.jira_keys or []:
            if key in issue_of:
                wanted[(issue_of[key], int(mr.id))] = _source(key, mr.title, mr.source_branch)
    return _write(session, wanted)


def link_issues(session: Session, issue_ids: Iterable[int]) -> int:
    """Link these Jira issues to every MR whose stored keys name them: one MR query per CHUNK
    keys (`jira_keys @> [key]` OR-ed, GIN-indexed), only the columns needed. Returns links
    written."""
    ids = sorted(set(issue_ids))
    if not ids:
        return 0
    issue_of = {
        str(key): int(issue_id)
        for issue_id, key in session.execute(
            select(Issue.id, Issue.key).where(
                Issue.id.in_(ids), Issue.source == "jira", Issue.key.is_not(None)
            )
        )
    }
    keys = sorted(issue_of)
    wanted: dict[tuple[int, int], str] = {}
    for start in range(0, len(keys), CHUNK):
        chunk = keys[start : start + CHUNK]
        rows = session.execute(
            select(*_MR_COLUMNS).where(or_(*(MergeRequest.jira_keys.contains([k]) for k in chunk)))
        )
        for mr in rows:
            for key in mr.jira_keys or []:
                if key in issue_of:
                    wanted[(issue_of[key], int(mr.id))] = _source(key, mr.title, mr.source_branch)
    return _write(session, wanted)


MAX_SHOWN = 20  # Jira issues shown for one MR (review page, MCP)


def _issue_row(row: Any) -> dict[str, Any]:
    return {
        "key": row.key,
        "title": row.title,
        "state": row.state,
        "type": row.type,
        "story_points": float(row.story_points) if row.story_points is not None else None,
        "url": row.web_url,
        # closes: the key is in the MR title or source branch; mention: description only.
        "link": "mention" if row.source == "mention" else "closes",
    }


def mr_issues(
    session: Session, settings: Settings, mr: MergeRequest, business_project_id: int | None
) -> dict[str, Any]:
    """The Jira side of an MR (docs/specs/27 §17).

    - `issues`: linked Jira issues counted for the repo's dự án (`jira_issue_in`), title/branch
      links first, then by Jira project and number, at most MAX_SHOWN.
    - `other_keys`: linked issues of another dự án (or a repo without one): key only. Anyone may
      type any key in an MR; that must not show a title the reader has no business seeing.
    - `missing_keys`: keys of registered Jira projects the MR names whose issue is not synced.
    - `expected`: the task source is Jira (an MR is then expected to name an issue).
    """
    rank = case((IssueMrLink.source == "mention", 1), else_=0)
    in_scope = jira_issue_in([business_project_id]) if business_project_id is not None else false()
    rows = session.execute(
        select(
            Issue.key,
            Issue.title,
            Issue.state,
            Issue.type,
            Issue.story_points,
            Issue.web_url,
            IssueMrLink.source,
            in_scope.label("in_scope"),
        )
        .join(IssueMrLink, IssueMrLink.issue_id == Issue.id)
        .where(IssueMrLink.mr_id == mr.id, Issue.source == "jira")
        .order_by(rank, Issue.jira_project_id, Issue.iid)
        .limit(MAX_SHOWN)
    ).all()
    issues = [_issue_row(r) for r in rows if r.in_scope]
    others = [str(r.key) for r in rows if not r.in_scope]
    registered = _registered(session)
    named = [k for k in mr.jira_keys or [] if k.rsplit("-", 1)[0] in registered]
    synced = (
        set(session.scalars(select(Issue.key).where(Issue.source == "jira", Issue.key.in_(named))))
        if named
        else set()
    )
    missing = [k for k in named if k not in synced][:MAX_SHOWN]
    return {
        "issues": issues,
        "other_keys": others,
        "missing_keys": missing,
        "expected": task_source(session, settings) == "jira",
    }
