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
