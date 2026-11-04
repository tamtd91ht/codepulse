"""The DPI rubric and profiles (docs/specs/25 §1): what is scored, out of how much, and how the
five groups weigh for each level. The defaults are seeded by migration e7b9d1f3a5c7 (a literal
copy, compared by tests/dpi/test_rubric.py)."""

from dataclasses import dataclass
from typing import Any

GROUPS = ("A", "B", "C", "D", "E")
GROUP_LABEL = {
    "A": "Work Output",
    "B": "Code Quality",
    "C": "Delivery",
    "D": "Collaboration",
    "E": "Ownership & Growth",
}
MANAGER_METRICS = ("A3", "B5", "D3", "E1", "E2", "E3")
COMPLEXITY_POINTS = {"XS": 1, "S": 2, "M": 3, "L": 5, "XL": 8}
PROD_PENALTY = {"P1": 4, "P2": 2, "P3": 1}
WORK_DAYS = 20  # a month's working days, for the A1 target

DEFAULT_VERSION = "dpi-v1"
DEFAULT_METRICS: list[dict[str, Any]] = [
    {
        "key": "A1",
        "group": "A",
        "max": 15,
        "source": "auto",
        "label": "Complexity-adjusted delivery",
    },
    {"key": "A2", "group": "A", "max": 5, "source": "auto", "label": "Completed contributions"},
    {
        "key": "A3",
        "group": "A",
        "max": 5,
        "source": "manager",
        "label": "Business/Technical impact",
    },
    {"key": "B1", "group": "B", "max": 8, "source": "auto", "label": "Quality Gate Compliance"},
    {"key": "B2", "group": "B", "max": 8, "source": "auto", "label": "Escaped Defects"},
    {"key": "B3", "group": "B", "max": 5, "source": "auto", "label": "Automated Test Adequacy"},
    {"key": "B4", "group": "B", "max": 4, "source": "auto", "label": "Maintainability & Rework"},
    {"key": "B5", "group": "B", "max": 5, "source": "manager", "label": "Code Design Quality"},
    {"key": "C1", "group": "C", "max": 5, "source": "auto", "label": "Development Flow Efficiency"},
    {"key": "C2", "group": "C", "max": 6, "source": "auto", "label": "Delivery Predictability"},
    {"key": "C3", "group": "C", "max": 4, "source": "auto", "label": "Process Compliance"},
    {
        "key": "D1",
        "group": "D",
        "max": 6,
        "source": "auto",
        "label": "Meaningful Review Contributions",
    },
    {"key": "D2", "group": "D", "max": 4, "source": "auto", "label": "Knowledge Sharing"},
    {"key": "D3", "group": "D", "max": 5, "source": "manager", "label": "Teamwork Quality"},
    {
        "key": "E1",
        "group": "E",
        "max": 5,
        "source": "manager",
        "label": "Ownership & accountability",
    },
    {"key": "E2", "group": "E", "max": 5, "source": "manager", "label": "Learning & growth"},
    {"key": "E3", "group": "E", "max": 5, "source": "manager", "label": "Initiative & mentoring"},
]

MIDDLE = {"A": 25, "B": 30, "C": 15, "D": 15, "E": 15}
DEFAULT_PROFILES: list[dict[str, Any]] = [
    {
        "key": "junior",
        "label": "Junior",
        "weights": {"A": 30, "B": 35, "C": 20, "D": 10, "E": 5},
        "a1_target": 20,
    },
    {"key": "middle", "label": "Middle", "weights": MIDDLE, "a1_target": 28},
    {
        "key": "senior",
        "label": "Senior",
        "weights": {"A": 20, "B": 30, "C": 15, "D": 15, "E": 20},
        "a1_target": 32,
    },
    {
        "key": "tech_lead",
        "label": "Tech Lead",
        "weights": {"A": 15, "B": 25, "C": 15, "D": 20, "E": 25},
        "a1_target": 20,
    },
    {"key": "devops_sre", "label": "DevOps / SRE", "weights": MIDDLE, "a1_target": 28},
    {"key": "qa_automation", "label": "QA Automation", "weights": MIDDLE, "a1_target": 28},
    {"key": "ai_ml", "label": "AI / ML", "weights": MIDDLE, "a1_target": 28},
]
DEFAULT_PROFILE = "middle"

RATINGS = (
    (90.0, "excellent", "Xuất sắc"),
    (80.0, "good", "Tốt"),
    (70.0, "meets", "Đạt"),
    (60.0, "improve", "Cần cải thiện"),
    (0.0, "below", "Không đạt kỳ vọng"),
)


def rating(total: float | None) -> str | None:
    if total is None:
        return None
    for floor, key, _label in RATINGS:
        if total >= floor:
            return key
    return "below"


@dataclass(frozen=True)
class Metric:
    key: str
    group: str
    max: float
    source: str
    label: str


@dataclass(frozen=True)
class Rubric:
    version: str
    metrics: tuple[Metric, ...]

    @property
    def auto(self) -> tuple[Metric, ...]:
        return tuple(m for m in self.metrics if m.source == "auto")

    def by_key(self) -> dict[str, Metric]:
        return {m.key: m for m in self.metrics}

    def group(self, g: str) -> tuple[Metric, ...]:
        return tuple(m for m in self.metrics if m.group == g)


def as_rubric(version: str, metrics: list[dict[str, Any]]) -> Rubric:
    return Rubric(
        version,
        tuple(
            Metric(
                str(m["key"]),
                str(m["group"]),
                float(m["max"]),
                str(m.get("source") or "auto"),
                str(m.get("label") or m["key"]),
            )
            for m in metrics
        ),
    )


DEFAULT_RUBRIC = as_rubric(DEFAULT_VERSION, DEFAULT_METRICS)


def validate_metrics(metrics: list[dict[str, Any]]) -> list[str]:
    """Problems of a rubric an admin saves (an empty list: fine)."""
    problems: list[str] = []
    keys = [str(m.get("key")) for m in metrics]
    if len(set(keys)) != len(keys):
        problems.append("mã chỉ số trùng nhau")
    for m in metrics:
        if m.get("group") not in GROUPS:
            problems.append(f"{m.get('key')}: nhóm phải là A-E")
        try:
            if float(m.get("max", 0)) <= 0:
                problems.append(f"{m.get('key')}: max phải > 0")
        except (TypeError, ValueError):
            problems.append(f"{m.get('key')}: max không hợp lệ")
        if m.get("source") not in ("auto", "manager"):
            problems.append(f"{m.get('key')}: source phải là auto hoặc manager")
        if m.get("source") == "manager" and m.get("key") not in MANAGER_METRICS:
            problems.append(f"{m.get('key')}: chỉ số quản lý phải là một trong {MANAGER_METRICS}")
    for g in GROUPS:
        if not any(m.get("group") == g for m in metrics):
            problems.append(f"nhóm {g} không có chỉ số")
    return problems


def validate_weights(weights: dict[str, Any]) -> list[str]:
    try:
        values = {g: float(weights.get(g, 0)) for g in GROUPS}
    except (TypeError, ValueError):
        return ["trọng số phải là số"]
    if any(v < 0 for v in values.values()):
        return ["trọng số không được âm"]
    if abs(sum(values.values()) - 100) > 0.01:
        return [f"tổng trọng số phải là 100 (đang {sum(values.values()):g})"]
    return []
