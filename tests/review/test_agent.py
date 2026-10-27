from pathlib import Path
from typing import Any

import pytest

from app.review.agent import (
    agent_findings,
    build_agent_prompt,
    context_reader,
    parse_agent,
    resolve,
    review_chunk_in_repo,
)
from app.review.prepare import PreparedDiff, chunk, parse_diff
from app.review.prompt import ReviewParseError, ReviewTarget
from app.review.providers import FakeLLMProvider, ReviewPrompt
from app.review.rubric import RUBRIC
from app.review.schema import AgentFinding


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "api.py").write_text("import db\nq = db.find(id)\nreturn q\n")
    (tmp_path / "app" / "auth.py").write_text("def guard(ctx):\n    assert ctx.tenant\n" * 3)
    (tmp_path / "app" / "legacy.py").write_text("password = 'supersecret123'\n" + "y = 1\n" * 10)
    return tmp_path


DIFF = {
    "app/api.py": parse_diff(
        "app/api.py", "@@ -1,1 +1,3 @@\n import db\n+q = db.find(id)\n+return q\n"
    )
}


def raw(**kw: Any) -> AgentFinding:
    base: dict[str, Any] = {
        "severity": "high",
        "category": "security",
        "file_path": "app/api.py",
        "line_start": 2,
        "line_end": 2,
        "title": "Missing tenant check",
        "message": "m",
        "confidence": 0.9,
        "evidence": "app/auth.py:1",
    }
    base.update(kw)
    return AgentFinding.model_validate(base)


def test_prompt_carries_knowledge_checklist_rules_diff_and_context(repo: Path) -> None:
    prepared = PreparedDiff(files=list(DIFF.values()), excluded={})
    (piece,) = chunk(prepared, 60_000, context_reader(repo))
    target = ReviewTarget(
        "demo/p",
        "Add lookup",
        "desc",
        conventions="## Quy tắc\n- tenantId",
        knowledge="## Invariants\n- rk:inv-1: tenantId on every query",
        guidance="### CLAUDE.md\nquoted guidance",
    )
    prompt = build_agent_prompt(target, piece)
    assert RUBRIC in prompt.system and "Tiền đề của dự án" in prompt.system
    assert "Read, Grep and Glob" in prompt.system and "untrusted data" in prompt.system
    assert "MR: Add lookup\ndesc" in prompt.user and "Languages: Python" in prompt.user
    assert "PROJECT KNOWLEDGE (compiled review knowledge; cite rk:<id>):\n## Invariants" in (
        prompt.user
    )
    # With knowledge the .claude guidance is not quoted (docs/specs/19 §4.2).
    assert "(none: see PROJECT KNOWLEDGE)" in prompt.user and "quoted guidance" not in prompt.user
    assert "### Python" in prompt.user and "## Quy tắc\n- tenantId" in prompt.user
    assert "    2 +q = db.find(id)" in prompt.user
    assert "### Context: app/api.py" in prompt.user  # ±30 lines read from the checkout
    assert prompt.json_schema["title"] == "AgentReview"
    assert "applied_rules" in prompt.json_schema["properties"]

    # Without knowledge the guidance path of docs/specs/17/18 still works (lộ trình step 1).
    fallback = build_agent_prompt(ReviewTarget("demo/p", "t", guidance="### CLAUDE.md\nq"), piece)
    assert "(none yet)" in fallback.user and "### CLAUDE.md\nq" in fallback.user


def test_parse_agent_defaults_the_rule_and_caps_confidence_without_evidence() -> None:
    review = parse_agent(
        '```json\n{"summary": "s", "findings": [{"severity": "critical", "category": "security",'
        ' "file_path": "a.py", "line_start": 1, "line_end": 1, "title": "t", "message": "m",'
        ' "confidence": 0.95}, {"severity": "high", "category": "bug", "file_path": "a.py",'
        ' "line_start": 1, "line_end": 1, "title": "t2", "message": "m", "confidence": 0.95,'
        ' "rule_id": "rk:x", "evidence": "a.py:1"}, {"severity": "low", "category": "style",'
        ' "file_path": "a.py", "line_start": 1, "line_end": 1, "title": "t3", "message": "m",'
        ' "confidence": 0.95, "rule_id": ""}], "applied_rules": [{"rule_id": "rk:x",'
        ' "applies": "yes", "where": "a.py:1"}, {"applies": true}, "junk"],'
        ' "project_fit": ["a", "", null]}\n```'
    )
    first, second, third = review.findings
    assert (first.rule_id, first.confidence) == ("rubric:4", 0.6)  # critical without evidence
    assert (second.rule_id, second.confidence) == ("rk:x", 0.95)
    assert (third.rule_id, third.confidence) == ("rubric:4", 0.95)  # low: no evidence needed
    assert [(r.rule_id, r.applies, r.where) for r in review.applied_rules] == [
        ("rk:x", True, "a.py:1")
    ]
    assert review.project_fit == ["a"] and review.score == 0
    with pytest.raises(ReviewParseError):
        parse_agent("nothing here")


VALID = {"summary": "s", "findings": []}


def sequence(*outputs: Any) -> FakeLLMProvider:
    queue = list(outputs)
    return FakeLLMProvider(responder=lambda prompt: queue.pop(0))


PROMPT = ReviewPrompt(system="s", user="u", json_schema={})


def test_review_chunk_in_repo_repairs_once_with_the_plain_provider() -> None:
    agent = sequence({"summary": 1, "findings": "x"})
    repairer = sequence(VALID)
    result = review_chunk_in_repo(agent, repairer, PROMPT, "repo-dir", max_turns=5, timeout_s=1)
    assert result.repaired and len(result.calls) == 2
    assert "did not match the schema" in repairer.prompts[0].user
    assert agent.repo_calls[0]["max_turns"] == 5 and agent.repo_calls[0]["workdir"] == "repo-dir"


def test_review_chunk_in_repo_gives_up_after_one_repair() -> None:
    agent = sequence("garbage")
    repairer = sequence("still garbage", VALID)
    with pytest.raises(ReviewParseError, match="invalid after repair"):
        review_chunk_in_repo(agent, repairer, PROMPT, "repo-dir", max_turns=5, timeout_s=1)
    assert len(repairer.prompts) == 1


def test_agent_findings_keep_diff_findings_and_note_the_rest(repo: Path) -> None:
    out = agent_findings(
        [
            raw(),  # on an added line of a diff file: scored, evidence appended
            raw(line_start=1, line_end=1, title="ctx", evidence=""),  # unchanged diff line: info
            raw(  # outside the diff, file and lines exist: info note, excerpt from disk
                file_path="/app/legacy.py",
                line_start=1,
                line_end=1,
                severity="critical",
                title="Hard-coded password",
            ),
            raw(file_path="app/auth.py", line_start=99, line_end=99, title="ghost"),  # beyond
            raw(file_path="nope.py", line_start=1, line_end=1, title="ghost2"),  # no file
            raw(file_path="app/auth.py", line_start=1, line_end=1, title="unsure", confidence=0.2),
            raw(file_path="../etc/passwd", line_start=1, line_end=1, title="escape"),
        ],
        DIFF,
        repo,
        0.5,
    )
    assert [(f.title, f.severity) for f in out.findings] == [
        ("Missing tenant check", "high"),
        ("ctx", "info"),  # severity, then path: app/api.py before app/legacy.py
        ("Hard-coded password", "info"),
    ]
    first, ctx, legacy = out.findings
    assert first.code_excerpt == "+q = db.find(id)" and "Bằng chứng: app/auth.py:1" in first.message
    assert legacy.message.startswith("[ngoài phần thay đổi]") and legacy.rule_id == "rubric:4"
    assert "REDACTED" in legacy.code_excerpt  # password value never stored
    assert ctx.severity == "info"
    assert out.dropped["file_not_in_diff"] == 2 and out.dropped["low_confidence"] == 1
    assert out.dropped["outside_diff_as_info"] == 1


def test_context_reader_serves_repository_files_only(repo: Path) -> None:
    read = context_reader(repo)
    assert read("app/api.py") == "import db\nq = db.find(id)\nreturn q\n"
    assert read("/app/api.py") is not None and read("./app/api.py") is not None
    assert read("nope.py") is None and read("../etc/passwd") is None and read("app") is None


@pytest.mark.parametrize(
    "path",
    [
        ".codepulse/claude-shared/rules/x.md",
        "./.codepulse/claude-shared/rules/x.md",
        ".codepulse//claude-shared/rules/x.md",
        "app/../.codepulse/claude-shared/rules/x.md",
    ],
)
def test_the_shared_kit_is_never_a_repository_file(repo: Path, path: str) -> None:
    kit = repo / ".codepulse" / "claude-shared" / "rules"
    kit.mkdir(parents=True)
    (kit / "x.md").write_text("a\nb\n")
    assert resolve(repo, path) is None
    assert resolve(repo, "app/api.py") is not None
