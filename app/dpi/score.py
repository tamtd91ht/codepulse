"""From metric values to a scorecard (docs/specs/25 §1): groups weighted by the profile, the
total, the coverage, the confidence, the rating; the rolling score over three months."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.dpi.metrics import Result
from app.dpi.rubric import GROUP_LABEL, GROUPS, RATINGS, Rubric, rating

HIGH_COVERAGE, MEDIUM_COVERAGE = 0.8, 0.6
ROLLING = (0.5, 0.3, 0.2)


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    weights: dict[str, float]
    a1_target: float


def _overall(coverage: float, metrics: dict[str, dict[str, Any]]) -> str:
    heavy_low = any(
        m["confidence"] == "low" and m["value"] is not None and m["max"] >= 6
        for m in metrics.values()
    )
    if coverage >= HIGH_COVERAGE and not heavy_low:
        return "high"
    if coverage >= MEDIUM_COVERAGE:
        return "medium"
    return "low"


def scorecard(
    rubric: Rubric,
    profile: Profile,
    auto: dict[str, Result],
    manager: dict[str, float],
    overrides: dict[str, float] | None = None,
    adjustment: float = 0.0,
) -> dict[str, Any]:
    """`manager`: metric -> points given by the lead (0..max); `overrides`: metric -> v set by a
    dispute; `adjustment`: calibration points added to the total (clamped 0..100)."""
    overrides = overrides or {}
    metrics: dict[str, dict[str, Any]] = {}
    for m in rubric.metrics:
        if m.source == "manager":
            given = manager.get(m.key)
            value = None if given is None else max(0.0, min(1.0, given / m.max))
            entry: dict[str, Any] = {
                "value": value,
                "confidence": "high" if value is not None else "low",
                "detail": {},
                "evidence": [],
            }
        else:
            result = auto.get(m.key)
            entry = (
                result.as_dict()
                if result is not None
                else {"value": None, "confidence": "low", "detail": {}, "evidence": []}
            )
        if m.key in overrides:
            entry = {**entry, "value": overrides[m.key], "overridden": True}
        entry.update({"group": m.group, "max": m.max, "source": m.source, "label": m.label})
        entry["points"] = None if entry["value"] is None else round(entry["value"] * m.max, 2)
        metrics[m.key] = entry

    groups: dict[str, dict[str, Any]] = {}
    total_weight = sum(float(profile.weights.get(g, 0)) for g in GROUPS) or 100.0
    earned = 0.0
    weight_with_data = 0.0
    covered = 0.0
    auto_points = auto_max = manager_points = manager_max = 0.0
    for g in GROUPS:
        group_metrics = [m for m in rubric.group(g)]
        weight = float(profile.weights.get(g, 0))
        have = [m for m in group_metrics if metrics[m.key]["value"] is not None]
        all_max = sum(m.max for m in group_metrics) or 1.0
        have_max = sum(m.max for m in have)
        score = None
        if have:
            ratio = sum(metrics[m.key]["value"] * m.max for m in have) / have_max
            score = ratio * weight
            earned += score
            weight_with_data += weight
        covered += weight * have_max / all_max
        for m in group_metrics:
            value = metrics[m.key]["value"]
            # Points on the 100 scale of this profile, for the auto / manager split.
            scaled = weight * m.max / all_max
            if m.source == "manager":
                manager_max += scaled
                manager_points += (value or 0.0) * scaled
            else:
                auto_max += scaled
                auto_points += (value or 0.0) * scaled if value is not None else 0.0
        groups[g] = {
            "label": GROUP_LABEL[g],
            "weight": weight,
            "score": None if score is None else round(score, 2),
            "ratio": None if score is None or not weight else round(score / weight, 4),
            "metrics": [m.key for m in group_metrics],
        }
    coverage = covered / total_weight
    total = None if not weight_with_data else earned / weight_with_data * 100.0
    if total is not None and adjustment:
        total = max(0.0, min(100.0, total + adjustment))
    provisional = any(
        metrics[m.key]["value"] is None for m in rubric.metrics if m.source == "manager"
    )
    confidence = _overall(coverage, metrics)
    rounded = None if total is None else round(total, 2)
    return {
        "rubric_version": rubric.version,
        "profile": profile.key,
        "profile_label": profile.label,
        "metrics": metrics,
        "groups": groups,
        "total": rounded,
        "coverage": round(coverage, 4),
        "confidence": confidence,
        "provisional": provisional,
        "adjustment": adjustment,
        # "Chưa đủ dữ liệu": no rating on a low confidence (§1).
        "rating": None if confidence == "low" else rating(rounded),
        "split": {
            "auto": round(auto_points, 2),
            "auto_max": round(auto_max, 2),
            "manager": round(manager_points, 2),
            "manager_max": round(manager_max, 2),
        },
    }


def rolling(scores: list[float | None]) -> dict[str, Any]:
    """R = 0.5·S0 + 0.3·S-1 + 0.2·S-2 (`scores`: [S0, S-1, S-2]); the weights of missing months
    are spread over the others, flagged."""
    pairs = [(w, s) for w, s in zip(ROLLING, scores, strict=False) if s is not None]
    if not pairs or scores[0] is None:
        return {"value": None, "months": len(pairs), "partial": True}
    weight = sum(w for w, _ in pairs)
    value = sum(w * s for w, s in pairs) / weight
    return {
        "value": round(value, 2),
        "months": len(pairs),
        "partial": len(pairs) < len(ROLLING),
        "rating": rating(value),
    }


def inputs_hash(payload: Any) -> str:
    """The same inputs always give the same hash (§6.1: a recompute of an old month with the
    same rubric version gives the same result)."""
    text = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


RATING_LABEL = {key: label for _floor, key, label in RATINGS}
