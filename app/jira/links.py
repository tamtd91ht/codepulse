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

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.jira.settings import JIRA_ID_OFFSET
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
