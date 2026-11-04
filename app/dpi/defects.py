"""Production defects (docs/specs/25 §4.4): where a prod-bug may come from. Suggestions only:
the lead confirms the root cause and who carries it."""

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Commit,
    CommitFile,
    Issue,
    IssueMrLink,
    MergeRequest,
    MrCommit,
    ProdDefect,
    User,
)

REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.IGNORECASE)
LOOKBACK = timedelta(days=14)
MAX_SUGGESTIONS = 6


class Authors:
    """account id -> {user_id, name}, read once for all the suggestions of a page."""

    def __init__(self, session: Session) -> None:
        self.s = session
        self.cache: dict[int, dict[str, Any] | None] = {}

    def load(self, account_ids: set[int]) -> None:
        wanted = [a for a in account_ids if a not in self.cache]
        if not wanted:
            return
        rows = self.s.execute(
            select(Account.id, User.id, User.full_name, User.primary_email)
            .join(User, User.id == Account.user_id)
            .where(Account.id.in_(wanted))
        ).all()
        found = {int(a): {"user_id": int(u), "name": n or e} for a, u, n, e in rows}
        for a in wanted:
            self.cache[a] = found.get(a)

    def get(self, account_id: int | None) -> dict[str, Any] | None:
        if account_id is None:
            return None
        self.load({account_id})
        return self.cache.get(account_id)


def _by_prefix(prefix: str) -> Any:
    """A SHA starting with `prefix`, as a range on the primary key (LIKE 'abc%' is not
    indexable under a non-C collation); SHAs are lowercase hex."""
    p = prefix.lower()
    return and_(Commit.sha >= p, Commit.sha < p + "g")


def suggest(
    session: Session, defect: ProdDefect, issue: Issue, authors: Authors | None = None
) -> list[dict[str, Any]]:
    """Candidates for the root cause: commits a revert near the bug undid, and MRs merged in
    the LOOKBACK before the bug that touched the files its fix changed."""
    authors = authors or Authors(session)
    out: list[dict[str, Any]] = []
    created = issue.created_at_src
    if created is None:
        return out
    # Reverts in the repo around the report.
    for sha, message in session.execute(
        select(Commit.sha, Commit.message).where(
            Commit.project_id == issue.project_id,
            Commit.authored_at >= created - LOOKBACK,
            Commit.authored_at <= created + LOOKBACK,
            Commit.message.ilike("%This reverts commit%"),
        )
    ):
        match = REVERTS.search(message or "")
        if not match:
            continue
        root = session.scalars(select(Commit).where(_by_prefix(match.group(1))).limit(1)).first()
        out.append(
            {
                "kind": "revert",
                "commit": (root.sha if root else match.group(1))[:12],
                "via": sha[:12],
                "author": authors.get(root.author_account_id) if root else None,
            }
        )
    # The fix's files: those of the MRs that close the issue.
    fixes = list(
        session.scalars(
            select(IssueMrLink.mr_id).where(
                IssueMrLink.issue_id == issue.id, IssueMrLink.source == "closes"
            )
        )
    )
    files = set(
        session.scalars(
            select(CommitFile.path)
            .join(MrCommit, MrCommit.sha == CommitFile.sha)
            .where(MrCommit.mr_id.in_(fixes or [0]))
        )
    )
    if files:
        rows = session.execute(
            select(MergeRequest)
            .join(MrCommit, MrCommit.mr_id == MergeRequest.id)
            .join(CommitFile, CommitFile.sha == MrCommit.sha)
            .where(
                MergeRequest.project_id == issue.project_id,
                MergeRequest.state == "merged",
                MergeRequest.merged_at >= created - LOOKBACK,
                MergeRequest.merged_at <= created,
                MergeRequest.id.not_in(fixes or [0]),
                CommitFile.path.in_(files),
            )
            .distinct()
            .order_by(MergeRequest.merged_at.desc())
        ).scalars()
        for mr in list(rows)[:MAX_SUGGESTIONS]:
            out.append(
                {
                    "kind": "mr",
                    "mr_id": mr.id,
                    "ref": f"!{mr.iid}",
                    "title": mr.title,
                    "merged_at": mr.merged_at,
                    "author": authors.get(mr.author_account_id),
                }
            )
    return out[:MAX_SUGGESTIONS]
