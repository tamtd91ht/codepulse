"""The automatic DPI metrics (docs/specs/25 §2): pure functions of one person's month.

Each returns a `Result`: `value` in [0, 1] (None: no data, the metric is left out and lowers
the coverage), a confidence, what it was computed from (`detail`) and the evidence to open
(`evidence`: issues, MRs, pipelines, defects...). Nothing here reads the database.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import median
from typing import Any

from app.dpi.rubric import PROD_PENALTY, WORK_DAYS

COVERAGE_TARGET = 80.0
TESTING_PER_100_LOC = 1.0  # testing findings per 100 LOC at which the test part is 0
REWORK_GOOD, REWORK_BAD = 0.10, 0.40
AUTHOR_TIME_WORST = 2.5  # the person's median author time / the repo's, at which C1 is 0
D1_CAP = 10
D2_TARGET = 6.0
SPLIT_MRS = 3  # MRs merged for one issue in one day by one person: a split worth a look
MEANINGFUL = frozenset({"actionable", "design", "correctness", "security", "testability"})


@dataclass(frozen=True)
class IssueFact:
    id: int
    iid: int
    project_id: int
    title: str | None
    web_url: str | None
    type: str | None
    points: int | None
    assignees: int
    due_date: date | None
    state: str | None
    closed_at: datetime | None
    blocked: bool = False


@dataclass(frozen=True)
class MrFact:
    id: int
    iid: int
    project_id: int
    title: str | None
    web_url: str | None
    merged_at: datetime | None
    author_hours: float | None
    issue_ids: tuple[int, ...] = ()
    tasks_total: int = 0
    tasks_done: int = 0
    pipeline: str | None = None  # status of the pipeline on its head
    coverage: float | None = None
    reviewed: bool = False
    refactor: bool = False
    ci: bool = True  # the repo runs pipelines at all


@dataclass(frozen=True)
class CommentFact:
    note_id: int
    mr_id: int
    mr_iid: int
    project_id: int
    label: str | None
    acted_on: bool
    confirmed: bool
    web_url: str | None = None


@dataclass(frozen=True)
class DefectFact:
    id: int
    issue_id: int
    severity: str
    share: float
    title: str | None
    web_url: str | None


@dataclass(frozen=True)
class ItemFact:
    """A self-review item (the monthly evidence pack, spec 20 §5.4); confirmed by the lead."""

    id: int
    kind: str
    title: str
    confirmed: bool


@dataclass
class Facts:
    user_id: int
    month: date
    a1_target: float
    leave_days: float = 0.0
    issues_closed: list[IssueFact] = field(default_factory=list)
    issues_due: list[IssueFact] = field(default_factory=list)
    issues_closed_all: int = 0  # closed issues assigned to them, with or without complexity
    review_count: int = 0
    review_avg: float | None = None  # criteria (primary set) where it exists, else review score
    severe_reviews: int = 0  # reviews with a counted critical / high
    testing_findings: int = 0
    review_loc: int = 0
    pipelines_success: int = 0
    pipelines_failed: int = 0
    mrs: list[MrFact] = field(default_factory=list)
    direct_commits: list[dict[str, Any]] = field(default_factory=list)
    added_lines: int = 0
    reworked_lines: int = 0
    rework_files: list[dict[str, Any]] = field(default_factory=list)
    # §4.2 contribution kinds of the lines added (new_work, refactor, help_others) and churn
    # (reworked within 21 days): shown, not scored.
    contribution: dict[str, int] = field(default_factory=dict)
    repo_author_median: dict[int, float] = field(default_factory=dict)
    comments: list[CommentFact] = field(default_factory=list)
    reviewed_mrs: int = 0  # others' MRs they responded to
    outside_reviews: list[dict[str, Any]] = field(default_factory=list)
    items: list[ItemFact] = field(default_factory=list)
    defects: list[DefectFact] = field(default_factory=list)


@dataclass
class Result:
    key: str
    value: float | None
    confidence: str  # high|medium|low
    detail: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": None if self.value is None else round(self.value, 4),
            "confidence": self.confidence,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _linear(x: float, good: float, bad: float) -> float:
    """1 at `good` or better, 0 at `bad` or worse (either direction)."""
    if good == bad:
        return 1.0 if x <= good else 0.0
    return _clamp((bad - x) / (bad - good)) if bad > good else _clamp((x - bad) / (good - bad))


def _issue(i: IssueFact) -> dict[str, Any]:
    return {"kind": "issue", "id": i.id, "ref": f"#{i.iid}", "label": i.title, "url": i.web_url}


def _mr(m: MrFact) -> dict[str, Any]:
    return {"kind": "mr", "id": m.id, "ref": f"!{m.iid}", "label": m.title, "url": m.web_url}


def a1(f: Facts) -> Result:
    pointed = [i for i in f.issues_closed if i.points]
    points = sum((i.points or 0) / max(i.assignees, 1) for i in pointed)
    available = max(0.0, WORK_DAYS - f.leave_days)
    target = f.a1_target * available / WORK_DAYS
    detail = {
        "points": round(points, 2),
        "target": round(target, 2),
        "issues_with_complexity": len(pointed),
        "issues_closed": f.issues_closed_all,
        "leave_days": f.leave_days,
    }
    if not pointed or target <= 0:
        return Result("A1", None, "low", detail)
    covered = len(pointed) / f.issues_closed_all if f.issues_closed_all else 1.0
    confidence = "low" if len(pointed) < 2 or covered < 0.5 else "high"
    return Result("A1", _clamp(points / target), confidence, detail, [_issue(i) for i in pointed])


def a2(f: Facts) -> Result:
    typed = [i for i in f.issues_closed if i.type]
    confirmed = [i for i in f.items if i.confirmed]
    n = len({i.id for i in typed}) + len(confirmed)
    value = 0.0 if n == 0 else 0.4 if n <= 2 else 0.7 if n <= 5 else 1.0
    by_type: dict[str, int] = {}
    for i in typed:
        by_type[str(i.type)] = by_type.get(str(i.type), 0) + 1
    for item in confirmed:
        by_type[item.kind] = by_type.get(item.kind, 0) + 1
    confidence = "medium" if not typed and confirmed else "high" if typed else "low"
    evidence = [_issue(i) for i in typed] + [
        {"kind": "self", "id": item.id, "label": item.title, "ref": item.kind} for item in confirmed
    ]
    if n == 0 and not f.issues_closed_all and not f.items:
        return Result("A2", None, "low", {"contributions": 0})
    return Result("A2", value, confidence, {"contributions": n, "by_type": by_type}, evidence)


def b1(f: Facts) -> Result:
    parts: list[tuple[float, float]] = []  # (weight, value)
    detail: dict[str, Any] = {"reviews": f.review_count}
    if f.review_count and f.review_avg is not None:
        parts.append((0.5, _clamp(f.review_avg / 100.0)))
        detail["review_avg"] = round(f.review_avg, 1)
        gate = 1.0 - f.severe_reviews / f.review_count
        parts.append((0.25, gate))
        detail["quality_gate_pass_rate"] = round(100 * gate, 1)
    finished = f.pipelines_success + f.pipelines_failed
    if finished:
        rate = f.pipelines_success / finished
        parts.append((0.25, rate))
        detail["pipeline_pass_rate"] = round(100 * rate, 1)
        detail["pipelines"] = finished
    if not parts:
        return Result("B1", None, "low", detail)
    value = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    confidence = "low" if f.review_count < 3 else "high" if finished else "medium"
    return Result("B1", value, confidence, detail)


def b2(f: Facts) -> Result:
    active = f.review_count or f.mrs or f.direct_commits or f.added_lines or f.defects
    if not active:
        return Result("B2", None, "low", {"defects": 0})  # no work this month: no free points
    penalty = sum(PROD_PENALTY.get(d.severity, 0) * d.share for d in f.defects)
    evidence = [
        {
            "kind": "defect",
            "id": d.id,
            "ref": d.severity,
            "label": d.title,
            "url": d.web_url,
            "share": d.share,
        }
        for d in f.defects
    ]
    return Result(
        "B2",
        _clamp(1.0 - penalty / 8.0),
        "high",
        {"defects": len(f.defects), "penalty": round(penalty, 2)},
        evidence,
    )


def b3(f: Facts) -> Result:
    coverages = [m.coverage for m in f.mrs if m.coverage is not None]
    detail: dict[str, Any] = {"testing_findings": f.testing_findings, "loc": f.review_loc}
    finding_part = None
    if f.review_loc:
        per_100 = 100.0 * f.testing_findings / f.review_loc
        finding_part = _clamp(1.0 - per_100 / TESTING_PER_100_LOC)
        detail["testing_per_100_loc"] = round(per_100, 2)
    if coverages:
        avg = sum(coverages) / len(coverages)
        detail["coverage"] = round(avg, 1)
        cover_part = _clamp(avg / COVERAGE_TARGET)
        value = cover_part if finding_part is None else 0.6 * cover_part + 0.4 * finding_part
        return Result(
            "B3", value, "high", detail, [_mr(m) for m in f.mrs if m.coverage is not None]
        )
    if finding_part is None:
        return Result("B3", None, "low", detail)
    return Result("B3", finding_part, "low", detail)


def b4(f: Facts) -> Result:
    detail: dict[str, Any] = {"added": f.added_lines, "reworked": f.reworked_lines}
    if f.added_lines <= 0:
        return Result("B4", None, "low", detail)
    rate = f.reworked_lines / f.added_lines
    detail["rework_rate"] = round(100 * rate, 1)
    confidence = "low" if f.added_lines < 200 else "high"
    evidence = [{"kind": "file", **r} for r in f.rework_files[:10]]
    return Result("B4", _linear(rate, REWORK_GOOD, REWORK_BAD), confidence, detail, evidence)


def c1(f: Facts) -> Result:
    timed = [m for m in f.mrs if m.author_hours is not None]
    if not timed:
        return Result("C1", None, "low", {"mrs": 0})
    ratios = []
    for m in timed:
        base = f.repo_author_median.get(m.project_id)
        if base and base > 0:
            ratios.append((m.author_hours or 0.0) / base)
    mine = median(m.author_hours or 0.0 for m in timed)
    detail = {"mrs": len(timed), "author_hours_median": round(mine, 1)}
    if not ratios:
        return Result("C1", None, "low", detail)
    ratio = median(ratios)
    detail["vs_repo"] = round(ratio, 2)
    value = _linear(ratio, 1.0, AUTHOR_TIME_WORST)
    confidence = "low" if len(timed) < 3 else "high"
    return Result("C1", value, confidence, detail, [_mr(m) for m in timed])


def c2(f: Facts) -> Result:
    due = [i for i in f.issues_due if i.due_date is not None and not i.blocked]
    if not due:
        return Result("C2", None, "low", {"issues": 0})
    on_time = [
        i
        for i in due
        if i.state == "closed"
        and i.closed_at is not None
        and i.due_date is not None
        and i.closed_at.date() <= i.due_date
    ]
    value = len(on_time) / len(due)
    confidence = "low" if len(due) < 3 else "high"
    detail = {"issues": len(due), "on_time": len(on_time)}
    return Result("C2", value, confidence, detail, [_issue(i) for i in due])


def _compliant(m: MrFact) -> bool:
    checklist = m.tasks_total == 0 or m.tasks_done == m.tasks_total
    green = m.pipeline == "success" if m.ci else True  # no CI in the repo: not their fault
    return bool(m.issue_ids) and checklist and green and m.reviewed


def c3(f: Facts) -> Result:
    units = len(f.mrs) + len(f.direct_commits)
    if not units:
        return Result("C3", None, "low", {"units": 0})
    ok = sum(1 for m in f.mrs if _compliant(m))
    detail = {
        "units": units,
        "compliant": ok,
        "mrs": len(f.mrs),
        "direct_commits": len(f.direct_commits),
        "missing_issue": sum(1 for m in f.mrs if not m.issue_ids),
        "unchecked_checklist": sum(1 for m in f.mrs if m.tasks_done < m.tasks_total),
        "pipeline_not_green": sum(1 for m in f.mrs if m.ci and m.pipeline != "success"),
        "not_reviewed": sum(1 for m in f.mrs if not m.reviewed),
    }
    evidence = [{**_mr(m), "ok": _compliant(m)} for m in f.mrs] + [
        {"kind": "commit", **c, "ok": False} for c in f.direct_commits[:20]
    ]
    confidence = "low" if units < 3 else "high"
    return Result("C3", ok / units, confidence, detail, evidence)


def d1(f: Facts) -> Result:
    counted = [c for c in f.comments if c.label in MEANINGFUL and (c.acted_on or c.confirmed)]
    detail = {
        "comments": len(f.comments),
        "meaningful": sum(1 for c in f.comments if c.label in MEANINGFUL),
        "counted": len(counted),
        "cap": D1_CAP,
        "reviewed_mrs": f.reviewed_mrs,
    }
    if not f.reviewed_mrs and not f.comments:
        return Result("D1", None, "low", detail)
    confidence = "low" if f.reviewed_mrs < 3 else "high"
    evidence = [
        {
            "kind": "comment",
            "id": c.note_id,
            "ref": f"!{c.mr_iid}",
            "label": c.label,
            "url": c.web_url,
        }
        for c in counted[:D1_CAP]
    ]
    return Result("D1", min(1.0, len(counted) / D1_CAP), confidence, detail, evidence)


def d2(f: Facts) -> Result:
    shared = [i for i in f.items if i.confirmed and i.kind in ("design", "mentoring")]
    raw = len(f.outside_reviews) + 2 * len(shared)
    detail = {"outside_reviews": len(f.outside_reviews), "shared_items": len(shared)}
    if not f.reviewed_mrs and not f.items:
        return Result("D2", None, "low", detail)
    evidence = [{"kind": "mr", **r} for r in f.outside_reviews[:10]] + [
        {"kind": "self", "id": i.id, "label": i.title, "ref": i.kind} for i in shared
    ]
    return Result("D2", min(1.0, raw / D2_TARGET), "medium", detail, evidence)


AUTO = {
    "A1": a1,
    "A2": a2,
    "B1": b1,
    "B2": b2,
    "B3": b3,
    "B4": b4,
    "C1": c1,
    "C2": c2,
    "C3": c3,
    "D1": d1,
    "D2": d2,
}


def compute(f: Facts, keys: list[str]) -> dict[str, Result]:
    """The automatic metrics of the rubric (a key the code does not know is left out)."""
    return {k: AUTO[k](f) for k in keys if k in AUTO}


def split_mrs(mrs: list[MrFact]) -> list[dict[str, Any]]:
    """§6.2 anti-gaming: ≥ SPLIT_MRS MRs merged for the same issue on the same day."""
    groups: dict[tuple[int, date], list[MrFact]] = {}
    for m in mrs:
        if m.merged_at is None:
            continue
        for issue_id in m.issue_ids:
            groups.setdefault((issue_id, m.merged_at.date()), []).append(m)
    return [
        {"issue_id": issue_id, "day": day.isoformat(), "mrs": [f"!{m.iid}" for m in group]}
        for (issue_id, day), group in groups.items()
        if len(group) >= SPLIT_MRS
    ]
