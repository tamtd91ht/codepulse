import json
from pathlib import Path
from typing import Any

import pytest

from app.review.eval import main
from app.review.eval_knowledge import (
    GenerationError,
    InvariantDict,
    KnowledgeDict,
    case_key,
    generate_cases,
    llm_generator,
    oracle_provider,
    run_knowledge_eval,
    template_generator,
    validate_generated,
    write_report,
)
from app.review.providers import FakeLLMProvider, ReviewPrompt

KNOWLEDGE: KnowledgeDict = {
    "language": "python",
    "invariants": [
        {
            "id": "inv-validate-input",
            "statement": "Every handler validates its payload before processing it.",
            "severity": "high",
            "signals": ["def handler", "payload"],
        },
        {
            "id": "inv-no-bare-return",
            "statement": "Handlers return a Result, never None.",
            "severity": "medium",
            "signals": ["return None"],
        },
    ],
}


def test_a_perfect_generator_and_reviewer_score_one(tmp_path: Path) -> None:
    cases, errors = generate_cases(KNOWLEDGE, "p1", template_generator, base_dir=tmp_path)
    assert errors == {} and len(cases) == 2
    report = run_knowledge_eval(
        oracle_provider(cases), KNOWLEDGE, "p1", generator=template_generator, base_dir=tmp_path
    )
    assert (report.recall, report.precision, report.passed) == (1.0, 1.0, True)
    assert [r.matched_by for r in report.results] == ["rule_id", "rule_id"]
    assert all(r.clean_false_positives == 0 for r in report.results)


def test_cached_cases_are_not_regenerated(tmp_path: Path) -> None:
    calls: list[str] = []

    def counting(invariant: InvariantDict, language: str) -> dict[str, Any]:
        calls.append(invariant["id"])
        return template_generator(invariant, language)

    first = run_knowledge_eval(
        FakeLLMProvider(), KNOWLEDGE, "p1", generator=counting, base_dir=tmp_path
    )
    second = run_knowledge_eval(
        FakeLLMProvider(), KNOWLEDGE, "p1", generator=counting, base_dir=tmp_path
    )
    assert calls == ["inv-validate-input", "inv-no-bare-return"]
    assert (first.generated, second.generated) == (2, 0)
    directory = tmp_path / "p1" / case_key(KNOWLEDGE["invariants"][0])
    assert {p.name for p in directory.iterdir()} == {
        "violating.patch",
        "clean.patch",
        "expected.json",
    }


def test_an_edited_statement_gets_a_new_case_directory() -> None:
    original = KNOWLEDGE["invariants"][0]
    edited: InvariantDict = {**original, "statement": original["statement"] + " Always."}
    assert case_key(original) != case_key(edited)
    assert case_key(original).startswith("inv-validate-input-")


def test_a_silent_reviewer_has_zero_recall(tmp_path: Path) -> None:
    report = run_knowledge_eval(
        FakeLLMProvider(), KNOWLEDGE, "p1", generator=template_generator, base_dir=tmp_path
    )
    assert report.recall == 0.0 and report.precision == 1.0 and not report.passed


def test_findings_on_the_expected_lines_count_without_the_rule_id(tmp_path: Path) -> None:
    cases, _ = generate_cases(KNOWLEDGE, "p1", template_generator, base_dir=tmp_path)

    def by_lines(prompt: ReviewPrompt) -> dict[str, Any]:
        for case in cases:
            if f"MR: {case.violating.description}" in prompt.user:
                e = case.violating.expected[0]
                return {
                    "summary": "s",
                    "score": 50,
                    "findings": [{**e.__dict__, "title": "t", "message": "m", "confidence": 0.9}],
                }
        return {"summary": "s", "score": 100, "findings": []}

    report = run_knowledge_eval(
        FakeLLMProvider(by_lines), KNOWLEDGE, "p1", generator=template_generator, base_dir=tmp_path
    )
    assert report.recall == 1.0 and [r.matched_by for r in report.results] == ["lines", "lines"]


def test_false_positives_on_clean_diffs_lower_precision(tmp_path: Path) -> None:
    cases, _ = generate_cases(KNOWLEDGE, "p1", template_generator, base_dir=tmp_path)
    oracle = oracle_provider(cases)

    def noisy(prompt: ReviewPrompt) -> Any:
        answer = oracle.review(prompt).output
        if "(clean)" in prompt.user:
            answer["findings"] = [
                {
                    "severity": "high",
                    "category": "bug",
                    "file_path": cases[0].clean.id and "app/generated/inv_validate_input.py",
                    "line_start": 2,
                    "line_end": 2,
                    "title": "noise",
                    "message": "noise",
                    "confidence": 0.9,
                }
            ]
        return answer

    report = run_knowledge_eval(
        FakeLLMProvider(noisy), KNOWLEDGE, "p1", generator=template_generator, base_dir=tmp_path
    )
    # One severe true positive (the second invariant is only "medium") against one false
    # positive: the second clean diff names a file outside its own diff, so that one is dropped.
    assert report.recall == 1.0
    assert report.precision == pytest.approx(1 / 2)
    assert not report.passed
    assert report.to_dict()["invariants"][0]["clean_false_positives"] == 1


def test_a_generator_answer_without_patches_is_reported_not_raised(tmp_path: Path) -> None:
    def broken(invariant: InvariantDict, language: str) -> dict[str, Any]:
        return {"file_path": "x.py"}

    report = run_knowledge_eval(
        FakeLLMProvider(), KNOWLEDGE, "p1", generator=broken, base_dir=tmp_path
    )
    assert len(report.results) == 2 and not report.passed
    assert all("GenerationError" in (r.error or "") for r in report.results)
    assert not (tmp_path / "p1").exists() or not list((tmp_path / "p1").iterdir())


def test_violating_lines_must_be_added_lines() -> None:
    answer = template_generator(KNOWLEDGE["invariants"][0], "python")
    answer["violating_line_start"] = answer["violating_line_end"] = 99
    with pytest.raises(GenerationError, match="not added lines"):
        validate_generated(KNOWLEDGE["invariants"][0], answer)


def test_llm_generator_accepts_a_json_string_and_asks_with_the_schema() -> None:
    expected = template_generator(KNOWLEDGE["invariants"][0], "python")
    provider = FakeLLMProvider(lambda prompt: json.dumps(expected))
    answer = llm_generator(provider)(KNOWLEDGE["invariants"][0], "python")
    assert answer == expected
    prompt = provider.prompts[0]
    assert "violating_patch" in prompt.json_schema["required"]
    assert "inv-validate-input" in prompt.user and "def handler, payload" in prompt.user


def test_the_reviewer_sees_the_rules_as_project_knowledge(tmp_path: Path) -> None:
    provider = FakeLLMProvider()
    run_knowledge_eval(provider, KNOWLEDGE, "p1", generator=template_generator, base_dir=tmp_path)
    assert all("[rk:inv-validate-input] (high)" in p.user for p in provider.prompts)


def test_report_file_name_carries_the_project(tmp_path: Path) -> None:
    report = run_knowledge_eval(
        FakeLLMProvider(), KNOWLEDGE, "p1", generator=template_generator, base_dir=tmp_path
    )
    out = write_report(report, tmp_path / "results")
    assert out.name.startswith("knowledge-p1-") and out.suffix == ".json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["project_id"] == "p1" and data["recall_per_invariant"] == 0.0
    assert data["invariants"][0]["id"] == "inv-validate-input"


def test_cli_fake_harness_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    knowledge_file = tmp_path / "knowledge.json"
    knowledge_file.write_text(json.dumps(KNOWLEDGE), encoding="utf-8")
    assert main(["--project", "p1", "--fake", "--knowledge", str(knowledge_file)]) == 0
    assert (tmp_path / "tests/eval/generated/p1").is_dir()
    reports = list((tmp_path / "tests/eval/results").glob("knowledge-p1-*.json"))
    assert len(reports) == 1 and json.loads(reports[0].read_text(encoding="utf-8"))["passed"]


def test_cli_project_without_knowledge_module_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    def no_knowledge(name: str) -> Any:
        raise ImportError("No module named 'app.review.knowledge'")

    monkeypatch.setattr(importlib, "import_module", no_knowledge)
    with pytest.raises(SystemExit, match="load_active_dict"):
        main(["--project", "p1", "--fake"])
