"""Turn GitLab diff entries into prompt-ready chunks (docs/specs/06 chunking, 11 secret masking).

Nothing here is stored: diffs and context only travel to the LLM, findings keep at most a short
excerpt (docs/specs/AGENTS.md).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.collector.gitlab.diffstat import is_ignored

REDACTED = "***REDACTED***"
CONTEXT_LINES = 30
CHARS_PER_TOKEN = 4  # rough estimate; good enough to keep chunks under the limit

SECRET_PATTERNS = [
    re.compile(p)
    for p in (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)",
        r"\bglpat-[A-Za-z0-9_\-]{20,}",
        r"\bgh[pousr]_[A-Za-z0-9]{36,}",
        r"\bsk-ant-[A-Za-z0-9_\-]{20,}",
        r"\bsk-[A-Za-z0-9]{32,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bxox[abpors]-[A-Za-z0-9-]{10,}",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}",
        r"(?i)://[^/\s:@]+:[^/\s@]+@",  # credentials inside URLs
    )
]
# key = "value" style assignments; only the value is replaced so the finding still makes sense.
ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b[\w.-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key)[\w.-]*
     \s*[:=]\s*)
    (["'])([^"'\s]{6,})\2
    """
)

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

ContextFetcher = Callable[[str], str | None]  # path at the reviewed sha -> file text


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return ASSIGNMENT.sub(lambda m: f"{m[1]}{m[2]}{REDACTED}{m[2]}", text)


@dataclass
class Hunk:
    header: str
    new_start: int
    lines: list[tuple[str, int | None, str]] = field(default_factory=list)  # (op, new_no, text)

    @property
    def new_range(self) -> tuple[int, int]:
        numbers = [n for _, n, _ in self.lines if n is not None]
        return (min(numbers), max(numbers)) if numbers else (self.new_start, self.new_start)


@dataclass
class DiffFile:
    path: str
    hunks: list[Hunk]
    additions: int
    deletions: int
    new_file: bool = False

    @property
    def added_lines(self) -> dict[int, str]:
        return {n: text for h in self.hunks for op, n, text in h.lines if op == "+" and n}

    def render(self, hunks: list[Hunk] | None = None) -> str:
        """Diff with new-file line numbers, so the model can cite lines it actually sees."""
        out = [f"### File: {self.path}"]
        for hunk in hunks if hunks is not None else self.hunks:
            out.append(hunk.header)
            for op, number, text in hunk.lines:
                label = f"{number:>5}" if number is not None else "     "
                out.append(f"{label} {op}{text}")
        return "\n".join(out)


def parse_diff(path: str, diff: str, new_file: bool = False) -> DiffFile:
    hunks: list[Hunk] = []
    additions = deletions = 0
    new_no = 0
    for line in diff.splitlines():
        if m := HUNK_HEADER.match(line):
            new_no = int(m[3])
            hunks.append(Hunk(header=line, new_start=new_no))
            continue
        if not hunks or line.startswith("\\"):
            continue
        op, text = (line[0], line[1:]) if line else (" ", "")
        if op == "+":
            hunks[-1].lines.append(("+", new_no, text))
            new_no += 1
            additions += 1
        elif op == "-":
            hunks[-1].lines.append(("-", None, text))
            deletions += 1
        else:
            hunks[-1].lines.append((" ", new_no, text))
            new_no += 1
    return DiffFile(path, hunks, additions, deletions, new_file)


@dataclass
class PreparedDiff:
    files: list[DiffFile]
    excluded: dict[str, str]  # path -> reason, for the review summary and logs

    @property
    def loc(self) -> int:
        return sum(f.additions + f.deletions for f in self.files)


def prepare(
    entries: list[dict[str, Any]], ignore_paths: list[str], sensitive_paths: list[str]
) -> PreparedDiff:
    files: list[DiffFile] = []
    excluded: dict[str, str] = {}
    for entry in entries:
        path = entry.get("new_path") or entry.get("old_path") or ""
        if is_ignored(path, sensitive_paths):
            excluded[path] = "sensitive"  # never sent to the LLM (docs/specs/11)
        elif is_ignored(path, ignore_paths):
            excluded[path] = "ignored"
        elif entry.get("deleted_file"):
            excluded[path] = "deleted"
        elif entry.get("too_large") or entry.get("collapsed"):
            excluded[path] = "too_large"
        elif not entry.get("diff"):
            excluded[path] = "binary_or_empty"
        else:
            files.append(parse_diff(path, redact(entry["diff"]), bool(entry.get("new_file"))))
    return PreparedDiff(files, excluded)


def context_for(file: DiffFile, hunks: list[Hunk], source: str | None) -> str:
    """±30 lines around each hunk from the file at the reviewed sha, merged when they overlap."""
    if not source or file.new_file:
        return ""
    lines = redact(source).splitlines()
    ranges: list[list[int]] = []
    for hunk in hunks:
        start, end = hunk.new_range
        lo, hi = max(1, start - CONTEXT_LINES), min(len(lines), end + CONTEXT_LINES)
        if ranges and lo <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], hi)
        else:
            ranges.append([lo, hi])
    blocks = ["\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(lo, hi + 1)) for lo, hi in ranges]
    return f"### Context: {file.path}\n" + "\n...\n".join(blocks)


@dataclass
class Chunk:
    diff: str
    context: str
    files: list[DiffFile]


def chunk(
    prepared: PreparedDiff, max_tokens: int, fetch_context: ContextFetcher | None = None
) -> list[Chunk]:
    """Group files into chunks under max_tokens; a file too big on its own is split by hunk.

    Context is added only while the chunk still has room: the diff always wins.
    """
    budget = max_tokens * CHARS_PER_TOKEN
    pieces: list[tuple[DiffFile, list[Hunk], str]] = []
    for file in prepared.files:
        whole = file.render()
        if len(whole) <= budget:
            pieces.append((file, file.hunks, whole))
            continue
        group: list[Hunk] = []
        for hunk in file.hunks:
            if group and len(file.render([*group, hunk])) > budget:
                pieces.append((file, group, file.render(group)))
                group = []
            group.append(hunk)
        if group:
            pieces.append((file, group, file.render(group)))

    chunks: list[Chunk] = []
    current: list[tuple[DiffFile, list[Hunk], str]] = []
    size = 0
    for piece in pieces:
        if current and size + len(piece[2]) > budget:
            chunks.append(_finish(current, budget, fetch_context))
            current, size = [], 0
        current.append(piece)
        size += len(piece[2])
    if current:
        chunks.append(_finish(current, budget, fetch_context))
    return chunks


def _finish(
    pieces: list[tuple[DiffFile, list[Hunk], str]],
    budget: int,
    fetch_context: ContextFetcher | None,
) -> Chunk:
    diff = "\n\n".join(text for _, _, text in pieces)
    contexts: list[str] = []
    room = budget - len(diff)
    for file, hunks, _ in pieces:
        if fetch_context is None or room <= 0:
            break
        block = context_for(file, hunks, fetch_context(file.path))
        if block and len(block) <= room:
            contexts.append(block)
            room -= len(block)
    files = list({id(f): f for f, _, _ in pieces}.values())
    return Chunk(diff=diff, context="\n\n".join(contexts), files=files)
