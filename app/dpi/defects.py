"""Production defects (docs/specs/25 §4.4): where a prod-bug may come from. Suggestions only:
the lead confirms the root cause and who carries it."""

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
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


def _author(session: Session, account_id: int | None) -> dict[str, Any] | None:
    account = session.get(Account, account_id) if account_id else None
    user = session.get(User, account.user_id) if account and account.user_id else None
    if user is None:
        return None
    return {"user_id": user.id, "name": user.full_name or user.primary_email}


def suggest(session: Session, defect: ProdDefect, issue: Issue) -> list[dict[str, Any]]:
    """Candidates for the root cause: commits a revert near the bug undid, and MRs merged in
    the LOOKBACK before the bug that touched the files its fix changed."""
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
        root = session.scalars(select(Commit).where(Commit.sha.like(f"{match.group(1)}%"))).first()
        out.append(
            {
                "kind": "revert",
                "commit": (root.sha if root else match.group(1))[:12],
                "via": sha[:12],
                "author": _author(session, root.author_account_id) if root else None,
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
                    "author": _author(session, mr.author_account_id),
                }
            )
    return out[:MAX_SUGGESTIONS]
