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
# Weights are versioned with the rubric (docs/specs/08): a score is only comparable with scores
# computed under the same rubric_version.
WEIGHTS_BY_RUBRIC: Mapping[str, Mapping[str, float]] = {"v1": DEFAULT_WEIGHTS}
SEVERITIES = frozenset(DEFAULT_WEIGHTS)

# Phase 0 decision: any critical finding caps the review at 50, however large the MR is.
CRITICAL_SCORE_CAP = 50.0
LOC_PER_SIZE_UNIT = 200


@dataclass(frozen=True)
class ScoredFinding:
    severity: str
    confidence: float
    status: str = "open"


def weights_for(
    rubric_version: str | None, override: Mapping[str, float] | None = None
) -> Mapping[str, float]:
    """SCORING_WEIGHTS overrides the rubric's weights; changing it requires a metrics recompute."""
    if override:
        if set(override) != SEVERITIES or any(v < 0 for v in override.values()):
            raise ValueError(f"SCORING_WEIGHTS needs non-negative weights for {sorted(SEVERITIES)}")
        return override
    try:
        return WEIGHTS_BY_RUBRIC[rubric_version or "v1"]
    except KeyError:
        raise ValueError(f"no scoring weights for rubric {rubric_version!r}") from None


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
