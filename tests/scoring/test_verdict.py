from app.scoring.verdict import assess, count_severities, risk_score, verdict_for


def test_risk_and_verdict_follow_the_techlead_scale() -> None:
    assert risk_score({"critical": 1, "high": 2, "medium": 3, "low": 9}) == 10 + 6 + 3
    assert verdict_for({"critical": 1}) == "block"
    assert verdict_for({"high": 1, "medium": 4}) == "fix_required"
    assert verdict_for({"medium": 4, "low": 2, "info": 1}) == "approve"
    assert verdict_for({}) == "approve"


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
