"""Merge verdict and risk score of one review (docs/specs/17; docs/specs/19 §4.4).

The 0-100 score (docs/specs/08) compares people over time; the verdict answers the reviewer's
question for one change: can this be merged? Same scale as the techlead's manual process:
risk = critical x 10 + high x 3 + medium x 1; a confirmed critical blocks, a confirmed high
needs a fix first. A critical/high finding the verification could not settle (`unverified`)
does not decide on its own: the verdict is `needs_human` and a person looks at it.

A finding with no verification record (reviews before rubric v4: `verified` = skipped) counts
as it always did, so recomputing an old review gives the old verdict.
"""

from collections.abc import Iterable, Mapping

VERDICTS = ("approve", "needs_human", "fix_required", "block")
VERDICT_LABEL = {
    "approve": "APPROVE",
    "needs_human": "CẦN NGƯỜI XEM",
    "fix_required": "FIX REQUIRED",
    "block": "BLOCK",
}
RISK_WEIGHTS: Mapping[str, int] = {"critical": 10, "high": 3, "medium": 1}
NOT_COUNTED = ("false_positive", "rejected")
UNDECIDED = "unverified"
SEVERE = ("critical", "high")

# (severity, status, verified); `verified` None or "skipped" = no verification: counts as settled.
Judged = tuple[str, str | None, str | None]
Pair = tuple[str, str | None]


def risk_score(counts: Mapping[str, int]) -> int:
    return sum(RISK_WEIGHTS.get(severity, 0) * int(n) for severity, n in counts.items())


def count_severities(findings: Iterable[Judged | Pair]) -> dict[str, int]:
    """Settled findings per severity: a lead's false positive, a rejected finding and an
    unverified one do not count (risk is computed on confirmed + downgraded, docs/specs/19)."""
    counts: dict[str, int] = {}
    for severity, status, verified in (_judged(f) for f in findings):
        if (status or "open") in NOT_COUNTED or verified == UNDECIDED:
            continue
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def verdict_for(findings: Iterable[Judged | Pair]) -> str:
    """docs/specs/19 §4.4: block on a confirmed critical, fix_required on a confirmed high,
    needs_human when a critical/high is unverified and nothing confirmed decides already."""
    judged = [_judged(f) for f in findings]
    settled = [s for s, st, v in judged if (st or "open") not in NOT_COUNTED and v != UNDECIDED]
    if "critical" in settled:
        return "block"
    if "high" in settled:
        return "fix_required"
    undecided = [s for s, st, v in judged if (st or "open") not in NOT_COUNTED and v == UNDECIDED]
    if any(s in SEVERE for s in undecided):
        return "needs_human"
    return "approve"


def needs_human(findings: Iterable[Judged | Pair]) -> bool:
    return verdict_for(findings) == "needs_human"


def assess(findings: Iterable[Judged | Pair]) -> tuple[str, int]:
    judged = [_judged(f) for f in findings]
    return verdict_for(judged), risk_score(count_severities(judged))


def _judged(f: Judged | Pair) -> Judged:
    if len(f) == 2:
        return (f[0], f[1], None)
    return (f[0], f[1], f[2])
