import pytest

from app.scoring.formula import ScoredFinding, review_score


def f(severity: str, confidence: float = 1.0, status: str = "open") -> ScoredFinding:
    return ScoredFinding(severity, confidence, status)


@pytest.mark.parametrize(
    ("findings", "loc", "expected"),
    [
        ([], 50, 100.0),
        ([f("info")], 50, 100.0),
        ([f("high"), f("medium", 0.5)], 100, 88.0),  # 100 - (10 + 2)
        ([f("low")] * 30, 600, 90.0),  # big MR: penalty 30 / size factor 3
        ([f("high")] * 12, 100, 0.0),  # clamped at 0
        ([f("critical", 0.6)], 100, 50.0),  # 100 - 15 = 85, capped by the critical rule
        ([f("critical")] * 3, 100, 25.0),  # already below the cap
        ([f("critical", status="false_positive")], 100, 100.0),  # accepted dispute
    ],
)
def test_review_score(findings: list[ScoredFinding], loc: int, expected: float) -> None:
    assert review_score(findings, loc) == expected


def test_small_mr_with_one_critical_vs_large_mr_with_many_lows() -> None:
    # docs/specs/08 "Test bắt buộc": a critical in a small MR must score far below many lows
    # spread over a big MR.
    small_critical = review_score([f("critical", 0.9)], 40)
    large_lows = review_score([f("low")] * 20, 2000)
    assert small_critical == 50.0
    assert large_lows == 98.0
    assert small_critical < large_lows


def test_weights_follow_the_rubric_unless_overridden() -> None:
    from app.scoring.formula import DEFAULT_WEIGHTS, weights_for

    assert weights_for("v1") == DEFAULT_WEIGHTS
    custom = {"critical": 40, "high": 15, "medium": 5, "low": 1, "info": 0}
    assert weights_for("v1", custom) == custom
    assert review_score([f("high")], 100, custom) == 85.0
    with pytest.raises(ValueError, match="no scoring weights"):
        weights_for("v9")
    with pytest.raises(ValueError, match="non-negative weights"):
        weights_for("v1", {"critical": 25})
