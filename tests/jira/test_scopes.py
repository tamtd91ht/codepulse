"""Routing a Jira issue to the CodePulse dự án of its project (docs/specs/27 §15): rules on
components / labels, the catch-all for the rest, every dự án when nothing else applies."""

from app.jira.scopes import Rule, clean_values, route

FE, BE, PM = 1, 2, 3


def test_no_rule_means_every_dự_án() -> None:
    assert route([], ["Frontend"], []) is None
    assert route([Rule(FE), Rule(BE)], ["Frontend"], ["x"]) is None


def test_components_and_labels_route_case_insensitively() -> None:
    rules = [Rule(FE, components=("Frontend",)), Rule(BE, components=("Backend",), labels=("api",))]
    assert route(rules, ["frontend "], []) == [FE]
    assert route(rules, [], ["API"]) == [BE]
    assert route(rules, ["Frontend", "Backend"], []) == [FE, BE]  # both: counted in both


def test_unmatched_issues_go_to_the_catch_all_else_everywhere() -> None:
    ruled = [Rule(FE, components=("Frontend",)), Rule(BE, components=("Backend",))]
    assert route(ruled, ["Docs"], ["chore"]) is None  # every dự án of the project
    assert route(ruled, None, None) is None
    with_rest = [*ruled, Rule(PM)]
    assert route(with_rest, ["Docs"], []) == [PM]
    assert route(with_rest, ["Backend"], []) == [BE]  # a match never adds the catch-all


def test_clean_values() -> None:
    assert clean_values([" FE ", "fe", "", "BE"], "c") == ["FE", "BE"]
    try:
        clean_values(["x" * 201], "c")
    except ValueError as exc:
        assert "200" in str(exc)
    else:
        raise AssertionError("too long must be refused")
