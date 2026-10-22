import io
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import CliError, build_parser, run
from app.models import MetricsUserPeriod, Review, ReviewFinding, User

pytestmark = pytest.mark.integration


def test_recompute_after_an_accepted_dispute(db_session: Session) -> None:
    s = db_session
    user = User(full_name="An")
    s.add(user)
    s.flush()
    review = Review(
        target_type="commit",
        reviewed_sha="a" * 40,
        status="done",
        author_user_id=user.id,
        score=Decimal(50),
        loc_reviewed=100,
        rubric_version="v1",
        finished_at=datetime(2026, 10, 6, 9, tzinfo=UTC),
    )
    s.add(review)
    s.flush()
    finding = ReviewFinding(
        review_id=review.id, severity="critical", category="security", confidence=Decimal(1)
    )
    s.add(finding)
    s.flush()

    finding.status = "false_positive"  # the lead accepted the developer's dispute
    out = io.StringIO()
    args = build_parser().parse_args(
        ["metrics", "recompute", "--from", "2026-10-01", "--to", "2026-10-07"]
    )
    assert run(args, s, None, None, out)  # type: ignore[arg-type]
    # Oct 1-7 overlaps the weeks of Sep 28 and Oct 5, and the month of October.
    assert out.getvalue() == "rescored 1 reviews; aggregated 3 periods\n"

    s.refresh(review)
    assert review.score == Decimal("100.00")
    week = s.scalars(
        select(MetricsUserPeriod).filter_by(period_type="week", period_start=date(2026, 10, 5))
    ).one()
    assert (week.avg_score, week.critical) == (Decimal("100.00"), 0)


def test_recompute_rejects_an_inverted_range(db_session: Session) -> None:
    args = build_parser().parse_args(
        ["metrics", "recompute", "--from", "2026-10-07", "--to", "2026-10-01"]
    )
    with pytest.raises(CliError, match="--to"):
        run(args, db_session, None, None, io.StringIO())  # type: ignore[arg-type]
