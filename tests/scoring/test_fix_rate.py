from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MergeRequest, MetricsUserPeriod, Review, ReviewFinding, User
from app.review.status import mark_fixed_findings
from app.scoring.aggregate import aggregate
from tests.collector.test_collect import make_project

pytestmark = pytest.mark.integration


def add_review(
    s: Session, user: User, mr: MergeRequest, sha: str, status: str, *prints: tuple[str, str]
) -> Review:
    review = Review(
        target_type="mr",
        mr_id=mr.id,
        reviewed_sha=sha,
        status=status,
        author_user_id=user.id,
        score=Decimal(80),
        loc_reviewed=100,
        rubric_version="v1",
        finished_at=datetime(2026, 10, 6, 10, tzinfo=UTC),
    )
    s.add(review)
    s.flush()
    for severity, fingerprint in prints:
        s.add(
            ReviewFinding(
                review_id=review.id,
                severity=severity,
                category="bug",
                confidence=Decimal(1),
                fingerprint=fingerprint,
            )
        )
    s.flush()
    return review


def test_fixed_findings_and_fix_rate(db_session: Session) -> None:
    s = db_session
    project = make_project(s)
    user = User(full_name="An")
    mr = MergeRequest(project_id=project.id, iid=1)
    s.add_all([user, mr])
    s.flush()
    # v1: three severe issues and a low one; v2 (latest) still has "sqli".
    first = add_review(
        s,
        user,
        mr,
        "v1",
        "superseded",
        ("critical", "sqli"),
        ("high", "npe"),
        ("high", "race"),
        ("low", "naming"),
    )
    latest = add_review(s, user, mr, "v2", "done", ("critical", "sqli"))

    assert mark_fixed_findings(s, mr.id, latest.id) == 2
    statuses = {
        f.fingerprint: f.status
        for f in s.scalars(select(ReviewFinding).where(ReviewFinding.review_id == first.id))
    }
    assert statuses == {"sqli": "open", "npe": "fixed", "race": "fixed", "naming": "open"}

    aggregate(s, "week", date(2026, 10, 5), ZoneInfo("UTC"))
    row = s.scalars(select(MetricsUserPeriod)).one()
    assert row.reviews_count == 1
    assert row.fix_rate == Decimal("0.667")  # npe and race fixed, sqli still there
