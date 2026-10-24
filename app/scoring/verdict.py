"""Merge verdict and risk score of one review (docs/specs/17).

The 0-100 score (docs/specs/08) compares people over time; the verdict answers the reviewer's
question for one change: can this be merged? Same scale as the techlead's manual process:
risk = critical x 10 + high x 3 + medium x 1; any critical blocks, any high needs a fix first.
"""

from collections.abc import Iterable, Mapping

VERDICTS = ("approve", "fix_required", "block")
VERDICT_LABEL = {"approve": "APPROVE", "fix_required": "FIX REQUIRED", "block": "BLOCK"}
RISK_WEIGHTS: Mapping[str, int] = {"critical": 10, "high": 3, "medium": 1}
NOT_COUNTED = ("false_positive",)


def risk_score(counts: Mapping[str, int]) -> int:
    return sum(RISK_WEIGHTS.get(severity, 0) * int(n) for severity, n in counts.items())


def verdict_for(counts: Mapping[str, int]) -> str:
    if counts.get("critical", 0) > 0:
        return "block"
    if counts.get("high", 0) > 0:
        return "fix_required"
    return "approve"


def count_severities(findings: Iterable[tuple[str, str | None]]) -> dict[str, int]:
    """(severity, status) pairs -> counts; findings a lead ruled false positive do not count."""
    counts: dict[str, int] = {}
    for severity, status in findings:
        if (status or "open") in NOT_COUNTED:
            continue
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def assess(findings: Iterable[tuple[str, str | None]]) -> tuple[str, int]:
    counts = count_severities(findings)
    return verdict_for(counts), risk_score(counts)
