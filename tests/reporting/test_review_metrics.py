"""docs/specs/19 §5, task 8.12: before/after numbers of the review flow."""

import io
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.cli import CliError, build_parser, run
from app.reporting import review_metrics
from app.reporting.review_metrics import (
    compare,
    drift,
    percentiles,
    render_markdown,
    summarise,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def test_percentiles_nearest_rank_ignores_none() -> None:
    assert percentiles([]) == {"n": 0, "p50": None, "p95": None, "mean": None}
    assert percentiles([3, None, 1, 2]) == {"n": 3, "p50": 2.0, "p95": 3.0, "mean": 2.0}
    assert percentiles([Decimal("0.5")])["p95"] == 0.5
    hundred = percentiles(range(1, 101))
    assert (hundred["p50"], hundred["p95"]) == (50.0, 95.0)


def test_drift_pairs_consecutive_rounds_per_target() -> None:
    rows: list[tuple[Any, int, float | Decimal | None, str | None]] = [
        ("a", 1, Decimal("80"), "approve"),
        ("a", 3, 70.0, "block"),  # rounds sorted, gaps allowed
        ("a", 2, 75.0, "approve"),
        ("b", 1, 60.0, "fix_required"),  # a single round: ignored
        ("c", 1, None, "approve"),  # score missing: verdict still compared
        ("c", 2, 50.0, "approve"),
    ]
    out = drift(rows)
    assert out["targets"] == 2 and out["pairs"] == 3
    assert out["mean_abs_score_diff"] == 5.0 and out["max_abs_score_diff"] == 5.0
    assert out["verdict_change_rate"] == round(1 / 3, 4)
    assert drift([]) == {
        "targets": 0,
        "pairs": 0,
        "mean_abs_score_diff": None,
        "max_abs_score_diff": None,
        "verdict_change_rate": None,
    }


def _review(mode: str = "agent", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "mode": mode,
        "cost_usd": Decimal("0.20"),
        "started_at": T0,
        "finished_at": T0 + timedelta(seconds=90),
        "loc_reviewed": 120,
        "deep": {"turns": 12, "ignored_verifications": ["x"]},
    }
    return {**base, **kw}


def test_summarise_counts_findings_disputes_and_ignored_verifications() -> None:
    reviews = [
        _review(),
        _review(cost_usd=Decimal("0.40"), deep={"turns": 20, "ignored_verifications": 0}),
        _review(deep=None, finished_at=None),
    ]
    findings = [(1, "high", "confirmed"), (1, "low", "rejected"), (2, "high", "confirmed")]
    disputes = [("high", "accepted"), ("high", "rejected"), ("low", None)]
    s = summarise(reviews, findings, disputes)
    assert s["reviews"] == 3
    assert s["cost_usd"]["p50"] == 0.2 and s["cost_usd"]["p95"] == 0.4
    assert s["duration_s"] == {"n": 2, "p50": 90.0, "p95": 90.0, "mean": 90.0}
    assert s["turns"]["n"] == 2 and s["turns"]["p95"] == 20.0
    assert s["ignored_verifications"] == {"sum": 1, "share_gt0": round(1 / 3, 4)}
    assert s["findings_per_review"]["high"] == round(2 / 3, 4)
    assert s["findings_per_review"]["critical"] == 0.0 and s["findings_total"] == 3
    assert s["verified"] == {"confirmed": 2, "rejected": 1}
    assert s["dispute_acceptance"]["high"] == {"decided": 2, "accepted": 1, "rate": 0.5}
    assert s["dispute_acceptance"]["low"] == {"decided": 0, "accepted": 0, "rate": None}


def test_summarise_fast_mode_has_no_turns_and_tolerates_missing_verified() -> None:
    s = summarise([_review("fast", deep=None)], [(1, "medium", None)])
    assert s["turns"] is None and s["verified"] is None
    assert s["ignored_verifications"] == {"sum": 0, "share_gt0": 0.0}
    assert summarise([])["findings_per_review"]["high"] is None


def _report(**groups: dict[str, Any]) -> dict[str, Any]:
    return {
        "since": "2026-10-01",
        "until": "2026-10-15",
        "project_id": None,
        "reviews": sum(g["reviews"] for g in groups.values()),
        "groups": groups,
    }


def test_compare_gives_deltas_and_marks_one_sided_groups() -> None:
    before = _report(**{"deep/v2": summarise([_review("deep", cost_usd=Decimal("0.5"))])})
    after = _report(
        **{
            "deep/v2": summarise([_review("deep", cost_usd=Decimal("0.25"))]),
            "agent/v3": summarise([_review()]),
        }
    )
    out = compare(before, after)
    cost = out["groups"]["deep/v2"]["cost_usd.p50"]
    assert cost == {"before": 0.5, "after": 0.25, "delta": -0.25, "pct": -50.0}
    assert out["groups"]["agent/v3"]["reviews"] == {
        "before": None,
        "after": 1.0,
        "delta": None,
        "pct": None,
    }
    md = render_markdown(out)
    assert "### deep/v2" in md and "| cost_usd.p50 | 0.5 | 0.25 | -0.25 | -50 |" in md


def test_render_markdown_report_lists_groups_as_columns() -> None:
    report = _report(
        **{"fast/v2": summarise([_review("fast")], [(1, "high", "confirmed")])},
    )
    md = render_markdown(report)
    assert md.splitlines()[2] == "| metric | fast/v2 |"
    assert "| reviews | 1 |" in md and "| verified.confirmed | 1 |" in md
    assert "| drift.pairs | 0 |" in md


# --- CLI (no database: collect() is replaced) ----------------------------------------------


def cli(*argv: str, session: Any = None) -> tuple[bool, str]:
    out = io.StringIO()
    ok = run(build_parser().parse_args(argv), session or MagicMock(), MagicMock(), MagicMock(), out)
    return ok, out.getvalue()


def test_review_metrics_cli_writes_json_and_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[datetime, datetime, int | None]] = []

    def fake_collect(
        session: Any, since: datetime, until: datetime, project_id: int | None = None
    ) -> dict[str, Any]:
        seen.append((since, until, project_id))
        return _report(**{"agent/v3": summarise([_review()])})

    monkeypatch.setattr(review_metrics, "collect", fake_collect)
    target = tmp_path / "before.json"
    ok, out = cli(
        "review-metrics", "--since", "2026-10-01", "--until", "2026-10-15", "--out", str(target)
    )
    assert ok and "1 reviews, 1 group(s)" in out
    assert seen == [(T0, datetime(2026, 10, 15, tzinfo=UTC), None)]
    assert json.loads(target.read_text(encoding="utf-8"))["groups"]["agent/v3"]["reviews"] == 1

    ok, out = cli("review-metrics", "--since", "2026-10-01", "--md")
    assert ok and out.startswith("Khoảng:") and "| metric | agent/v3 |" in out
    assert seen[-1][1] > seen[-1][0]  # --until omitted: through today

    _, out = cli("review-metrics", "--since", "2026-10-01")
    assert json.loads(out)["reviews"] == 1  # plain JSON on stdout

    with pytest.raises(CliError, match="--until"):
        cli("review-metrics", "--since", "2026-10-15", "--until", "2026-10-01")


def test_review_metrics_cli_checks_project(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    session.get.return_value = None
    with pytest.raises(CliError, match="project 9 not found"):
        cli("review-metrics", "--since", "2026-10-01", "--project", "9", session=session)


def test_review_metrics_compare_cli(tmp_path: Path) -> None:
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(
        review_metrics.dumps(_report(**{"deep/v2": summarise([_review("deep")])})),
        encoding="utf-8",
    )
    after.write_text(
        review_metrics.dumps(
            _report(**{"deep/v2": summarise([_review("deep", cost_usd=Decimal("0.1"))])})
        ),
        encoding="utf-8",
    )
    ok, out = cli("review-metrics-compare", str(before), str(after))
    assert ok and json.loads(out)["groups"]["deep/v2"]["cost_usd.p50"]["delta"] == -0.1
    ok, out = cli("review-metrics-compare", str(before), str(after), "--md")
    assert "| cost_usd.p50 | 0.2 | 0.1 | -0.1 | -50 |" in out
    with pytest.raises(CliError, match="cannot read"):
        cli("review-metrics-compare", str(before), str(tmp_path / "missing.json"))
