"""Per-file diff statistics. Diff text itself is never stored (docs/specs/AGENTS.md)."""

from fnmatch import fnmatchcase
from typing import Any


def count_lines(diff: str) -> tuple[int, int]:
    """(additions, deletions) of a unified diff hunk body as returned by GitLab."""
    additions = deletions = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return additions, deletions


def file_stat(entry: dict[str, Any]) -> dict[str, Any]:
    """A GitLab diff entry without its `diff` text, plus line counts."""
    additions, deletions = count_lines(entry.get("diff") or "")
    return {
        "old_path": entry.get("old_path"),
        "new_path": entry.get("new_path"),
        "new_file": bool(entry.get("new_file")),
        "renamed_file": bool(entry.get("renamed_file")),
        "deleted_file": bool(entry.get("deleted_file")),
        "too_large": bool(entry.get("too_large")),
        "additions": additions,
        "deletions": deletions,
    }


def is_ignored(path: str, patterns: list[str]) -> bool:
    """IGNORE_PATHS glob match; a leading `**/` also matches files at the repository root."""
    return any(fnmatchcase(path, p) or fnmatchcase("/" + path, p) for p in patterns)
