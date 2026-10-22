from typing import Any

from app.review.postprocess import MAX_EXCERPT_LINES, fingerprint, postprocess
from app.review.prepare import parse_diff
from app.review.schema import LLMFinding

DIFF = """@@ -10,3 +10,4 @@
 conn = db()
-q = "SELECT 1"
+q = "SELECT * FROM users WHERE name = '" + user + "'"
+rows = conn.execute(q)
 return rows
"""
FILES = {"app/auth.py": parse_diff("app/auth.py", DIFF)}


def finding(**overrides: Any) -> LLMFinding:
    base = {
        "severity": "critical",
        "category": "security",
        "file_path": "app/auth.py",
        "line_start": 11,
        "line_end": 12,
        "title": "SQL injection",
        "message": "m",
        "suggestion": "s",
        "confidence": 0.9,
    }
    return LLMFinding.model_validate({**base, **overrides})


def test_valid_finding_gets_excerpt_and_fingerprint() -> None:
    (f,) = postprocess([finding()], FILES, 0.5).findings
    assert f.severity == "critical"
    assert f.code_excerpt.splitlines() == [
        '+q = "SELECT * FROM users WHERE name = \'" + user + "\'"',
        "+rows = conn.execute(q)",
    ]
    assert len(f.fingerprint) == 64


def test_drops_low_confidence_unknown_files_and_lines_outside_hunks() -> None:
    result = postprocess(
        [
            finding(confidence=0.3),
            finding(file_path="other.py"),
            finding(line_start=200, line_end=201),
        ],
        FILES,
        0.5,
    )
    assert result.findings == []
    assert result.dropped == {"low_confidence": 1, "file_not_in_diff": 1, "line_not_in_diff": 1}


def test_context_line_is_downgraded_to_info() -> None:
    (f,) = postprocess([finding(line_start=10, line_end=10)], FILES, 0.5).findings
    assert f.severity == "info"


def test_duplicates_across_chunks_keep_the_strongest() -> None:
    result = postprocess(
        [finding(severity="high", confidence=0.95), finding(), finding(confidence=0.6)], FILES, 0.5
    )
    (f,) = result.findings
    assert (f.severity, f.confidence) == ("critical", 0.9)
    assert result.dropped["duplicate"] == 2


def test_fingerprint_ignores_line_numbers_and_whitespace() -> None:
    assert fingerprint("a.py", "bug", "x  =  1", "t") == fingerprint("a.py", "bug", "x = 1", "t")
    assert fingerprint("a.py", "bug", "x = 1", "t") != fingerprint("a.py", "security", "x = 1", "t")


def test_excerpt_is_capped() -> None:
    body = "".join(f"+line {i}\n" for i in range(100))
    files = {"big.py": parse_diff("big.py", f"@@ -0,0 +1,100 @@\n{body}")}
    (f,) = postprocess(
        [finding(file_path="big.py", line_start=1, line_end=100)], files, 0.5
    ).findings
    assert len(f.code_excerpt.splitlines()) == MAX_EXCERPT_LINES


def test_findings_sorted_by_severity() -> None:
    result = postprocess(
        [
            finding(severity="low", title="a", line_start=12, line_end=12, category="style"),
            finding(),
        ],
        FILES,
        0.5,
    )
    assert [f.severity for f in result.findings] == ["critical", "low"]
