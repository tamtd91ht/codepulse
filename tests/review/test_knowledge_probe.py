"""The code-probe layer of a knowledge build (docs/specs/19 §3.2): prompt, cleaning of the
answer against the checkout, and the merge into a knowledge dict. A fake agent answers."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.review.knowledge import Knowledge
from app.review.knowledge_probe import (
    MAX_CONVENTIONS,
    MAX_MODULES,
    PROBE_QUESTIONS,
    TOOLS,
    build_probe_prompt,
    evidence_ok,
    merge_probe,
    module_ok,
    parse_probe,
    probe_code,
)
from app.review.prepare import REDACTED
from app.review.providers.base import ReviewPrompt, ReviewResult


class FakeAgent:
    name = "fake"

    def __init__(self, output: Any) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def review_in_repo(
        self,
        prompt: ReviewPrompt,
        workdir: str,
        *,
        tools: Sequence[str],
        max_turns: int,
        timeout_s: float,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> ReviewResult:
        self.calls.append(
            {"prompt": prompt, "workdir": workdir, "tools": tuple(tools), "max_turns": max_turns}
        )
        return ReviewResult(output=self.output, model="fake", cost_usd=0.2, raw={"num_turns": 7})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app" / "db").mkdir(parents=True)
    (tmp_path / "app" / "db" / "session.py").write_text("def session(): ...\n" * 4)
    (tmp_path / "app" / "http.py").write_text("TIMEOUT = 10\n" * 5)
    (tmp_path / ".codepulse" / "claude-shared").mkdir(parents=True)
    (tmp_path / ".codepulse" / "claude-shared" / "shared.md").write_text("shared")
    (tmp_path / ".codepulse" / "note.md").write_text("ours")
    return tmp_path


KNOWLEDGE: dict[str, Any] = {
    "domain": {
        "summary": "CRM đa kênh",
        "tenancy": "theo tenant_id",
        "stack": ["FastAPI"],
        "critical_modules": [{"path": "app/billing", "why": "tiền"}],
    },
    "invariants": [{"id": "inv-1", "statement": "Mọi truy vấn lọc theo tenant_id"}],
    "code_conventions": [
        {"id": "conv-1", "statement": "Repository methods take tenant_id", "evidence": ["x:1"]}
    ],
}


def test_prompt_carries_questions_and_layer_one() -> None:
    prompt = build_probe_prompt(KNOWLEDGE, "demo/p")
    assert "Read, Grep and Glob" in prompt.system
    assert "demo/p" in prompt.user
    assert "CRM đa kênh" in prompt.user and "tenant_id" in prompt.user
    assert "app/billing" in prompt.user
    assert all(q.split(":")[0] in prompt.user for q in PROBE_QUESTIONS)
    assert "7." in prompt.user
    assert prompt.json_schema["properties"]["code_conventions"]["maxItems"] == MAX_CONVENTIONS
    assert prompt.json_schema["properties"]["critical_modules"]["maxItems"] == MAX_MODULES


def test_prompt_redacts_secrets_from_layer_one() -> None:
    leaky = {"domain": {"summary": "token = 'abcdef123456' and glpat-ABCDEFGHIJKLMNOPQRSTUV"}}
    prompt = build_probe_prompt(leaky, "p")
    assert "abcdef123456" not in prompt.user and "glpat-ABCDEFGHIJ" not in prompt.user


def test_evidence_must_point_at_a_file_in_the_checkout(repo: Path) -> None:
    assert evidence_ok(repo, "app/http.py:3") == "app/http.py:3"
    assert evidence_ok(repo, "app\\db\\session.py:1-4") == "app/db/session.py:1-4"
    assert evidence_ok(repo, "/app/http.py:3") == "app/http.py:3"
    assert evidence_ok(repo, "app/missing.py:3") is None
    assert evidence_ok(repo, "app/http.py") is None  # no lines
    assert evidence_ok(repo, "app/db:1") is None  # a directory
    assert evidence_ok(repo, "../outside.py:1") is None
    # the shared Claude configuration mounted into the checkout is not the repository
    assert evidence_ok(repo, ".codepulse/claude-shared/shared.md:1") is None
    assert evidence_ok(repo, "./.codepulse/claude-shared/shared.md:1") is None
    assert evidence_ok(repo, "app/../.codepulse/claude-shared/shared.md:1") is None
    assert evidence_ok(repo, ".CODEPULSE/claude-shared/shared.md:1") is None
    assert module_ok(repo, ".codepulse/claude-shared") is None
    assert evidence_ok(repo, ".codepulse/note.md:1") == ".codepulse/note.md:1"
    # line numbers must exist in the file (app/http.py has five lines)
    assert evidence_ok(repo, "app/http.py:1-1") == "app/http.py:1-1"
    assert evidence_ok(repo, "app/http.py:6") is None
    assert evidence_ok(repo, "app/http.py:1-9") is None
    assert evidence_ok(repo, "app/http.py:0") is None
    assert evidence_ok(repo, "app/db/session.py:1-0") is None
    assert module_ok(repo, "app/db") == "app/db"
    assert module_ok(repo, "app/http.py") == "app/http.py"
    assert module_ok(repo, "nope") is None
    assert module_ok(repo, "../") is None


def test_parse_cleans_caps_and_drops_unsupported_items(repo: Path) -> None:
    output = {
        "code_conventions": [
            {
                "statement": "HTTP calls use app/http.py with TIMEOUT",
                "evidence": ["app/http.py:1", "app/http.py:1", "ghost.py:2"],
                "confidence": 1.7,
            },
            {"statement": "HTTP calls use app/http.py with TIMEOUT", "evidence": ["app/http.py:1"]},
            {"statement": "No evidence survives", "evidence": ["ghost.py:1"], "confidence": 0.9},
            {"statement": "", "evidence": ["app/http.py:1"]},
            "junk",
            {
                "statement": "Password is password = 'hunter2secret'",
                "evidence": ["app/db/session.py:1"],
                "confidence": "0.4",
            },
        ]
        + [
            {"statement": f"Convention {n}", "evidence": ["app/http.py:1"], "confidence": 0.5}
            for n in range(40)
        ],
        "critical_modules": [
            {"path": "app/db", "why": "sessions"},
            {"path": "app/db/", "why": "again"},
            {"path": "missing", "why": "x"},
            {"path": "app/http.py", "why": ""},
            7,
        ]
        + [{"path": "app/http.py", "why": f"m{n}"} for n in range(20)],
    }
    probe = parse_probe(output, repo)
    conventions = probe["code_conventions"]
    assert len(conventions) == MAX_CONVENTIONS
    first = conventions[0]
    assert first["id"] == "code-1" and first["evidence"] == ["app/http.py:1"]
    assert first["confidence"] == 1.0 and first["authority"] == "repo"
    statements = [c["statement"] for c in conventions]
    assert statements.count("HTTP calls use app/http.py with TIMEOUT") == 1
    assert "No evidence survives" not in statements
    assert any(REDACTED in s for s in statements)
    assert not any("hunter2secret" in s for s in statements)
    assert conventions[1]["confidence"] == 0.4
    modules = probe["critical_modules"]
    assert modules[0] == {"path": "app/db", "why": "sessions"}
    assert [m["path"] for m in modules] == ["app/db", "app/http.py"]


def test_parse_accepts_json_text_and_rejects_non_objects(repo: Path) -> None:
    text = '{"code_conventions": [], "critical_modules": []}'
    assert parse_probe(text, repo) == {"code_conventions": [], "critical_modules": []}
    with pytest.raises(ValueError):
        parse_probe("not json", repo)
    with pytest.raises(ValueError):
        parse_probe(["list"], repo)


def test_probe_code_runs_the_agent_in_the_checkout(repo: Path) -> None:
    agent = FakeAgent(
        {
            "code_conventions": [
                {
                    "statement": "Sessions come from app/db/session.py",
                    "evidence": ["app/db/session.py:1"],
                    "confidence": 0.8,
                }
            ],
            "critical_modules": [{"path": "app/db", "why": "every query"}],
        }
    )
    probe, result = probe_code(agent, repo, KNOWLEDGE, "demo/p", max_turns=12, timeout_s=30)
    (call,) = agent.calls
    assert call["workdir"] == str(repo) and call["tools"] == tuple(TOOLS)
    assert call["max_turns"] == 12
    assert result.cost_usd == 0.2 and result.raw["num_turns"] == 7
    assert probe["code_conventions"][0]["statement"] == "Sessions come from app/db/session.py"
    assert probe["critical_modules"] == [{"path": "app/db", "why": "every query"}]


def test_merge_appends_without_duplicates_and_validates() -> None:
    probe = {
        "code_conventions": [
            {
                "id": "code-1",
                "statement": "repository methods take tenant_id",
                "evidence": ["a:1"],
                "confidence": 0.9,
            },
            {
                "id": "code-2",
                "statement": "HTTP via app/http.py",
                "evidence": ["app/http.py:1"],
                "confidence": 0.7,
            },
        ],
        "critical_modules": [
            {"path": "app/billing/", "why": "dup"},
            {"path": "app/db", "why": "sessions"},
        ],
    }
    merged = merge_probe(KNOWLEDGE, probe)
    assert [c["statement"] for c in merged["code_conventions"]] == [
        "Repository methods take tenant_id",
        "HTTP via app/http.py",
    ]
    assert [m["path"] for m in merged["domain"]["critical_modules"]] == ["app/billing", "app/db"]
    assert merged["domain"]["summary"] == "CRM đa kênh"
    # the input is left alone
    assert len(KNOWLEDGE["code_conventions"]) == 1
    assert len(KNOWLEDGE["domain"]["critical_modules"]) == 1
    knowledge = Knowledge.model_validate(merged)
    assert knowledge.counts()["code_conventions"] == 2
    assert {c.id for c in knowledge.code_conventions} == {"conv-1", "code-2"}


def test_merge_respects_caps_and_missing_sections() -> None:
    probe = {
        "code_conventions": [
            {"statement": f"c{n}", "evidence": ["x:1"], "confidence": 0.5} for n in range(30)
        ],
        "critical_modules": [{"path": f"m{n}", "why": "w"} for n in range(30)],
    }
    merged = merge_probe({}, probe)
    assert len(merged["code_conventions"]) == MAX_CONVENTIONS
    assert len(merged["domain"]["critical_modules"]) == 20
    assert merge_probe({"domain": "broken"}, {}) == {
        "domain": {"critical_modules": []},
        "code_conventions": [],
    }
