"""DPI scoring without a database (docs/specs/25 §1, §2)."""

import importlib.util
import pathlib
from datetime import UTC, date, datetime

from app.dpi import metrics as m
from app.dpi.labels import label_fields
from app.dpi.rubric import (
    DEFAULT_METRICS,
    DEFAULT_PROFILES,
    DEFAULT_RUBRIC,
    DEFAULT_VERSION,
    rating,
    validate_metrics,
    validate_weights,
)
from app.dpi.score import Profile, inputs_hash, rolling, scorecard

MIDDLE = Profile("middle", "Middle", {"A": 25, "B": 30, "C": 15, "D": 15, "E": 15}, 28)
MONTH = date(2026, 10, 1)


def at(day: int) -> datetime:
    return datetime(2026, 10, day, 9, tzinfo=UTC)


def issue(i: int, points: int | None, **kw: object) -> m.IssueFact:
    base: dict[str, object] = dict(
        id=i,
        iid=i,
        project_id=1,
        title=f"t{i}",
        web_url=None,
        type="feature",
        points=points,
        assignees=1,
        due_date=None,
        state="closed",
        closed_at=at(10),
    )
    base.update(kw)
    return m.IssueFact(**base)  # type: ignore[arg-type]


def test_the_migration_seeds_the_defaults_of_the_code() -> None:
    path = next(pathlib.Path("migrations/versions").glob("*_e7b9d1f3a5c7_dpi.py"))
    spec = importlib.util.spec_from_file_location("dpi_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RUBRIC_VERSION == DEFAULT_VERSION
    assert module.RUBRIC == DEFAULT_METRICS
    assert module.PROFILES == DEFAULT_PROFILES


def test_rubric_is_70_auto_30_manager_and_profiles_sum_to_100() -> None:
    auto = sum(x.max for x in DEFAULT_RUBRIC.metrics if x.source == "auto")
    manager = sum(x.max for x in DEFAULT_RUBRIC.metrics if x.source == "manager")
    assert (auto, manager) == (70, 30)
    for p in DEFAULT_PROFILES:
        assert validate_weights(p["weights"]) == []
    assert validate_metrics(DEFAULT_METRICS) == []
    assert validate_weights({"A": 50, "B": 10}) and validate_metrics([{"key": "X1", "group": "Z"}])


def test_labels_and_ratings() -> None:
    assert label_fields(["complexity::L", "type::Bug", "prod-bug::p1"]) == {
        "type": "bug",
        "complexity": "L",
        "complexity_points": 5,
        "prod_severity": "P1",
    }
    assert label_fields(["complexity::huge"])["complexity_points"] is None
    assert [rating(x) for x in (95, 85, 75, 65, 10, None)] == [
        "excellent",
        "good",
        "meets",
        "improve",
        "below",
        None,
    ]


def test_a1_scales_the_target_with_leave_and_splits_shared_issues() -> None:
    f = m.Facts(user_id=1, month=MONTH, a1_target=20, leave_days=10)
    f.issues_closed = [issue(1, 5), issue(2, 8, assignees=2), issue(3, None)]
    f.issues_closed_all = 3
    result = m.a1(f)
    # 5 + 8/2 = 9 points against a target of 20 x 10/20 days = 10.
    assert result.value == 0.9 and result.detail["target"] == 10
    assert result.confidence == "high" and len(result.evidence) == 2
    assert m.a1(m.Facts(user_id=1, month=MONTH, a1_target=20)).value is None


def test_b1_b3_b4_c_and_d_formulas() -> None:
    f = m.Facts(user_id=1, month=MONTH, a1_target=28)
    f.review_count, f.review_avg, f.severe_reviews = 4, 80.0, 1
    f.pipelines_success, f.pipelines_failed = 3, 1
    b1 = m.b1(f)
    assert b1.value is not None and abs(b1.value - (0.5 * 0.8 + 0.25 * 0.75 + 0.25 * 0.75)) < 1e-9
    f.review_loc, f.testing_findings = 400, 2  # 0.5 per 100 LOC -> 0.5
    f.mrs = [
        m.MrFact(1, 1, 1, "a", None, at(5), 10.0, (7,), 2, 2, "success", 72.0, True),
        m.MrFact(2, 2, 1, "b", None, at(6), 30.0, (), 1, 0, "failed", None, False),
    ]
    b3 = m.b3(f)
    assert b3.value is not None and abs(b3.value - (0.6 * 0.9 + 0.4 * 0.5)) < 1e-9
    f.added_lines, f.reworked_lines = 1000, 250  # 25 %: halfway between 10 % and 40 %
    assert m.b4(f).value == 0.5
    f.repo_author_median = {1: 10.0}
    c1 = m.c1(f)  # ratios 1.0 and 3.0 -> median 2.0 -> (2.5 - 2) / 1.5
    assert c1.value is not None and abs(c1.value - 1 / 3) < 1e-9
    f.direct_commits = [{"ref": "abc", "label": "hotfix", "url": None}]
    c3 = m.c3(f)  # one MR of three units passes
    assert c3.value is not None and abs(c3.value - 1 / 3) < 1e-9
    assert c3.detail["direct_commits"] == 1
    f.issues_due = [
        issue(1, 2, due_date=date(2026, 10, 10), closed_at=at(9)),
        issue(2, 2, due_date=date(2026, 10, 10), closed_at=at(12)),
        issue(3, 2, due_date=date(2026, 10, 10), state="opened", closed_at=None, blocked=True),
    ]
    assert m.c2(f).value == 0.5
    f.reviewed_mrs = 4
    f.comments = [
        m.CommentFact(i, 1, 1, 1, "correctness", acted_on=True, confirmed=False) for i in range(12)
    ] + [m.CommentFact(99, 1, 1, 1, "cosmetic", acted_on=True, confirmed=False)]
    d1 = m.d1(f)
    assert d1.value == 1.0 and d1.detail["counted"] == 12  # capped at 10
    f.outside_reviews = [{"id": 1}, {"id": 2}]
    f.items = [m.ItemFact(1, "mentoring", "Kèm bạn mới", confirmed=True)]
    assert m.d2(f).value == (2 + 2) / 6


def test_b2_penalises_confirmed_defects_by_share() -> None:
    f = m.Facts(user_id=1, month=MONTH, a1_target=28)
    f.defects = [
        m.DefectFact(1, 1, "P1", 0.5, "crash", None),
        m.DefectFact(2, 2, "P3", 1, "x", None),
    ]
    assert m.b2(f).value == 1 - (4 * 0.5 + 1) / 8


def test_scorecard_leaves_out_missing_metrics_and_is_provisional_without_the_lead() -> None:
    auto = {
        "A1": m.Result("A1", 1.0, "high"),
        "B1": m.Result("B1", 0.5, "high"),
        "B2": m.Result("B2", 1.0, "high"),
    }
    card = scorecard(DEFAULT_RUBRIC, MIDDLE, auto, {})
    assert card["provisional"] is True
    # A: A1 only -> ratio 1 x 25; B: (0.5 x 8 + 8) / 16 = 0.75 x 30; total over A and B.
    assert card["total"] == round((25 + 22.5) / 55 * 100, 2)
    assert card["confidence"] == "low" and card["rating"] is None  # too little coverage
    manager = {"A3": 5.0, "B5": 4.0, "D3": 5.0, "E1": 5.0, "E2": 4.0, "E3": 3.0}
    full_auto = {k: m.Result(k, 1.0, "high") for k in m.AUTO}
    full = scorecard(DEFAULT_RUBRIC, MIDDLE, full_auto, manager)
    assert full["provisional"] is False and full["confidence"] == "high"
    assert full["coverage"] == 1.0 and full["rating"] in ("excellent", "good")
    assert full["split"]["auto_max"] + full["split"]["manager_max"] == 100
    adjusted = scorecard(DEFAULT_RUBRIC, MIDDLE, full_auto, manager, adjustment=-50)
    assert adjusted["total"] == round(full["total"] - 50, 2)
    overridden = scorecard(DEFAULT_RUBRIC, MIDDLE, full_auto, manager, {"B2": 0.0})
    assert overridden["metrics"]["B2"]["overridden"] and overridden["total"] < full["total"]


def test_rolling_and_hash() -> None:
    assert rolling([80, 70, 60])["value"] == 0.5 * 80 + 0.3 * 70 + 0.2 * 60
    partial = rolling([80, None, 60])
    assert partial["partial"] and partial["value"] == round((0.5 * 80 + 0.2 * 60) / 0.7, 2)
    assert rolling([None, 70, 60])["value"] is None
    assert inputs_hash({"a": 1, "b": [1, 2]}) == inputs_hash({"b": [1, 2], "a": 1})


def test_split_mrs_flags_many_mrs_for_one_issue_in_a_day() -> None:
    mrs = [m.MrFact(i, i, 1, None, None, at(5), 1.0, (9,)) for i in range(3)]
    assert m.split_mrs(mrs) == [{"issue_id": 9, "day": "2026-10-05", "mrs": ["!0", "!1", "!2"]}]
    assert m.split_mrs(mrs[:2]) == []
