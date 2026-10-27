from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.scoring.evaluation import (
    FindingFacts,
    ReviewFacts,
    evaluate_member,
    evaluate_members,
    week_starts,
)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def review(
    rid: int,
    user_id: int,
    score: float | None,
    loc: int,
    finished: datetime,
    *,
    verdict: str | None = "approve",
    target_type: str = "mr",
    rubric: str = "v4",
    project_id: int | None = 1,
    findings: list[FindingFacts] | None = None,
) -> ReviewFacts:
    return ReviewFacts(
        id=rid,
        user_id=user_id,
        finished_at=finished,
        score=score,
        loc=loc,
        target_type=target_type,
        verdict=verdict,
        rubric_version=rubric,
        project_id=project_id,
        project_path=f"demo/p{project_id}",
        findings=findings or [],
    )


def test_week_starts_cover_the_range_from_its_monday() -> None:
    assert week_starts(date(2026, 10, 7), date(2026, 10, 20)) == [
        date(2026, 10, 5),
        date(2026, 10, 12),
        date(2026, 10, 19),
    ]


def test_members_are_scored_by_loc_weight_and_findings_count_like_the_score() -> None:
    oct6 = datetime(2026, 10, 6, 9, tzinfo=UTC)
    oct13 = datetime(2026, 10, 13, 9, tzinfo=UTC)
    reviews = [
        # 500 LOC at 70 + 20 LOC at 100: the big MR dominates the average.
        review(
            1,
            7,
            70,
            500,
            oct6,
            verdict="fix_required",
            findings=[
                FindingFacts("high", "bug", 0.9, verified="confirmed"),
                FindingFacts(
                    "medium", "testing", 0.5, verified="unverified"
                ),  # not confident enough
                FindingFacts("critical", "security", 0.9, status="false_positive"),
                FindingFacts("low", "style", 0.9, verified="rejected"),
            ],
        ),
        review(2, 7, 100, 20, oct13, target_type="commit", project_id=2),
        review(
            3,
            8,
            40,
            100,
            oct6,
            verdict="block",
            findings=[
                FindingFacts("critical", "security", 1.0, verified="confirmed"),
            ],
        ),
        review(4, 9, None, 10, oct6, verdict=None),  # scored nothing (skipped / failed)
    ]
    out = evaluate_members(reviews, date(2026, 10, 5), date(2026, 10, 18), TZ)

    assert out["weeks"] == [date(2026, 10, 5), date(2026, 10, 12)]
    rows = {m["user_id"]: m for m in out["members"]}
    an = rows[7]
    assert an["reviews"] == 2 and an["mrs"] == 1 and an["commits"] == 1 and an["repos"] == 2
    assert an["avg_score"] == round((70 * 500 + 100 * 20) / 520, 1)
    assert an["min_score"] == 70
    assert an["high"] == 1 and an["medium"] == 0 and an["critical"] == 0 and an["low"] == 0
    assert an["findings"] == 1 and an["disputed"] == 1 and an["rejected"] == 1
    assert an["verdicts"] == {"approve": 1, "needs_human": 0, "fix_required": 1, "block": 0}
    assert an["pass_rate"] == 50.0
    assert an["needs_attention"] is False
    assert an["trend"] == [70.0, 100.0]
    assert an["last_review_at"] == oct13

    binh = rows[8]
    assert binh["critical"] == 1 and binh["needs_attention"] is True
    assert binh["findings_per_100_loc"] == 1.0
    assert binh["trend"] == [40.0, None]

    nobody = rows[9]
    assert nobody["avg_score"] is None and nobody["reviews"] == 1 and nobody["pass_rate"] is None

    # Lowest average first, people without a score last.
    assert [m["user_id"] for m in out["members"]] == [8, 7, 9]
    assert out["totals"]["members"] == 3 and out["totals"]["reviews"] == 4
    assert out["totals"]["critical"] == 1 and out["totals"]["high"] == 1


def test_pre_v4_reviews_count_unverified_findings() -> None:
    oct6 = datetime(2026, 10, 6, 9, tzinfo=UTC)
    reviews = [
        review(
            1,
            7,
            80,
            100,
            oct6,
            rubric="v3",
            findings=[
                FindingFacts("medium", "testing", 0.5, verified="skipped"),
            ],
        )
    ]
    out = evaluate_members(reviews, date(2026, 10, 5), date(2026, 10, 11), TZ)
    assert out["members"][0]["medium"] == 1


def test_low_average_flags_attention_and_weeks_follow_the_report_timezone() -> None:
    # 23:30 UTC on Sunday is already Monday in Asia/Ho_Chi_Minh: the next week.
    sunday_late = datetime(2026, 10, 11, 23, 30, tzinfo=UTC)
    out = evaluate_members(
        [review(1, 7, 50, 100, sunday_late)], date(2026, 10, 5), date(2026, 10, 18), TZ
    )
    row = out["members"][0]
    assert row["needs_attention"] is True
    assert row["trend"] == [None, 50.0]


def test_member_breakdown_by_repo_and_category() -> None:
    oct6 = datetime(2026, 10, 6, 9, tzinfo=UTC)
    reviews = [
        review(
            1,
            7,
            70,
            100,
            oct6,
            project_id=1,
            findings=[
                FindingFacts("high", "bug", 0.9, verified="confirmed"),
                FindingFacts("low", "style", 0.9, verified="confirmed"),
            ],
        ),
        review(
            2,
            7,
            90,
            100,
            oct6,
            project_id=1,
            findings=[
                FindingFacts("low", "style", 0.9, verified="confirmed"),
            ],
        ),
        review(3, 7, 100, 50, oct6, project_id=2, target_type="commit"),
    ]
    out = evaluate_member(reviews, date(2026, 10, 5), date(2026, 10, 11), TZ)
    assert out["summary"]["reviews"] == 3 and out["summary"]["repos"] == 2
    assert [r["path"] for r in out["by_repo"]] == ["demo/p1", "demo/p2"]  # most reviews first
    assert out["by_repo"][0]["avg_score"] == 80.0 and out["by_repo"][0]["high"] == 1
    assert out["categories"] == [{"category": "style", "count": 2}, {"category": "bug", "count": 1}]
    assert out["trend"] == [round((70 * 100 + 90 * 100 + 100 * 50) / 250, 1)]
    assert out["trend_reviews"] == [3]
