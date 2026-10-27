"""Before/after numbers for a change of the review flow (docs/specs/19 §5, task 8.12).

`collect()` reads the reviews finished in a window and groups them by `mode` (fast / deep /
agent) and `rubric_version`; `compare()` puts two such reports side by side; `render_markdown()`
prints either. The arithmetic lives in small pure helpers (`percentiles`, `drift`, `summarise`)
so it is testable without a database; the SQL part is plain `select()`s in `collect()`.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

from app.models import Commit, FindingDispute, MergeRequest, Review, ReviewFinding
from app.models.review import SEVERITIES

# Statuses that mean "the review finished and its score counts". An earlier round of a
# re-reviewed sha keeps its findings but is marked superseded (docs/specs/17 §Lịch sử).
DONE_STATUSES = ("done", "superseded")

Group = tuple[str, str]  # (mode, rubric_version)


# --- pure helpers ---------------------------------------------------------------------------


def percentiles(values: Iterable[float | int | Decimal | None]) -> dict[str, float | None]:
    """p50 / p95 (nearest-rank) of the non-null values, plus their count and mean."""
    xs = sorted(float(v) for v in values if v is not None)
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "mean": None}

    def rank(p: float) -> float:
        return xs[min(len(xs) - 1, max(0, math.ceil(p * len(xs)) - 1))]

    return {
        "n": len(xs),
        "p50": round(rank(0.50), 4),
        "p95": round(rank(0.95), 4),
        "mean": round(sum(xs) / len(xs), 4),
    }


def drift(rows: Iterable[tuple[Any, int, float | Decimal | None, str | None]]) -> dict[str, Any]:
    """Score drift when the same sha is reviewed again.

    `rows` are `(target_key, round, score, verdict)`; targets with fewer than two rows are
    ignored. For every pair of consecutive rounds of one target: the absolute score difference
    and whether the verdict changed.
    """
    by_target: dict[Any, list[tuple[int, float | None, str | None]]] = defaultdict(list)
    for key, round_no, score, verdict in rows:
        by_target[key].append((round_no, None if score is None else float(score), verdict))
    diffs: list[float] = []
    changed = 0
    pairs = 0
    targets = 0
    for rounds in by_target.values():
        if len(rounds) < 2:
            continue
        targets += 1
        rounds.sort(key=lambda r: r[0])
        for (_, s1, v1), (_, s2, v2) in pairwise(rounds):
            pairs += 1
            if s1 is not None and s2 is not None:
                diffs.append(abs(s2 - s1))
            if v1 != v2:
                changed += 1
    return {
        "targets": targets,
        "pairs": pairs,
        "mean_abs_score_diff": round(sum(diffs) / len(diffs), 4) if diffs else None,
        "max_abs_score_diff": round(max(diffs), 4) if diffs else None,
        "verdict_change_rate": round(changed / pairs, 4) if pairs else None,
    }


def _ignored(deep: Mapping[str, Any] | None) -> int:
    """`deep.ignored_verifications` was a list in spec 18 and may be a count later on."""
    if not deep:
        return 0
    value = deep.get("ignored_verifications")
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, list | tuple | dict):
        return len(value)
    return 0


def _turns(deep: Mapping[str, Any] | None) -> int | None:
    value = (deep or {}).get("turns")
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def summarise(
    reviews: Sequence[Mapping[str, Any]],
    findings: Sequence[tuple[int, str, str | None]] = (),
    disputes: Sequence[tuple[str, str | None]] = (),
    drift_rows: Sequence[tuple[Any, int, float | Decimal | None, str | None]] = (),
) -> dict[str, Any]:
    """One group's numbers from plain rows.

    `reviews`: dicts with `cost_usd`, `started_at`, `finished_at`, `loc_reviewed`, `deep`,
    `mode`; `findings`: `(review_id, severity, verified)`; `disputes`: `(severity, decision)`.
    """
    n = len(reviews)
    durations = [
        (r["finished_at"] - r["started_at"]).total_seconds()
        for r in reviews
        if r.get("finished_at") is not None and r.get("started_at") is not None
    ]
    deeps = [r.get("deep") for r in reviews]
    ignored = [_ignored(d) for d in deeps]
    turns = [_turns(d) for d in deeps]
    with_tools = any(r.get("mode") in ("deep", "agent") for r in reviews)

    per_severity: dict[str, int] = dict.fromkeys(SEVERITIES, 0)
    verified: dict[str, int] = defaultdict(int)
    for _, severity, label in findings:
        per_severity[severity] = per_severity.get(severity, 0) + 1
        if label is not None:
            verified[label] += 1

    acceptance: dict[str, dict[str, Any]] = {}
    for severity in SEVERITIES:
        decided = [d for s, d in disputes if s == severity and d is not None]
        accepted = sum(1 for d in decided if d == "accepted")
        acceptance[severity] = {
            "decided": len(decided),
            "accepted": accepted,
            "rate": round(accepted / len(decided), 4) if decided else None,
        }

    return {
        "reviews": n,
        "cost_usd": percentiles(r.get("cost_usd") for r in reviews),
        "duration_s": percentiles(durations),
        "loc_reviewed": percentiles(r.get("loc_reviewed") for r in reviews),
        "turns": percentiles(turns) if with_tools else None,
        "ignored_verifications": {
            "sum": sum(ignored),
            "share_gt0": round(sum(1 for i in ignored if i > 0) / n, 4) if n else None,
        },
        "findings_per_review": {s: round(c / n, 4) if n else None for s, c in per_severity.items()},
        "findings_total": sum(per_severity.values()),
        "verified": dict(sorted(verified.items())) if verified else None,
        "dispute_acceptance": acceptance,
        "drift": drift(drift_rows),
    }


# --- database -------------------------------------------------------------------------------


def _group_key(mode: str | None, rubric: str | None) -> str:
    return f"{mode or 'fast'}/{rubric or '-'}"


def collect(
    session: Session, since: datetime, until: datetime, project_id: int | None = None
) -> dict[str, Any]:
    """Numbers per `mode/rubric_version` for reviews finished in `[since, until)`."""
    if until <= since:
        raise ValueError("until must be after since")

    base = select(Review).where(
        Review.status.in_(DONE_STATUSES),
        Review.finished_at.is_not(None),
        Review.finished_at >= since,
        Review.finished_at < until,
    )
    if project_id is not None:
        base = base.where(
            (Review.commit_sha.in_(select(Commit.sha).where(Commit.project_id == project_id)))
            | (
                Review.mr_id.in_(
                    select(MergeRequest.id).where(MergeRequest.project_id == project_id)
                )
            )
        )
    reviews = list(session.scalars(base.order_by(Review.id)))
    review_ids = [r.id for r in reviews]
    group_of: dict[int, str] = {r.id: _group_key(r.mode, r.rubric_version) for r in reviews}

    rows_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reviews:
        rows_by_group[group_of[r.id]].append(
            {
                "cost_usd": r.cost_usd,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "loc_reviewed": r.loc_reviewed,
                "deep": r.deep,
                "mode": r.mode,
            }
        )

    findings_by_group: dict[str, list[tuple[int, str, str | None]]] = defaultdict(list)
    disputes_by_group: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    verified_available = True
    if review_ids:
        finding_rows, verified_available = _findings(session, review_ids)
        for review_id, severity, label in finding_rows:
            findings_by_group[group_of[review_id]].append((review_id, severity, label))
        dispute_rows = session.execute(
            select(ReviewFinding.review_id, ReviewFinding.severity, FindingDispute.decision)
            .join(FindingDispute, FindingDispute.finding_id == ReviewFinding.id)
            .where(ReviewFinding.review_id.in_(review_ids))
        ).all()
        for rid, severity, decision in dispute_rows:
            if rid is not None:
                disputes_by_group[group_of[rid]].append((severity, decision))

    # Score drift: every finished round of the targets seen in the window, keyed by the group
    # of the later round so a group's number describes what *it* produced.
    drift_by_group: dict[str, list[tuple[Any, int, float | Decimal | None, str | None]]] = (
        defaultdict(list)
    )
    target_keys = {(r.target_type, r.mr_id, r.commit_sha, r.reviewed_sha) for r in reviews}
    if target_keys:
        shas = {k[3] for k in target_keys}
        all_rounds = session.execute(
            select(
                Review.id,
                Review.target_type,
                Review.mr_id,
                Review.commit_sha,
                Review.reviewed_sha,
                Review.round,
                Review.score,
                Review.verdict,
                Review.mode,
                Review.rubric_version,
            ).where(Review.status.in_(DONE_STATUSES), Review.reviewed_sha.in_(shas))
        ).all()
        rounds_by_target: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
        for row in all_rounds:
            key = (row.target_type, row.mr_id, row.commit_sha, row.reviewed_sha)
            if key in target_keys:
                rounds_by_target[key].append(row)
        for key, rows in rounds_by_target.items():
            rows.sort(key=lambda r: r.round)
            for earlier, later in pairwise(rows):
                group = _group_key(later.mode, later.rubric_version)
                drift_by_group[group].append((key, earlier.round, earlier.score, earlier.verdict))
                drift_by_group[group].append((key, later.round, later.score, later.verdict))
        # A pair whose two rows were added twice (three rounds) is harmless: drift() keys by
        # target and sorts by round; duplicates collapse below.
        for group, rows_ in drift_by_group.items():
            drift_by_group[group] = sorted(set(rows_), key=lambda r: (str(r[0]), r[1]))

    groups: dict[str, Any] = {}
    for group in sorted(rows_by_group):
        mode, _, rubric = group.partition("/")
        summary = summarise(
            rows_by_group[group],
            findings_by_group.get(group, []),
            disputes_by_group.get(group, []),
            drift_by_group.get(group, []),
        )
        if not verified_available:
            summary["verified"] = None
        groups[group] = {"mode": mode, "rubric_version": rubric, **summary}

    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "project_id": project_id,
        "reviews": len(reviews),
        "groups": groups,
    }


def _findings(
    session: Session, review_ids: Sequence[int]
) -> tuple[list[tuple[int, str, str | None]], bool]:
    """`(review_id, severity, verified)` rows and whether the `verified` column exists (a
    database behind the spec 19 migration has none; the rows then carry None)."""
    try:
        with session.begin_nested():
            rows = session.execute(
                select(
                    ReviewFinding.review_id, ReviewFinding.severity, ReviewFinding.verified
                ).where(ReviewFinding.review_id.in_(review_ids))
            ).all()
        return [
            (int(rid), str(sev), str(label) if label else None)
            for rid, sev, label in rows
            if rid is not None
        ], True
    except (ProgrammingError, DBAPIError):
        plain = session.execute(
            select(ReviewFinding.review_id, ReviewFinding.severity).where(
                ReviewFinding.review_id.in_(review_ids)
            )
        ).all()
        return [(int(rid), str(sev), None) for rid, sev in plain if rid is not None], False


# --- compare and render ---------------------------------------------------------------------

_SCALARS: tuple[tuple[str, ...], ...] = (
    ("reviews",),
    ("cost_usd", "p50"),
    ("cost_usd", "p95"),
    ("duration_s", "p50"),
    ("duration_s", "p95"),
    ("loc_reviewed", "p50"),
    ("loc_reviewed", "p95"),
    ("turns", "p50"),
    ("turns", "p95"),
    ("ignored_verifications", "sum"),
    ("ignored_verifications", "share_gt0"),
    ("findings_total",),
    *(("findings_per_review", s) for s in SEVERITIES),
    *(("dispute_acceptance", s, "rate") for s in SEVERITIES),
    ("drift", "pairs"),
    ("drift", "mean_abs_score_diff"),
    ("drift", "verdict_change_rate"),
)


def _dig(data: Mapping[str, Any] | None, path: tuple[str, ...]) -> float | None:
    value: Any = data
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def compare(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Per-metric deltas, group by group (groups present on only one side show `None`)."""
    groups_before = before.get("groups", {})
    groups_after = after.get("groups", {})
    out: dict[str, Any] = {}
    for group in sorted(set(groups_before) | set(groups_after)):
        metrics: dict[str, dict[str, float | None]] = {}
        for path in _SCALARS:
            b = _dig(groups_before.get(group), path)
            a = _dig(groups_after.get(group), path)
            delta = pct = None
            if a is not None and b is not None:
                delta = round(a - b, 4)
                pct = round((a - b) / b * 100, 1) if b else None
            metrics[".".join(path)] = {"before": b, "after": a, "delta": delta, "pct": pct}
        out[group] = metrics
    return {
        "before": {"since": before.get("since"), "until": before.get("until")},
        "after": {"since": after.get("since"), "until": after.get("until")},
        "groups": out,
    }


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return str(int(value)) if float(value).is_integer() else f"{value:.4g}"


def render_markdown(data: Mapping[str, Any]) -> str:
    """Markdown tables for a `collect()` report or a `compare()` result."""
    groups: Mapping[str, Any] = data.get("groups", {})
    is_comparison = "before" in data and "after" in data
    lines: list[str] = []
    if is_comparison:
        lines.append(
            f"Trước: {data['before'].get('since')} → {data['before'].get('until')}; "
            f"sau: {data['after'].get('since')} → {data['after'].get('until')}"
        )
        for group, metrics in groups.items():
            lines += [
                "",
                f"### {group}",
                "",
                "| metric | trước | sau | delta | % |",
                "|---|---:|---:|---:|---:|",
            ]
            for name, m in metrics.items():
                lines.append(
                    f"| {name} | {_fmt(m['before'])} | {_fmt(m['after'])} | "
                    f"{_fmt(m['delta'])} | {_fmt(m['pct'])} |"
                )
        return "\n".join(lines) + "\n"

    lines.append(
        f"Khoảng: {data.get('since')} → {data.get('until')}"
        + (f"; project {data['project_id']}" if data.get("project_id") is not None else "")
        + f"; {data.get('reviews', 0)} review"
    )
    lines += ["", "| metric | " + " | ".join(groups) + " |", "|---|" + "---:|" * len(groups)]
    for path in _SCALARS:
        cells = [_fmt(_dig(g, path)) for g in groups.values()]
        lines.append(f"| {'.'.join(path)} | " + " | ".join(cells) + " |")
    verified_rows = {label for g in groups.values() for label in (g.get("verified") or {})}
    for label in sorted(verified_rows):
        cells = [_fmt(_dig(g, ("verified", label))) for g in groups.values()]
        lines.append(f"| verified.{label} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def dumps(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)
