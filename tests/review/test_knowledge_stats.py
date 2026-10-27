"""Per-rule statistics (docs/specs/19 §5): the pure aggregation, without a database."""

from app.review.knowledge_stats import (
    FindingRow,
    ReviewRow,
    aggregate,
    applied_rule_ids,
    normalize_rule_id,
)


def finding(
    review_id: int, rule: str, verified: str = "confirmed", disputed: bool = False
) -> FindingRow:
    return FindingRow(review_id, rule, verified, disputed)


def by_rule(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(r["rule_id"]): r for r in rows}


def test_normalize_rule_id_keeps_knowledge_rules_only() -> None:
    assert normalize_rule_id("rk:inv-1") == "rk:inv-1"
    assert normalize_rule_id(" inv-1 ") == "rk:inv-1"
    assert normalize_rule_id("rubric:2") is None
    assert normalize_rule_id("checklist:python") is None
    assert normalize_rule_id("rk:") is None
    assert normalize_rule_id(None) is None


def test_applied_rule_ids_reads_both_basis_shapes() -> None:
    nested = {
        "knowledge": {
            "applied_rules": [
                {"rule_id": "rk:inv-1", "applies": True, "where": "app/a.py"},
                {"rule_id": "inv-2", "applies": True},
                {"rule_id": "rk:inv-3", "applies": False},
                {"rule_id": "rubric:1", "applies": True},
                "garbage",
            ]
        }
    }
    assert applied_rule_ids(nested) == frozenset({"rk:inv-1", "rk:inv-2"})
    flat = {"applied_rules": [{"rule_id": "rk:chk-1", "applies": True}]}
    assert applied_rule_ids(flat) == frozenset({"rk:chk-1"})
    assert applied_rule_ids(None) == frozenset()
    assert applied_rule_ids({"knowledge": {"applied_rules": "no"}}) == frozenset()


def test_counts_per_rule_and_sorts_by_fired() -> None:
    reviews = [
        ReviewRow(1, frozenset({"rk:inv-1", "rk:inv-2"})),
        ReviewRow(2, frozenset({"rk:inv-2"})),
        ReviewRow(3, frozenset()),
    ]
    findings = [
        finding(1, "rk:inv-1", "confirmed"),
        finding(1, "rk:inv-1", "downgraded"),
        finding(3, "rk:inv-1", "rejected", disputed=True),
        finding(2, "rk:inv-2", "unverified"),
        finding(2, "rubric:1", "confirmed"),  # not a knowledge rule
        finding(3, "rk:inv-1", "skipped"),  # verification skipped: fired, no verified bucket
    ]
    rows = by_rule(aggregate(reviews, findings))
    assert list(rows) == ["rk:inv-1", "rk:inv-2"]
    inv1 = rows["rk:inv-1"]
    assert inv1["fired"] == 4
    assert (inv1["confirmed"], inv1["downgraded"], inv1["rejected"], inv1["unverified"]) == (
        1,
        1,
        1,
        0,
    )
    assert inv1["disputes_accepted"] == 1
    assert inv1["applied_not_fired"] == 0
    assert inv1["label"] is None
    inv2 = rows["rk:inv-2"]
    assert inv2["fired"] == 1 and inv2["unverified"] == 1
    # applied in review 1 without a finding, fired in review 2
    assert inv2["applied_not_fired"] == 1


def test_dead_rule_applied_but_never_fired() -> None:
    reviews = [ReviewRow(1, frozenset({"rk:inv-9"})), ReviewRow(2, frozenset({"rk:inv-9"}))]
    (row,) = aggregate(reviews, [])
    assert row["rule_id"] == "rk:inv-9"
    assert row["fired"] == 0 and row["applied_not_fired"] == 2
    assert row["label"] == "dead"


def test_noisy_rule_needs_two_firings_and_half_noise() -> None:
    one = aggregate([], [finding(1, "rk:a", "rejected")])
    assert one[0]["label"] is None  # a single firing says nothing yet
    two = aggregate([], [finding(1, "rk:a", "rejected"), finding(2, "rk:a", "confirmed")])
    assert two[0]["label"] == "noisy"
    disputed = aggregate(
        [],
        [
            finding(1, "rk:b", "confirmed", disputed=True),
            finding(2, "rk:b", "confirmed", disputed=True),
            finding(3, "rk:b", "confirmed"),
            finding(4, "rk:b", "confirmed"),
        ],
    )
    assert disputed[0]["label"] == "noisy"
    fine = aggregate([], [finding(1, "rk:c", "rejected"), finding(2, "rk:c"), finding(3, "rk:c")])
    assert fine[0]["label"] is None


def test_empty_window() -> None:
    assert aggregate([], []) == []
