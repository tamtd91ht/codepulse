"""Reading the domain layer into the plain facts the insights functions take (docs/specs/20).

The only module of app/insights that touches the database; everything else stays pure. Shared by
the Insights API and the evidence packs (app/evidence).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.insights.flow import BOT_MARKER, MrFacts, Note
from app.models import Account, Commit, MergeRequest, MrCommit, MrNote

CHUNK = 1000  # ids per IN list


def chunks(ids: Sequence[int]) -> Iterable[Sequence[int]]:
    for i in range(0, len(ids), CHUNK):
        yield ids[i : i + CHUNK]


def load_mrs(
    session: Session,
    repo_ids: list[int],
    *,
    merged_between: tuple[datetime, datetime] | None = None,
    open_only: bool = False,
) -> list[MrFacts]:
    """Merged MRs in [lo, hi) of `merged_between`, or the open ones, with what §4.2 needs."""
    if not repo_ids:
        return []
    query = (
        select(MergeRequest, Account.user_id)
        .outerjoin(Account, Account.id == MergeRequest.author_account_id)
        .where(MergeRequest.project_id.in_(repo_ids))
    )
    if merged_between is not None:
        lo, hi = merged_between
        query = query.where(
            MergeRequest.state == "merged",
            MergeRequest.merged_at >= lo,
            MergeRequest.merged_at < hi,
        )
    if open_only:
        query = query.where(MergeRequest.state == "opened")
    rows = session.execute(query).all()
    ids = [mr.id for mr, _u in rows]
    notes = load_notes(session, ids)
    first_at = first_commits(session, ids)
    return [
        MrFacts(
            id=mr.id,
            project_id=int(mr.project_id or 0),
            iid=mr.iid,
            title=mr.title,
            author_account_id=mr.author_account_id,
            author_user_id=user_id,
            state=mr.state,
            target_branch=mr.target_branch,
            opened_at=mr.created_at_src,
            merged_at=mr.merged_at,
            additions=mr.additions,
            deletions=mr.deletions,
            review_rounds=mr.review_rounds,
            first_commit_at=first_at.get(mr.id),
            notes=tuple(notes.get(mr.id, ())),
        )
        for mr, user_id in rows
    ]


def first_commits(session: Session, mr_ids: list[int]) -> dict[int, datetime]:
    """mr id -> its earliest non-merge commit's author date, for these MRs only."""
    out: dict[int, datetime] = {}
    for chunk in chunks(mr_ids):
        rows = session.execute(
            select(MrCommit.mr_id, func.min(Commit.authored_at))
            .join(Commit, Commit.sha == MrCommit.sha)
            .where(MrCommit.mr_id.in_(chunk), Commit.is_merge.is_not(True))
            .group_by(MrCommit.mr_id)
        ).all()
        for mr_id, first_at in rows:
            if first_at is not None:
                out[int(mr_id)] = first_at
    return out


def load_notes(session: Session, mr_ids: list[int]) -> dict[int, list[Note]]:
    """Notes that can be a response or a ready mark (§4.2.1). Other system notes (most of the
    rows) and CodePulse's own review comments are dropped in SQL; a comment's text is never
    read, only system notes need theirs."""
    out: dict[int, list[Note]] = defaultdict(list)
    body = func.lower(MrNote.body)
    system = MrNote.is_system.is_(True)
    for chunk in chunks(mr_ids):
        rows = session.execute(
            select(
                MrNote.mr_id,
                MrNote.author_account_id,
                Account.user_id,
                case((system, MrNote.body), else_=literal("")),
                system,
                MrNote.created_at_src,
            )
            .outerjoin(Account, Account.id == MrNote.author_account_id)
            .where(
                MrNote.mr_id.in_(chunk),
                or_(
                    and_(
                        MrNote.is_system.is_not(True),
                        func.coalesce(MrNote.body, "").not_like(f"%{BOT_MARKER}%"),
                    ),
                    and_(
                        system,
                        or_(
                            body.contains("approved this merge request"),
                            body.contains("as **ready**"),
                            body.contains("as ready"),
                            body.contains("work in progress"),
                        ),
                    ),
                ),
            )
        ).all()
        for mr_id, account_id, user_id, text, is_system, created in rows:
            if mr_id is not None:
                out[int(mr_id)].append(
                    Note(account_id, user_id, text or "", bool(is_system), created)
                )
    return out
