"""Post-processing of LLM findings (docs/specs/06 "Hậu xử lý")."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

from app.review.prepare import DiffFile
from app.review.schema import LLMFinding

MAX_EXCERPT_LINES = 40  # docs/specs/AGENTS.md: store at most 40 lines of the diff hunk
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
WHITESPACE = re.compile(r"\s+")


@dataclass
class Finding:
    severity: str
    category: str
    file_path: str
    line_start: int
    line_end: int
    title: str
    message: str
    suggestion: str
    confidence: float
    code_excerpt: str
    fingerprint: str


@dataclass
class PostprocessResult:
    findings: list[Finding]
    dropped: Counter[str] = field(default_factory=Counter)


def _excerpt(file: DiffFile, start: int, end: int) -> str:
    """Lines of the hunk(s) overlapping [start, end], as they appear in the diff."""
    out: list[str] = []
    for hunk in file.hunks:
        lo, hi = hunk.new_range
        if hi < start or lo > end:
            continue
        for op, number, text in hunk.lines:
            if number is not None and start <= number <= end:
                out.append(f"{op}{text}")
    return "\n".join(out[:MAX_EXCERPT_LINES])


def fingerprint(file_path: str, category: str, code: str, title: str) -> str:
    """Stable across line moves: file + category + normalised flagged code (else the title)."""
    normalised = WHITESPACE.sub(" ", code).strip().lower() or title.strip().lower()
    return hashlib.sha256(f"{file_path}\x00{category}\x00{normalised}".encode()).hexdigest()


def postprocess(
    raw: list[LLMFinding], files: dict[str, DiffFile], min_confidence: float
) -> PostprocessResult:
    result = PostprocessResult(findings=[])
    best: dict[str, Finding] = {}
    for f in raw:
        if f.confidence < min_confidence:
            result.dropped["low_confidence"] += 1
            continue
        file = files.get(f.file_path.lstrip("/"))
        if file is None:
            result.dropped["file_not_in_diff"] += 1
            continue
        added = file.added_lines
        flagged = [n for n in range(f.line_start, f.line_end + 1) if n in added]
        severity = f.severity
        if not flagged:
            in_hunk = any(
                lo <= f.line_end and f.line_start <= hi
                for lo, hi in (h.new_range for h in file.hunks)
            )
            if not in_hunk:
                result.dropped["line_not_in_diff"] += 1
                continue
            severity = "info"  # points at unchanged context: keep as a hint, never penalise
            result.dropped["downgraded_to_info"] += 1
        code = "\n".join(added[n] for n in flagged)
        finding = Finding(
            severity=severity,
            category=f.category,
            file_path=file.path,
            line_start=f.line_start,
            line_end=f.line_end,
            title=f.title,
            message=f.message,
            suggestion=f.suggestion,
            confidence=round(f.confidence, 2),
            code_excerpt=_excerpt(file, f.line_start, f.line_end),
            fingerprint=fingerprint(file.path, f.category, code, f.title),
        )
        kept = best.get(finding.fingerprint)
        if kept is None or (SEVERITY_RANK[finding.severity], finding.confidence) > (
            SEVERITY_RANK[kept.severity],
            kept.confidence,
        ):
            if kept is not None:
                result.dropped["duplicate"] += 1
            best[finding.fingerprint] = finding
        else:
            result.dropped["duplicate"] += 1
    result.findings = sorted(
        best.values(), key=lambda x: (-SEVERITY_RANK[x.severity], x.file_path, x.line_start)
    )
    return result
