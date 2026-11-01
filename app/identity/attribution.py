"""Reviews follow their author's account (user 2026-11-01).

A review stores its author (`Review.author_user_id`) when it runs: the person behind the commit's
or the merge request's author account at that moment. An account nobody was linked to leaves the
review without a person, so it counted for nobody. When an account gets its person, those reviews
are given that person here, and the stored weekly / monthly metrics they touch are rebuilt:

- by hand (Admin > Tài khoản chưa gắn người, "Gắn" in the commit list), with the `backfill`
  option (default on): every review of that account takes its person, so a corrected link also
  moves the reviews of the person linked before. Unlinking never touches reviews.
- the GitLab member import (same option): the imported accounts and the commit emails its
  autolink tied, reviews without a person only; a member moved from someone else as above.
- a scheduled sync (`sync_project_job`): the accounts its autolink tied in that run, reviews
  without a person only. Other autolinks (the sync a review runs first, `fetch_commit`, the CLI)
  are not followed here; reviews of those accounts keep no person until an admin links again.

Only the accounts whose person changed are looked at: one UPDATE ... FROM per target type
(commit / MR) joined on primary keys plus `author_account_id` (indexed), returning the moments the
changed reviews count in. Merged MRs of those accounts are added too: the stored metrics count
them through the account's current person (scoring/aggregate.load_period). Every week / month
touched is rebuilt by one background job, the running ones included (a dispute or a recompute may
have stored them already). Per-review scores do not depend on the author, so nothing is re-scored.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from rq import Retry
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.orm import Session

from app.models import Account, Commit, MergeRequest, Review
from app.scoring.aggregate import PeriodType, period_start_of

log = logging.getLogger(__name__)

# One attribution at a time (an admin link, an import, a sync): two of them updating the same
# reviews in a different order could deadlock. Held to the end of the caller's transaction.
LOCK = (0x0C0D, 0xA77B)
PERIOD_TYPES: tuple[PeriodType, ...] = ("week", "month")
REBUILD_TIMEOUT_S = 60 * 60


class Enqueuer(Protocol):
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> Any: ...


@dataclass
class Attribution:
    reviews: int = 0  # reviews whose person changed
    periods: set[tuple[PeriodType, date]] = field(default_factory=set)  # metrics to rebuild

    def merge(self, other: "Attribution") -> None:
        self.reviews += other.reviews
        self.periods |= other.periods

    def job_args(self) -> list[list[str]]:
        return [[kind, start.isoformat()] for kind, start in sorted(self.periods)]


def attribute_reviews(
    session: Session,
    tz: ZoneInfo,
    account_ids: Collection[int],
    *,
    unassigned_only: bool = True,
) -> Attribution:
    """Give the reviews of `account_ids` (accounts whose person just changed) that person.

    `unassigned_only`: only reviews without a person (the sweep after automatic links).
    False, for accounts an admin linked: every review of those accounts takes the account's
    person, which also moves reviews from a person the account was wrongly linked to."""
    ids = sorted(set(account_ids))
    if not ids:
        return Attribution()
    session.execute(select(func.pg_advisory_xact_lock(*LOCK)))
    result = Attribution()
    moments: list[datetime] = []
    targets: tuple[tuple[str, ColumnElement[bool], ColumnElement[bool]], ...] = (
        ("commit", Review.commit_sha == Commit.sha, Commit.author_account_id == Account.id),
        ("mr", Review.mr_id == MergeRequest.id, MergeRequest.author_account_id == Account.id),
    )
    for target_type, to_target, to_account in targets:
        conditions = [Review.target_type == target_type, to_target, to_account, Account.id.in_(ids)]
        if unassigned_only:
            conditions += [Review.author_user_id.is_(None), Account.user_id.is_not(None)]
        else:
            conditions.append(Review.author_user_id.is_distinct_from(Account.user_id))
        rows = session.execute(
            update(Review)
            .where(*conditions)
            .values(author_user_id=Account.user_id)
            .returning(Review.status, Review.counted_at)
            .execution_options(synchronize_session=False)
        ).all()
        result.reviews += len(rows)
        # Only finished reviews are in the stored metrics (scoring/aggregate.load_period).
        moments += [moment for status, moment in rows if status == "done" and moment is not None]
    # Merged MRs count for the account's current person (MR count, size, review time, builds).
    moments += [
        merged
        for merged in session.scalars(
            select(MergeRequest.merged_at).where(
                MergeRequest.author_account_id.in_(ids), MergeRequest.merged_at.is_not(None)
            )
        )
        if merged is not None
    ]
    for moment in moments:
        day = moment.astimezone(tz).date()
        for kind in PERIOD_TYPES:
            result.periods.add((kind, period_start_of(day, kind)))
    if result.reviews:
        # Review objects already loaded must not keep the old person.
        for obj in list(session.identity_map.values()):
            if isinstance(obj, Review):
                session.expire(obj, ["author_user_id"])
    return result


def rebuild_after_commit(queue: Enqueuer, attribution: Attribution) -> int:
    """Queue the rebuild of the stored metrics the attribution touched, after the caller
    committed (the job reads the reviews' new person). Returns how many periods were queued;
    0 when there is nothing to rebuild or Redis is down (the link itself stands; an admin
    runs `codepulse metrics recompute --from --to` to catch up)."""
    if not attribution.periods:
        return 0
    try:
        queue.enqueue(
            "app.scoring.aggregate.aggregate_periods_job",
            attribution.job_args(),
            job_timeout=REBUILD_TIMEOUT_S,
            retry=Retry(max=2, interval=[60, 300]),
        )
    except Exception:
        log.exception("could not queue the metrics rebuild after linking an account")
        return 0
    return len(attribution.periods)
