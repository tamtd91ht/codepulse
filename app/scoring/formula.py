"""Review score 0-100 (docs/specs/08 §1). The LLM's own score is never used as the result."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "critical": 25,
    "high": 10,
    "medium": 4,
    "low": 1,
    "info": 0,
}
# Phase 0 decision: any critical finding caps the review at 50, however large the MR is.
CRITICAL_SCORE_CAP = 50.0
LOC_PER_SIZE_UNIT = 200


@dataclass(frozen=True)
class ScoredFinding:
    severity: str
    confidence: float
    status: str = "open"


def penalty(findings: Iterable[ScoredFinding], weights: Mapping[str, float]) -> float:
    return sum(
        weights.get(f.severity, 0) * f.confidence for f in findings if f.status != "false_positive"
    )


def review_score(
    findings: Iterable[ScoredFinding],
    loc_reviewed: int,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> float:
    counted = [f for f in findings if f.status != "false_positive"]
    size_factor = max(1.0, loc_reviewed / LOC_PER_SIZE_UNIT)
    score = min(100.0, max(0.0, 100.0 - penalty(counted, weights) / size_factor))
    if any(f.severity == "critical" for f in counted):
        score = min(score, CRITICAL_SCORE_CAP)
    return round(score, 2)
