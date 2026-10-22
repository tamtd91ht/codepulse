from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MetricsUserPeriod, Review, Team, TeamMember, User
from app.scoring.aggregate import aggregate
from app.scoring.compare import compare_team, compare_user

pytestmark = pytest.mark.integration
UTC_TZ = ZoneInfo("UTC")


def reviews(s: Session, user: User, day: int, score: int, count: int) -> None:
    for i in range(count):
        s.add(
            Review(
                target_type="commit",
                reviewed_sha=f"{user.id}-{day}-{i}",
                status="done",
                author_user_id=user.id,
                score=Decimal(score),
                loc_reviewed=100,
                rubric_version="v1",
                finished_at=datetime(2026, 10, day, 9, tzinfo=UTC),
            )
        )
    s.flush()


def test_min_sample_and_week_over_week_delta(db_session: Session) -> None:
    s = db_session
    an, binh, team = User(full_name="An"), User(full_name="Binh"), Team(name="Backend")
    s.add_all([an, binh, team])
    s.flush()
    s.add_all(
        [
            TeamMember(team_id=team.id, user_id=an.id, joined_at=date(2025, 1, 1)),
            TeamMember(team_id=team.id, user_id=binh.id, joined_at=date(2025, 1, 1)),
        ]
    )
    reviews(s, an, 6, 70, 3)  # week of Oct 5
    reviews(s, an, 13, 90, 3)  # week of Oct 12
    reviews(s, binh, 13, 60, 2)  # below MIN_SAMPLE=3
    for week in (date(2026, 10, 5), date(2026, 10, 12)):
        aggregate(s, "week", week, UTC_TZ, min_sample=3)

    binh_row = s.scalars(select(MetricsUserPeriod).filter_by(user_id=binh.id)).one()
    assert binh_row.avg_score == Decimal("60.00") and binh_row.quality_index is None

    result = compare_user(s, an.id, "week", date(2026, 10, 12))
    assert result.current is not None and result.previous is not None
    # No findings, pipelines or severe issues: index = (0.5*score + 0.2*100) / 0.7
    assert result.current.quality_index == Decimal("92.86")
    assert result.previous.quality_index == Decimal("78.57")
    assert result.delta["quality_index"] == 14.29
    assert result.delta["avg_score"] == 20.0
    assert result.delta["critical"] == 0
    # Team: An x3 at 90 and Binh x2 at 60 -> avg 78, index (39 + 20) / 0.7
    assert result.team_quality_index == 84.29

    team_result = compare_team(s, team.id, "week", date(2026, 10, 12))
    assert team_result.delta["avg_score"] == 8.0  # 70 -> 78


def test_first_period_has_no_delta(db_session: Session) -> None:
    user = User(full_name="New")
    db_session.add(user)
    db_session.flush()
    reviews(db_session, user, 6, 80, 3)
    aggregate(db_session, "week", date(2026, 10, 5), UTC_TZ)
    result = compare_user(db_session, user.id, "week", date(2026, 10, 5))
    assert result.previous is None
    assert set(result.delta.values()) == {None}
