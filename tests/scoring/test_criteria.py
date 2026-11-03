"""docs/specs/22 §1-2: the components check and the weighted criteria score."""

import pytest
from pydantic import ValidationError

from app.scoring.criteria import (
    CATEGORIES,
    TEMPLATES,
    Component,
    Components,
    CriteriaFinding,
    score,
)
from app.scoring.formula import WEIGHTS_V3, ScoredFinding, review_score

TWO = [
    {"key": "security", "name": "Bảo mật", "weight": 20, "categories": ["security"]},
    {
        "key": "rest",
        "name": "Còn lại",
        "weight": 80,
        "categories": [c for c in CATEGORIES if c != "security"],
    },
]


def comps(raw: list[dict[str, object]] = TWO) -> list[Component]:
    return Components(components=raw).components  # type: ignore[arg-type]


def finding(i: int, category: str, severity: str, placed: str | None = None) -> CriteriaFinding:
    return CriteriaFinding(i, category, ScoredFinding(severity, 1.0, "open", "confirmed"), placed)


def run(findings: list[CriteriaFinding], apply_caps: bool = True, loc: int = 200) -> object:
    return score(
        findings,
        comps(),
        loc_reviewed=loc,
        weights=WEIGHTS_V3,
        rubric_version="v4",
        apply_caps=apply_caps,
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c[0].update(weight=30), "tổng trọng số"),
        (lambda c: c[1].update(key="security"), "key thành phần bị trùng"),
        (lambda c: c[1]["categories"].remove("docs"), "chưa thuộc thành phần nào"),
        (lambda c: c[0]["categories"].append("bug"), "nằm ở cả"),
        (lambda c: c[0].update(key="Bad Key"), "key"),
        (lambda c: c.pop(), "cần 2-12"),
    ],
)
def test_components_are_checked_as_a_whole(change: object, message: str) -> None:
    raw = [dict(c, categories=list(c["categories"])) for c in TWO]  # type: ignore[call-overload]
    change(raw)  # type: ignore[operator]
    if len(raw) == 1:
        raw[0]["weight"] = 100
    with pytest.raises(ValidationError, match=message):
        Components(components=raw)  # type: ignore[arg-type]


def test_the_templates_are_valid_sets() -> None:
    for template in TEMPLATES.values():
        Components(components=template["components"])


def test_no_findings_scores_100() -> None:
    result = run([])
    assert result.score == 100 and all(p.score == 100 for p in result.components)  # type: ignore[attr-defined]


def test_each_component_scores_its_findings_and_the_final_is_weighted() -> None:
    medium = finding(1, "style", "medium")
    result = run([medium])
    sec, rest = result.components  # type: ignore[attr-defined]
    assert sec.score == 100
    assert rest.score == review_score([medium.scored], 200, WEIGHTS_V3, "v4")  # 95
    assert result.score == round((20 * 100 + 80 * rest.score) / 100, 2)  # type: ignore[attr-defined]
    assert rest.findings == [{"id": 1, "source": "category"}]


def test_the_model_places_a_finding_and_unknown_keys_fall_back_to_the_category() -> None:
    placed = finding(1, "bug", "high", placed="security")
    stray = finding(2, "bug", "low", placed="no_such_part")
    sec, rest = run([placed, stray]).components  # type: ignore[attr-defined]
    assert sec.findings == [{"id": 1, "source": "ai"}]
    assert rest.findings == [{"id": 2, "source": "category"}]


def test_a_critical_caps_the_final_score_unless_the_set_turns_caps_off() -> None:
    critical = finding(1, "security", "critical")
    capped = run([critical])
    assert capped.components[0].score == 50  # type: ignore[attr-defined]
    # 20% x 50 + 80% x 100 = 90, capped to 50 like the review score.
    assert capped.score == 50 and capped.capped  # type: ignore[attr-defined]
    free = run([critical], apply_caps=False)
    assert free.score == 90 and not free.capped  # type: ignore[attr-defined]


def test_disputed_findings_do_not_count() -> None:
    gone = CriteriaFinding(
        1, "security", ScoredFinding("critical", 1.0, "false_positive", "confirmed")
    )
    assert run([gone]).score == 100  # type: ignore[attr-defined]
