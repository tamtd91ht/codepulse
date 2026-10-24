"""Team rules that go into the review prompt (docs/specs/17).

Three sources, merged in this order: the dự án's rules (Admin), the repo's own rules (Admin),
and files inside the repo (e.g. `.claude/rules/critical/*.md`, read at the reviewed sha). Each
piece keeps its heading so a finding can cite it in `rule_id`. Everything is capped: rules are
context for the model, not the whole handbook.
"""

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from app.models import BusinessProject, Project

log = logging.getLogger(__name__)

MAX_RULES_CHARS = 20_000  # about 5k tokens, inside the smallest chunk budget
MAX_FILE_CHARS = 6_000
MAX_FILES = 10

FileFetcher = Callable[[str], str | None]  # path -> text at the reviewed sha, None if missing


def rule_paths(spec: str | None) -> list[str]:
    """Comma/newline separated repo paths; relative, no traversal, at most MAX_FILES."""
    if not spec:
        return []
    out: list[str] = []
    for raw in spec.replace("\n", ",").split(","):
        path = raw.strip().lstrip("/")
        if not path or ".." in path.split("/") or path in out:
            continue
        out.append(path)
        if len(out) == MAX_FILES:
            break
    return out


def _clip(text: str, limit: int, label: str) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    log.info("review rules clipped", extra={"source": label, "chars": len(text), "limit": limit})
    return text[:limit].rstrip() + "\n(… rút gọn)"


def _digest(text: str) -> str:
    """Short fingerprint of a rule text: shows whether rules changed between two reviews."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def collect_rules(
    business_project: BusinessProject | None,
    project: Project,
    fetch: FileFetcher,
    skip: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """The `conventions` block of the prompt (empty when nobody configured anything) and one
    record per source: kind, name (the heading a finding cites in rule_id), status, size, hash.

    `skip`: paths the change under review touches; a rules file among them is not used (the
    author could have edited it) and the prompt says so."""
    parts: list[str] = []
    sources: list[dict[str, Any]] = []
    spans: dict[int, tuple[int, int]] = {}  # source index -> (start, end) in the merged text
    offset = 0

    def add(part: str) -> tuple[int, int]:
        nonlocal offset
        start = offset + (2 if parts else 0)  # the blank-line separator
        parts.append(part)
        offset = start + len(part)
        return start, offset

    def used(kind: str, name: str, text: str) -> None:
        spans[len(sources)] = add(f"## {name}\n{text}")
        sources.append(
            {
                "kind": kind,
                "name": name,
                "status": "used",
                "chars": len(text),
                "hash": _digest(text),
            }
        )

    if business_project and business_project.review_rules:
        used(
            "business_project",
            f"Quy tắc dự án {business_project.name}",
            _clip(business_project.review_rules, MAX_RULES_CHARS // 2, "business_project"),
        )
    if project.review_rules:
        used(
            "project",
            f"Quy tắc repo {project.path_with_namespace}",
            _clip(project.review_rules, MAX_RULES_CHARS // 2, "project"),
        )
    for path in rule_paths(project.review_rules_paths):
        if skip and path in skip:
            add(f"## {path}\n(file này bị thay đổi trong chính diff đang review nên không dùng)")
            sources.append({"kind": "file", "name": path, "status": "changed_in_diff"})
            continue
        try:
            text = fetch(path)
        except Exception:  # GitLab hiccup: the review still runs, without this file
            log.warning("review rules file not fetched", extra={"path": path})
            text = None
        if text:
            used("file", path, _clip(text, MAX_FILE_CHARS, path))
        else:
            sources.append({"kind": "file", "name": path, "status": "missing"})
    merged = "\n\n".join(parts)
    if len(merged) > MAX_RULES_CHARS:
        merged = merged[:MAX_RULES_CHARS]
        for index, (begin, finish) in spans.items():
            if begin >= MAX_RULES_CHARS:
                sources[index]["status"] = "cut"  # not in the prompt at all
            elif finish > MAX_RULES_CHARS:
                sources[index]["status"] = "partial"  # only its beginning reached the model
    return merged, sources


def build_rules(
    business_project: BusinessProject | None,
    project: Project,
    fetch: FileFetcher,
    skip: set[str] | None = None,
) -> str:
    """The `conventions` block only (see collect_rules)."""
    return collect_rules(business_project, project, fetch, skip)[0]
