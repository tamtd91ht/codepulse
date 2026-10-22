from typing import Any

import pytest

from app.review.prepare import REDACTED, chunk, context_for, parse_diff, prepare, redact

DIFF = r"""@@ -10,4 +10,5 @@ def login(user):
     conn = db()
-    q = "SELECT 1"
+    q = "SELECT * FROM users WHERE name = '" + user + "'"
+    rows = conn.execute(q)
     return rows
\ No newline at end of file
"""


@pytest.mark.parametrize(
    "secret",
    [
        "glpat-abcdefghijklmnopqrstuv",
        "AKIAABCDEFGHIJKLMNOP",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "xoxb-1234567890-abcdefghij",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "Bearer abcdefghijklmnopqrstuvwxyz012345",
    ],
)
def test_redacts_tokens(secret: str) -> None:
    redacted = redact(f"token: {secret} end")
    assert secret not in redacted
    assert REDACTED in redacted


def test_redacts_assignment_values_but_keeps_the_key() -> None:
    assert redact('DB_PASSWORD = "hunter2-secret"') == f'DB_PASSWORD = "{REDACTED}"'
    assert redact("password = 'x'") == "password = 'x'"  # too short to be a real secret
    assert "s3cr3t" not in redact("postgres://app:s3cr3t@db:5432/x")


def test_redacts_private_key_blocks() -> None:
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----"
    assert redact(block) == REDACTED


def test_parse_numbers_new_lines() -> None:
    file = parse_diff("app/auth.py", DIFF)
    assert (file.additions, file.deletions) == (2, 1)
    assert file.added_lines == {
        11: '    q = "SELECT * FROM users WHERE name = \'" + user + "\'"',
        12: "    rows = conn.execute(q)",
    }
    rendered = file.render()
    assert "   11 +    q = " in rendered
    assert "      -    q = " in rendered


def test_prepare_excludes_sensitive_ignored_deleted_and_large() -> None:
    entries: list[dict[str, Any]] = [
        {"new_path": "app/auth.py", "diff": DIFF},
        {"new_path": ".env.production", "diff": "@@ -0,0 +1 @@\n+KEY=1\n"},
        {"new_path": "yarn.lock", "diff": "@@ -0,0 +1 @@\n+x\n"},
        {"new_path": "old.py", "deleted_file": True, "diff": "@@ -1 +0,0 @@\n-x\n"},
        {"new_path": "big.sql", "too_large": True, "diff": ""},
        {"new_path": "logo.png", "diff": ""},
    ]
    prepared = prepare(entries, ignore_paths=["*.lock"], sensitive_paths=[".env*"])
    assert [f.path for f in prepared.files] == ["app/auth.py"]
    assert prepared.excluded == {
        ".env.production": "sensitive",
        "yarn.lock": "ignored",
        "old.py": "deleted",
        "big.sql": "too_large",
        "logo.png": "binary_or_empty",
    }
    assert prepared.loc == 3


def big_diff(lines: int, start: int = 1) -> str:
    body = "".join(f"+line {i}\n" for i in range(lines))
    return f"@@ -0,0 +{start},{lines} @@\n{body}"


def test_chunks_respect_the_token_budget_and_split_large_files() -> None:
    entries: list[dict[str, Any]] = [
        {"new_path": "a.py", "diff": big_diff(50)},
        {"new_path": "b.py", "diff": big_diff(50)},
        {"new_path": "huge.py", "diff": big_diff(40) + big_diff(40, start=500)},
    ]
    prepared = prepare(entries, [], [])
    chunks = chunk(prepared, max_tokens=300)  # 1200 characters
    assert all(len(c.diff) <= 1200 for c in chunks)
    rendered = "\n".join(c.diff for c in chunks)
    assert rendered.count("### File: huge.py") == 2  # split by hunk
    assert {f.path for c in chunks for f in c.files} == {"a.py", "b.py", "huge.py"}


def test_context_merges_overlapping_windows_and_is_redacted() -> None:
    source = "\n".join(f"src {n}" for n in range(1, 101)) + '\napi_key = "abcdef123456"'
    diff = "@@ -10,1 +10,1 @@\n-x\n+y\n@@ -40,1 +40,1 @@\n-x\n+y\n@@ -95,1 +95,1 @@\n-x\n+y\n"
    file = parse_diff("m.py", diff)
    context = context_for(file, file.hunks, source)
    assert context.count("...") == 0  # 10±30 and 40±30 and 95±30 all overlap
    assert "    1  src 1" in context
    assert "abcdef123456" not in context


def test_context_is_skipped_when_it_does_not_fit() -> None:
    prepared = prepare([{"new_path": "a.py", "diff": big_diff(5, start=50)}], [], [])
    source = "\n".join("x" * 200 for _ in range(200))
    (small,) = chunk(prepared, max_tokens=100, fetch_context=lambda path: source)
    assert small.context == ""
    (roomy,) = chunk(prepared, max_tokens=20000, fetch_context=lambda path: source)
    assert roomy.context.startswith("### Context: a.py")
