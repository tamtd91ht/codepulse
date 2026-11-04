"""GitLab label conventions of the DPI (docs/specs/25 §9): what an issue's labels say."""

from typing import Any

from app.dpi.rubric import COMPLEXITY_POINTS

TYPES = ("feature", "bug", "refactor", "chore", "docs", "incident", "automation")
SEVERITIES = ("P1", "P2", "P3")
# name -> (color, description): what "Tạo label chuẩn" creates on a repo.
STANDARD: dict[str, tuple[str, str]] = {
    **{
        f"complexity::{size}": ("#6c63ff", f"Độ phức tạp {size} = {points} điểm (DPI A1)")
        for size, points in COMPLEXITY_POINTS.items()
    },
    **{f"type::{t}": ("#00b1ff", f"Loại việc: {t} (DPI A2)") for t in TYPES},
    **{f"prod-bug::{s}": ("#ff5955", f"Lỗi production mức {s} (DPI B2)") for s in SEVERITIES},
    "blocked": ("#fea220", "Bị chặn, không tính hạn (DPI C2)"),
}


def _scoped(labels: list[str], scope: str) -> str | None:
    """The value of a scoped label `scope::value` (GitLab keeps one per scope; the last wins)."""
    found = None
    prefix = f"{scope}::"
    for label in labels:
        if label.lower().startswith(prefix):
            found = label[len(prefix) :].strip()
    return found


def label_fields(labels: list[str]) -> dict[str, Any]:
    """type, complexity (+ points), prod_severity of an issue, from its labels."""
    kind = (_scoped(labels, "type") or "").lower() or None
    size = (_scoped(labels, "complexity") or "").upper() or None
    severity = (_scoped(labels, "prod-bug") or "").upper() or None
    return {
        "type": kind,
        "complexity": size if size in COMPLEXITY_POINTS else None,
        "complexity_points": COMPLEXITY_POINTS.get(size or ""),
        "prod_severity": severity if severity in SEVERITIES else None,
    }


def blocked(labels: list[str] | None) -> bool:
    return any(label.lower() == "blocked" for label in labels or [])
