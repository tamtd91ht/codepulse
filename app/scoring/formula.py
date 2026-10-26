"""Review score 0-100 (docs/specs/08 §1). The LLM's own score is never used as the result."""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "critical": 25,
    "high": 10,
    "medium": 4,
    "low": 1,
    "info": 0,
}
# v3 (2026-10-26): v2 scored a 1000-LOC change with a high finding about 97 while its verdict
# said FIX REQUIRED; heavier weights, a sub-linear size factor and a high cap fix that.
WEIGHTS_V3: Mapping[str, float] = {
    "critical": 30,
    "high": 15,
    "medium": 5,
    "low": 1.5,
    "info": 0,
}
# Weights are versioned with the rubric (docs/specs/08): a score is only comparable with scores
# computed under the same rubric_version.
WEIGHTS_BY_RUBRIC: Mapping[str, Mapping[str, float]] = {
    "v1": DEFAULT_WEIGHTS,
    "v2": DEFAULT_WEIGHTS,
    "v3": WEIGHTS_V3,
}
SEVERITIES = frozenset(DEFAULT_WEIGHTS)

# Phase 0 decision: any critical finding caps the review at 50, however large the MR is.
CRITICAL_SCORE_CAP = 50.0
# From v3: a high finding (verdict FIX REQUIRED) caps the review at 80.
HIGH_SCORE_CAP = 80.0
LOC_PER_SIZE_UNIT = 200


@dataclass(frozen=True)
class SizeRule:
    """How a rubric version spreads the penalty over the size of the change."""

    sqrt_size: bool  # v3: sqrt(loc / 200); before: loc / 200, which forgave large changes
    high_cap: float | None


SIZE_RULES: Mapping[str, SizeRule] = {
    "v1": SizeRule(sqrt_size=False, high_cap=None),
    "v2": SizeRule(sqrt_size=False, high_cap=None),
    "v3": SizeRule(sqrt_size=True, high_cap=HIGH_SCORE_CAP),
}


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
    rubric_version: str | None = None,
) -> float:
    """`rubric_version` picks the size rule the review was scored under (None: v1)."""
    rule = SIZE_RULES.get(rubric_version or "v1", SIZE_RULES["v1"])
    counted = [f for f in findings if f.status != "false_positive"]
    units = loc_reviewed / LOC_PER_SIZE_UNIT
    size_factor = max(1.0, math.sqrt(units) if rule.sqrt_size else units)
    score = min(100.0, max(0.0, 100.0 - penalty(counted, weights) / size_factor))
    if any(f.severity == "critical" for f in counted):
        score = min(score, CRITICAL_SCORE_CAP)
    if rule.high_cap is not None and any(f.severity == "high" for f in counted):
        score = min(score, rule.high_cap)
    return round(score, 2)


QI_WEIGHTS = {"score": 0.50, "density": 0.20, "build": 0.15, "fix": 0.15}


def quality_index(
    avg_score: float | None,
    defect_density: float | None,
    build_success_rate: float | None,
    fix_rate: float | None,
) -> float | None:
    """docs/specs/08 §3. A missing component (no pipelines, nothing severe to fix) is left out
    and the remaining weights are rescaled, instead of counting it as 0."""
    if avg_score is None:
        return None
    parts = {
        "score": avg_score,
        "density": 100 - min(100.0, (defect_density or 0) * 10),
        "build": build_success_rate,
        "fix": fix_rate * 100 if fix_rate is not None else None,
    }
    present = {k: v for k, v in parts.items() if v is not None}
    total = sum(QI_WEIGHTS[k] for k in present)
    return round(sum(QI_WEIGHTS[k] * v for k, v in present.items()) / total, 2)
