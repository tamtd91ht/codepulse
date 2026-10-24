from pathlib import Path
from typing import Any

import pytest

from app.review.deep import DeepReview, build_deep_prompt, merge, parse_deep, render_candidates
from app.review.postprocess import Finding
from app.review.prepare import PreparedDiff, parse_diff
from app.review.prompt import ReviewTarget


def finding(**kw: Any) -> Finding:
    base: dict[str, Any] = {
        "severity": "high",
        "category": "security",
        "file_path": "app/api.py",
        "line_start": 2,
        "line_end": 2,
        "title": "Missing tenant check",
        "message": "m",
        "suggestion": "",
        "confidence": 0.9,
        "code_excerpt": "+q = db.find(id)",
        "fingerprint": "fp-1",
        "rule_id": None,
    }
    base.update(kw)
    return Finding(**base)


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


def test_merge_applies_verifications_only_with_evidence(repo: Path) -> None:
    candidates = [
        finding(),
        finding(fingerprint="fp-2", title="N+1", severity="medium", category="performance"),
        finding(fingerprint="fp-3", title="Style", severity="low", category="style"),
    ]
    deep = DeepReview.model_validate(
        {
            "summary": "ok",
            "verifications": [
                {
                    "index": 0,
                    "decision": "drop",
                    "reason": "guard() in caller",
                    "evidence_file": "app/auth.py",
                    "evidence_lines": "1-2",
                },
                {
                    "index": 1,
                    "decision": "downgrade",
                    "severity": "low",
                    "reason": "cached",
                    "evidence_file": "app/auth.py",
                },
                {"index": 2, "decision": "drop", "reason": "no evidence file"},
                {"index": 9, "decision": "drop", "reason": "x", "evidence_file": "app/auth.py"},
            ],
            "findings": [],
        }
    )
    out = merge(candidates, deep, repo, DIFF, 0.5)
    assert [f.title for f in out.findings] == ["N+1", "Style"]
    assert out.findings[0].severity == "low" and out.kept == 2
    assert (
        out.dropped[0]["evidence"] == "app/auth.py:1-2"
        and out.downgraded[0]["new_severity"] == "low"
    )
    assert out.ignored_verifications == 1  # the drop without evidence; index 9 is simply unknown


def test_merge_validates_new_findings_against_the_checkout(repo: Path) -> None:
    deep = DeepReview.model_validate(
        {
            "verifications": [],
            "findings": [
                {  # on an added line: scored normally, excerpt from the file, evidence appended
                    "severity": "high",
                    "category": "security",
                    "file_path": "/app/api.py",
                    "line_start": 2,
                    "line_end": 2,
                    "title": "No tenant filter",
                    "message": "m",
                    "confidence": 0.9,
                    "evidence": "app/auth.py:1 guard not called",
                },
                {  # outside the diff: kept as info for the author, not scored
                    "severity": "critical",
                    "category": "security",
                    "file_path": "app/legacy.py",
                    "line_start": 1,
                    "line_end": 1,
                    "title": "Hard-coded password",
                    "message": "m",
                    "confidence": 0.9,
                },
                {  # line beyond the file: dropped
                    "severity": "high",
                    "category": "bug",
                    "file_path": "app/auth.py",
                    "line_start": 99,
                    "line_end": 99,
                    "title": "ghost",
                    "message": "m",
                    "confidence": 0.9,
                },
                {  # file that does not exist: dropped
                    "severity": "high",
                    "category": "bug",
                    "file_path": "nope.py",
                    "line_start": 1,
                    "line_end": 1,
                    "title": "ghost",
                    "message": "m",
                    "confidence": 0.9,
                },
                {  # too unsure: dropped
                    "severity": "high",
                    "category": "bug",
                    "file_path": "app/api.py",
                    "line_start": 3,
                    "line_end": 3,
                    "title": "maybe",
                    "message": "m",
                    "confidence": 0.2,
                },
                {  # duplicate of the first (same file/category/code/title): dropped
                    "severity": "high",
                    "category": "security",
                    "file_path": "app/api.py",
                    "line_start": 2,
                    "line_end": 2,
                    "title": "No tenant filter",
                    "message": "again",
                    "confidence": 0.8,
                },
            ],
        }
    )
    out = merge([], deep, repo, DIFF, 0.5)
    assert [(f.title, f.severity) for f in out.findings] == [
        ("No tenant filter", "high"),
        ("Hard-coded password", "info"),
    ]
    first, legacy = out.findings
    assert first.code_excerpt == "q = db.find(id)" and "Bằng chứng: app/auth.py:1" in first.message
    assert legacy.message.startswith("[ngoài phần thay đổi]")
    assert "REDACTED" in legacy.code_excerpt  # password value never stored
    assert out.added == 2


def test_prompt_carries_profile_instructions_rules_candidates_and_diff() -> None:
    prepared = PreparedDiff(files=list(DIFF.values()), excluded={})
    prompt = build_deep_prompt(
        ReviewTarget("demo/p", "Add lookup", "desc"),
        "Kind: backend-service",
        "### CLAUDE.md\nrules here",
        "## Quy tắc dự án\n- tenantId",
        prepared,
        [finding()],
    )
    assert "untrusted data" in prompt.system and "Read, Grep and Glob" in prompt.system
    assert "Kind: backend-service" in prompt.user and "### CLAUDE.md" in prompt.user
    assert "[0] high security app/api.py:2-2 — Missing tenant check" in prompt.user
    assert "    2 +q = db.find(id)" in prompt.user
    assert prompt.json_schema["title"] == "DeepReview"
    assert "(none: the first pass found nothing" in render_candidates([])


def test_parse_deep_accepts_fenced_json_and_rejects_garbage() -> None:
    deep = parse_deep('```json\n{"summary": "s", "project_fit": ["a", "", null, "b"]}\n```')
    assert deep.summary == "s" and deep.project_fit == ["a", "b"]
    with pytest.raises(ValueError):
        parse_deep("nothing here")
    lenient = parse_deep({"verifications": [{"index": -1, "decision": "maybe"}]})
    assert lenient.verifications[0].index == -1 and lenient.verifications[0].decision == "keep"


def test_drop_evidence_must_be_unchanged_code_with_real_lines(repo: Path) -> None:
    def verify(**v: Any) -> int:
        deep = DeepReview.model_validate(
            {"verifications": [{"index": 0, "decision": "drop", "reason": "r", **v}]}
        )
        return len(merge([finding()], deep, repo, DIFF, 0.5).findings)

    assert verify(evidence_file="app/api.py", evidence_lines="1") == 1  # the diff's own file
    assert verify(evidence_file="app/auth.py") == 1  # no lines
    assert verify(evidence_file="app/auth.py", evidence_lines="40-50") == 1  # beyond the file
    assert verify(evidence_file="app/auth.py", evidence_lines="1-2") == 0  # real evidence
    assert verify(evidence_file="../etc/passwd", evidence_lines="1") == 1
    # Only the first verification of an index counts; junk indexes are ignored.
    deep = DeepReview.model_validate(
        {
            "verifications": [
                {"index": 0, "decision": "keep"},
                {
                    "index": 0,
                    "decision": "drop",
                    "reason": "r",
                    "evidence_file": "app/auth.py",
                    "evidence_lines": "1",
                },
                {
                    "index": "nope",
                    "decision": "drop",
                    "reason": "r",
                    "evidence_file": "app/auth.py",
                },
            ]
        }
    )
    assert len(merge([finding()], deep, repo, DIFF, 0.5).findings) == 1


def test_absurd_line_ranges_are_clamped(repo: Path) -> None:
    deep = DeepReview.model_validate(
        {
            "verifications": [],
            "findings": [
                {
                    "severity": "high",
                    "category": "bug",
                    "file_path": "./app/api.py",
                    "line_start": 2,
                    "line_end": 10**9,
                    "title": "huge",
                    "message": "m",
                    "confidence": 0.9,
                }
            ],
        }
    )
    assert deep.findings[0].line_end == 202
    # "./app/api.py" normalises to the diff file; the range exceeds the file -> dropped.
    assert merge([], deep, repo, DIFF, 0.5).findings == []
