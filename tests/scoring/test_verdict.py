from typing import Any

import pytest

from app.scoring.verdict import (
    VERDICT_LABEL,
    VERDICTS,
    assess,
    count_severities,
    needs_human,
    risk_score,
    verdict_for,
)


def test_risk_and_verdict_follow_the_techlead_scale() -> None:
    # verdict_for takes (severity, status, verified) tuples since docs/specs/19 §4.4.
    assert risk_score({"critical": 1, "high": 2, "medium": 3, "low": 9}) == 10 + 6 + 3
    assert verdict_for([("critical", "open", "confirmed")]) == "block"
    assert verdict_for([("high", "open", "confirmed"), ("medium", "open", "confirmed")]) == (
        "fix_required"
    )
    assert verdict_for([("medium", "open", "confirmed"), ("low", "open", "skipped")]) == "approve"
    assert verdict_for([]) == "approve"


@pytest.mark.parametrize(
    ("findings", "verdict", "risk"),
    [
        # unverified critical/high: a person decides; they carry no risk until confirmed
        ([("critical", "open", "unverified")], "needs_human", 0),
        ([("high", "open", "unverified"), ("medium", "open", "confirmed")], "needs_human", 1),
        # a confirmed high decides even when a critical is unverified
        ([("critical", "open", "unverified"), ("high", "open", "confirmed")], "fix_required", 3),
        # a downgraded finding counts at its new severity
        ([("high", "open", "downgraded")], "fix_required", 3),
        ([("medium", "open", "downgraded"), ("medium", "open", "unverified")], "approve", 1),
        # rejected never counts; a false positive never counts
        ([("critical", "rejected", "rejected")], "approve", 0),
        ([("critical", "false_positive", "confirmed")], "approve", 0),
        # reviews before v4 (no verification record) keep the old verdict
        ([("critical", "open", "skipped")], "block", 10),
        ([("critical", "open", None)], "block", 10),
        ([("critical", "open")], "block", 10),
    ],
)
def test_needs_human_and_verified_counting(
    findings: list[tuple[Any, ...]], verdict: str, risk: int
) -> None:
    assert assess(findings) == (verdict, risk)
    assert needs_human(findings) == (verdict == "needs_human")


def test_needs_human_is_a_verdict_with_a_label() -> None:
    assert "needs_human" in VERDICTS and VERDICT_LABEL["needs_human"] == "CẦN NGƯỜI XEM"


def test_false_positives_do_not_count() -> None:
    findings = [
        ("critical", "false_positive"),
        ("high", "open"),
        ("medium", None),
        ("low", "fixed"),
    ]
    assert count_severities(findings) == {"high": 1, "medium": 1, "low": 1}
    assert assess(findings) == ("fix_required", 4)
    assert assess([("critical", "false_positive")]) == ("approve", 0)
