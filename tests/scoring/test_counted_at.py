from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Review
from app.models.review import CHANGE_TIME_UNKNOWN

FINISHED = datetime(2026, 10, 20, 9, tzinfo=UTC)


def review(changed_at: datetime | None, finished_at: datetime | None = FINISHED) -> Review:
    return Review(
        target_type="commit",
        reviewed_sha=f"{changed_at}",
        status="done",
        rubric_version="v1",
        changed_at=changed_at,
        finished_at=finished_at,
    )


@pytest.mark.parametrize(
    ("changed", "counted"),
    [
        (FINISHED - timedelta(days=3), FINISHED - timedelta(days=3)),  # the commit's week
        (FINISHED - timedelta(days=30), FINISHED - timedelta(days=30)),  # at the limit
        (FINISHED - timedelta(days=31), FINISHED),  # older: back-dated, the finish counts
        (FINISHED + timedelta(hours=1), FINISHED),  # after the review: future-dated
        (CHANGE_TIME_UNKNOWN, FINISHED),
        (None, FINISHED),
    ],
)
def test_the_commit_time_counts_within_30_days_before_the_review(
    changed: datetime | None, counted: datetime
) -> None:
    assert review(changed).counted_at == counted


def test_an_unfinished_review_keeps_its_change_time() -> None:
    day = FINISHED - timedelta(days=90)
    assert review(day, finished_at=None).counted_at == day


@pytest.mark.integration
def test_the_database_counts_the_same_moment(db_session: Session) -> None:
    rows = [
        review(FINISHED - timedelta(days=3)),
        review(FINISHED - timedelta(days=31)),
        review(FINISHED + timedelta(hours=1)),
        review(CHANGE_TIME_UNKNOWN),
        review(FINISHED - timedelta(days=90), finished_at=None),
    ]
    db_session.add_all(rows)
    db_session.flush()
    in_db = dict(
        db_session.execute(
            select(Review.id, Review.counted_at).where(Review.id.in_([r.id for r in rows]))
        ).tuples()
    )
    assert in_db == {r.id: r.counted_at for r in rows}
