import pytest

from app.scoring.formula import ScoredFinding, review_score


def f(
    severity: str, confidence: float = 1.0, status: str = "open", verified: str = "skipped"
) -> ScoredFinding:
    return ScoredFinding(severity, confidence, status, verified)


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


def test_quality_index_matches_the_spec_formula() -> None:
    from app.scoring.formula import quality_index

    # 0.5*80 + 0.2*(100 - 30) + 0.15*90 + 0.15*50 = 40 + 14 + 13.5 + 7.5
    assert quality_index(80, 3.0, 90, 0.5) == 75.0
    assert quality_index(80, 50.0, 90, 0.5) == 61.0  # density term floors at 0


def test_quality_index_rescales_missing_components() -> None:
    from app.scoring.formula import quality_index

    # No pipelines and nothing severe to fix: (0.5*80 + 0.2*70) / 0.7
    assert quality_index(80, 3.0, None, None) == 77.14
    assert quality_index(None, 3.0, 90, 0.5) is None


H, M, L = ScoredFinding("high", 0.8), ScoredFinding("medium", 0.7), ScoredFinding("low", 0.6)


@pytest.mark.parametrize(
    ("findings", "loc", "v2", "v3"),
    [
        ([H, M, M, L, L, L], 400, 92.3, 80.0),  # a high finding caps v3 at 80
        ([H, M, M, L, L, L], 1000, 96.92, 80.0),
        ([H, H, H], 1500, 96.8, 80.0),
        ([M] * 5, 800, 96.5, 91.25),  # sqrt(4) = 2 instead of 4
        ([L] * 6, 300, 97.6, 95.59),
        ([], 5000, 100.0, 100.0),
    ],
)
def test_v3_no_longer_forgives_large_changes(
    findings: list[ScoredFinding], loc: int, v2: float, v3: float
) -> None:
    from app.scoring.formula import weights_for

    assert review_score(findings, loc, weights_for("v2"), "v2") == v2
    assert review_score(findings, loc, weights_for("v3"), "v3") == v3


def test_v3_keeps_the_critical_cap_and_ignores_false_positives() -> None:
    from app.scoring.formula import weights_for

    critical = ScoredFinding("critical", 0.5)
    assert review_score([critical], 10_000, weights_for("v3"), "v3") == 50.0
    disputed = ScoredFinding("high", 0.9, "false_positive")
    assert review_score([disputed], 100, weights_for("v3"), "v3") == 100.0


V4 = [
    # docs/specs/19 §4.4: only confirmed / downgraded / skipped / confident unverified count
    ([f("critical", 0.9, verified="confirmed")], 100, 50.0),  # 73, capped: confirmed critical
    ([f("critical", 0.9, verified="unverified")], 100, 73.0),  # counted, but no cap
    ([f("critical", 0.6, verified="unverified")], 100, 100.0),  # below 0.7: not counted
    ([f("high", 0.8, verified="confirmed")], 100, 80.0),  # 88, capped: confirmed high
    ([f("high", 0.8, verified="unverified")], 100, 88.0),
    ([f("medium", 0.8, verified="downgraded")], 100, 96.0),
    ([f("low", 0.5, verified="skipped")], 100, 99.25),
    # Verification not configured (docs/specs/19 §4.3 "skipped"): as reported, so capped.
    ([f("critical", 0.9, verified="skipped")], 100, 50.0),
    ([f("high", 0.8, verified="skipped")], 100, 80.0),
    ([f("critical", 0.9, verified="downgraded")], 100, 73.0),  # a downgrade never caps
    ([f("critical", 0.9, status="rejected", verified="rejected")], 100, 100.0),
    ([f("critical", 0.9, status="false_positive", verified="confirmed")], 100, 100.0),
    ([f("high", 0.8, verified="confirmed"), f("critical", 0.9, verified="unverified")], 100, 61.0),
]


@pytest.mark.parametrize(("findings", "loc", "expected"), V4)
def test_v4_scores_only_verified_findings(
    findings: list[ScoredFinding], loc: int, expected: float
) -> None:
    from app.scoring.formula import weights_for

    assert weights_for("v4") == weights_for("v3")
    assert review_score(findings, loc, weights_for("v4"), "v4") == expected


def test_v4_threshold_for_unverified_findings_is_configurable() -> None:
    from app.scoring.formula import weights_for

    unsure = [f("critical", 0.6, verified="unverified")]
    assert review_score(unsure, 100, weights_for("v4"), "v4", min_confidence_unverified=0.5) == 82.0


def test_older_rubrics_ignore_the_verification_column() -> None:
    from app.scoring.formula import weights_for

    unverified = [f("critical", 0.5, verified="unverified")]
    assert review_score(unverified, 10_000, weights_for("v3"), "v3") == 50.0
    assert review_score(unverified, 100, weights_for("v1"), "v1") == 50.0
    rejected = [f("high", 0.9, status="rejected", verified="rejected")]
    assert review_score(rejected, 100, weights_for("v3"), "v3") == 100.0
