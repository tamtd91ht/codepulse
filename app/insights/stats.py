"""Small statistics helpers shared by the insights modules (docs/specs/20 P6: median and p75)."""

import math
from collections.abc import Iterable
from typing import Any


def quantile(values: Iterable[float | None], q: float) -> float | None:
    """The q-quantile of the non-null values, linear between closest ranks (numpy's default);
    None when there are none."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def spread(values: Iterable[float | None], min_sample: int) -> dict[str, Any]:
    """{n, median, p75} rounded to 0.1; median and p75 stay None below `min_sample` values (§7:
    too few samples show no median)."""
    xs = [v for v in values if v is not None]
    if len(xs) < min_sample:
        return {"n": len(xs), "median": None, "p75": None}
    median, p75 = quantile(xs, 0.5), quantile(xs, 0.75)
    return {
        "n": len(xs),
        "median": round(median, 1) if median is not None else None,
        "p75": round(p75, 1) if p75 is not None else None,
    }


def share(part: int, whole: int) -> float | None:
    """part / whole as a percentage rounded to 0.1, None when whole is 0."""
    return round(100.0 * part / whole, 1) if whole else None
