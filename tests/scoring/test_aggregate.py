from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    MergeRequest,
    MetricsTeamPeriod,
    MetricsUserPeriod,
    Pipeline,
    Review,
    ReviewFinding,
    Team,
    TeamMember,
    User,
)
from app.scoring.aggregate import (
    ReviewFacts,
    aggregate,
    compute,
    late_periods,
    period_bounds,
    period_start_of,
    previous_period,
)
from tests.collector.test_collect import make_project

VN = ZoneInfo("Asia/Ho_Chi_Minh")
WEEK = date(2026, 10, 5)  # Monday


def test_period_helpers() -> None:
    assert period_start_of(date(2026, 10, 8), "week") == WEEK
    assert period_start_of(date(2026, 10, 8), "month") == date(2026, 10, 1)
    assert previous_period(WEEK, "week") == date(2026, 9, 28)
    assert previous_period(date(2026, 3, 1), "month") == date(2026, 2, 1)
    start, end = period_bounds(date(2026, 12, 1), "month", VN)
    assert (start.isoformat(), end.isoformat()) == (
        "2026-12-01T00:00:00+07:00",
        "2027-01-01T00:00:00+07:00",
    )


def at(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def test_compute_counts_what_the_review_score_counted() -> None:
    """docs/specs/19 §4.4: a rejected finding and an unverified one below the confidence
    threshold are left out of the period metrics as they were left out of the score; the
    threshold is the same setting, and reviews before rubric v4 keep their old counting."""
    v4 = ReviewFacts(
        user_id=1,
        finished_at=at(6),
        score=50.0,
        loc=100,
        rubric_version="v4",
        findings=[
            ("critical", 0.9, "open", "confirmed"),
            ("high", 0.6, "open", "unverified"),  # below 0.7: not counted
            ("medium", 0.9, "rejected", "rejected"),
            ("low", 0.5, "open", "skipped"),
        ],
    )
    row = compute([v4], [], [])
    assert (row["critical"], row["high"], row["medium"], row["low"]) == (1, 0, 0, 1)
    assert row["weighted_penalty"] == Decimal("27.75")  # 30 * 0.9 + 1.5 * 0.5
    lenient = compute([v4], [], [], min_confidence_unverified=0.5)
    assert (lenient["high"], lenient["weighted_penalty"]) == (1, Decimal("36.75"))
    v3 = ReviewFacts(
        user_id=1,
        finished_at=at(6),
        score=50.0,
        loc=100,
        rubric_version="v3",
        findings=[("high", 0.6, "open", "unverified"), ("medium", 0.9, "rejected", "rejected")],
    )
    old = compute([v3], [], [])
    assert (old["high"], old["medium"], old["weighted_penalty"]) == (1, 0, Decimal("9.00"))


@pytest.mark.integration
def test_weekly_user_and_team_metrics(db_session: Session) -> None:
    s = db_session
    project = make_project(s)
    an, binh, bot = (
        User(full_name="An", primary_email="an@x.vn"),
        User(full_name="Binh", primary_email="binh@x.vn"),
        User(full_name="renovate", active=False),
    )
    backend, mobile = Team(name="Backend"), Team(name="Mobile")
    s.add_all([an, binh, bot, backend, mobile])
    s.flush()
    s.add_all(
        [
            TeamMember(team_id=backend.id, user_id=an.id, joined_at=date(2025, 1, 1)),
            # Binh moves from Backend to Mobile on Oct 7.
            TeamMember(
                team_id=backend.id,
                user_id=binh.id,
                joined_at=date(2025, 1, 1),
                left_at=date(2026, 10, 7),
            ),
            TeamMember(team_id=mobile.id, user_id=binh.id, joined_at=date(2026, 10, 7)),
        ]
    )
    an_account = Account(kind="gitlab_user", external_key="11", user_id=an.id)
    s.add(an_account)
    s.flush()

    def review(
        user: User, finished: datetime, score: float, loc: int, *findings: tuple[str, float, str]
    ) -> None:
        r = Review(
            target_type="commit",
            reviewed_sha=f"{len(s.new)}{finished.isoformat()}{user.id}",
            status="done",
            author_user_id=user.id,
            score=Decimal(str(score)),
            loc_reviewed=loc,
            rubric_version="v1",
            finished_at=finished,
        )
        s.add(r)
        s.flush()
        for severity, confidence, status in findings:
            s.add(
                ReviewFinding(
                    review_id=r.id,
                    severity=severity,
                    category="bug",
                    confidence=Decimal(str(confidence)),
                    status=status,
                )
            )

    review(an, at(6), 90, 300, ("medium", 1.0, "open"), ("high", 0.8, "false_positive"))
    review(an, at(8), 50, 100, ("critical", 1.0, "open"))
    review(binh, at(6), 80, 50)  # in Backend that day
    review(binh, at(9), 70, 50, ("low", 1.0, "open"))  # in Mobile by then
    review(bot, at(6), 100, 10)  # inactive users never appear in reports
    # Sunday 23:30 in Vietnam is still this week; Monday 00:30 Vietnam is the next one.
    review(an, datetime(2026, 10, 11, 16, 30, tzinfo=UTC), 100, 10)
    review(an, datetime(2026, 10, 11, 17, 30, tzinfo=UTC), 0, 10)
    s.add(
        Review(
            target_type="mr",
            reviewed_sha="old",
            status="superseded",
            author_user_id=an.id,
            score=Decimal(0),
            loc_reviewed=1000,
            finished_at=at(7),
        )
    )

    s.add(
        MergeRequest(
            project_id=project.id,
            iid=1,
            author_account_id=an_account.id,
            source_branch="feat",
            created_at_src=at(5, 8),
            first_review_at=at(5, 12),
            merged_at=at(7),
            additions=90,
            deletions=10,
            review_rounds=2,
        )
    )
    s.add_all(
        [
            Pipeline(
                id=1, project_id=project.id, ref="feat", status="success", created_at_src=at(5)
            ),
            Pipeline(
                id=2, project_id=project.id, ref="feat", status="failed", created_at_src=at(6)
            ),
            Pipeline(
                id=3, project_id=project.id, ref="other", status="failed", created_at_src=at(6)
            ),
        ]
    )
    s.flush()

    for _ in range(2):  # idempotent
        result = aggregate(s, "week", date(2026, 10, 7), VN)
    assert (result.users, result.teams) == (2, 2)

    rows = {r.user_id: r for r in s.scalars(select(MetricsUserPeriod))}
    assert set(rows) == {an.id, binh.id}
    a = rows[an.id]
    assert (a.period_type, a.period_start) == ("week", WEEK)
    assert a.reviews_count == 3  # superseded review excluded; Monday-00:30 review is next week
    assert a.avg_score == Decimal("80.49")  # (90*300 + 50*100 + 100*10) / 410
    assert (a.critical, a.high, a.medium, a.low) == (1, 0, 1, 0)  # false positive not counted
    assert a.weighted_penalty == Decimal("29.00")  # 4 + 25
    assert a.defect_density == Decimal("7.073")  # 29 / 4.1
    assert (a.mr_count, a.avg_review_time_h, a.avg_mr_size) == (1, Decimal("4.00"), 100)
    assert a.build_success_rate == Decimal("50.00")  # branch "other" is not this MR's

    teams = {t.team_id: t for t in s.scalars(select(MetricsTeamPeriod))}
    assert teams[backend.id].reviews_count == 4  # An x3 + Binh's review before the move
    assert teams[mobile.id].reviews_count == 1
    assert teams[mobile.id].low == 1


@pytest.mark.integration
def test_a_review_counts_in_the_period_of_its_change(db_session: Session) -> None:
    """Scores follow the commit's time (user, 2026-10-29): a commit of the week of Sep 28
    reviewed on Oct 6 counts in Sep 28, which is then aggregated again."""
    s = db_session
    an = User(full_name="An", primary_email="an@x.vn")
    s.add(an)
    s.flush()
    s.add(
        Review(
            target_type="commit",
            reviewed_sha="late",
            status="done",
            author_user_id=an.id,
            score=Decimal(60),
            loc_reviewed=10,
            rubric_version="v1",
            changed_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
            finished_at=at(6),
        )
    )
    s.flush()
    assert late_periods(s, "week", WEEK, VN) == [date(2026, 9, 28)]
    assert aggregate(s, "week", WEEK, VN).users == 0  # not in the week it was reviewed
    aggregate(s, "week", date(2026, 9, 28), VN)
    (row,) = s.scalars(select(MetricsUserPeriod))
    assert (row.period_start, row.reviews_count) == (date(2026, 9, 28), 1)
