from app.models import BusinessProject, Project
from app.review.rules import MAX_FILE_CHARS, MAX_RULES_CHARS, build_rules, rule_paths


def test_rule_paths_are_relative_deduped_and_capped() -> None:
    spec = "/.claude/rules/a.md, docs/rules.md\n.claude/rules/a.md,,../secret, x/../y, "
    assert rule_paths(spec) == [".claude/rules/a.md", "docs/rules.md"]
    assert rule_paths(None) == [] and rule_paths("") == []
    assert len(rule_paths(",".join(f"r{i}.md" for i in range(30)))) == 10


def test_build_rules_merges_the_three_sources_with_headings() -> None:
    bp = BusinessProject(name="OmiCRM", review_rules="- tenantId on every query")
    project = Project(
        external_id=1,
        path_with_namespace="demo/api",
        review_rules="- no blocking calls in verticles",
        review_rules_paths=".claude/rules/money.md, missing.md",
    )
    files = {".claude/rules/money.md": "# Money safety\nidempotency keys"}
    text = build_rules(bp, project, files.get)
    assert text.index("## Quy tắc dự án OmiCRM") < text.index("## Quy tắc repo demo/api")
    assert "## .claude/rules/money.md\n# Money safety" in text
    assert "missing.md" not in text
    assert build_rules(None, Project(external_id=1, path_with_namespace="x"), files.get) == ""


def test_intent_and_rule_id_are_clipped_not_rejected() -> None:
    from app.review.schema import Intent, LLMFinding

    intent = Intent.model_validate(
        {"summary": "x" * 2000, "altitude": "sideways", "concerns": ["a"] * 7 + ["", None]}
    )
    assert len(intent.summary) == 1500 and intent.altitude is None and intent.concerns == ["a"] * 5
    finding = LLMFinding(
        severity="low",
        category="style",
        file_path="a.py",
        line_start=1,
        line_end=1,
        title="t",
        message="m",
        confidence=0.9,
        rule_id="r" * 500,
    )
    assert finding.rule_id is not None and len(finding.rule_id) == 120


def test_build_rules_survives_fetch_errors_and_clips_large_text() -> None:
    def boom(path: str) -> str | None:
        raise RuntimeError("gitlab down")

    project = Project(
        external_id=1,
        path_with_namespace="demo/api",
        review_rules="x" * (MAX_RULES_CHARS * 2),
        review_rules_paths="a.md",
    )
    text = build_rules(None, project, boom)
    assert "(… rút gọn)" in text and len(text) <= MAX_RULES_CHARS
    big = build_rules(
        None,
        Project(external_id=1, path_with_namespace="p", review_rules_paths="big.md"),
        lambda p: "y" * (MAX_FILE_CHARS + 100),
    )
    assert big.count("y") == MAX_FILE_CHARS
