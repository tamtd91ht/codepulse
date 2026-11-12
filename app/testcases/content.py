"""What the TestRail model shares (docs/specs/30 §13): keys, labels, section paths, shared steps
expanded, a case's content as a test shows it, estimates, who may record results.

Used by the cases side (app/testcases/service.py, the routers of cases) and the runs side
(app/testcases/runs.py); keep it free of either.
"""

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.models import TestCase, TestSection, TestSharedStep
from app.models.testcases import CASE_TYPES

# Keys shown for each object (§13.1): nobody types a code.
PREFIX = {
    "case": "C",
    "run": "R",
    "test": "T",
    "plan": "P",
    "milestone": "M",
    "shared": "SS",
    "section": "S",
}

TYPE_LABEL = {
    "acceptance": "Acceptance",
    "accessibility": "Accessibility",
    "automated": "Automated",
    "compatibility": "Compatibility",
    "destructive": "Destructive",
    "functional": "Functional",
    "other": "Other",
    "performance": "Performance",
    "regression": "Regression",
    "security": "Security",
    "smoke": "Smoke & Sanity",
    "usability": "Usability",
}
if set(TYPE_LABEL) != set(CASE_TYPES):  # a type added in one place only
    raise RuntimeError("TYPE_LABEL must name every CASE_TYPES entry")
TEMPLATE_LABEL = {"text": "Test Case (Text)", "steps": "Test Case (Steps)"}
TEST_STATUS_LABEL = {
    "untested": "Chưa chạy",
    "passed": "Passed",
    "failed": "Failed",
    "blocked": "Blocked",
    "retest": "Retest",
}
STEP_MAX = 100  # steps of one case
STEP_TEXT_MAX = 5_000
SECTION_DEPTH_MAX = 10
PATH_SEP = " > "


def key(kind: str, ident: int | None) -> str | None:
    return f"{PREFIX[kind]}{ident}" if ident is not None else None


def case_id_of(text: str) -> int | None:
    """123 for "C123" / "c123" (a key typed or read from a sheet's ID column), else None."""
    m = re.fullmatch(r"\s*[Cc](\d{1,10})\s*", text or "")
    return int(m.group(1)) if m else None


# -- estimates -------------------------------------------------------------------------------------

_UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "giây": 1,
    "giay": 1,
    "m": 60,
    "min": 60,
    "ph": 60,
    "phút": 60,
    "phut": 60,
    "h": 3600,
    "hr": 3600,
    "giờ": 3600,
    "gio": 3600,
}
# Longest alternatives first so "min" is not read as "m" + "in".
_UNITS = "|".join(sorted(_UNIT_SECONDS, key=len, reverse=True))
_SPAN = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({_UNITS})?")
_SPANS = re.compile(rf"(?:\d+(?:[.,]\d+)?\s*(?:{_UNITS})?\s*)+")


def parse_estimate(text: Any) -> int | None:
    """Seconds of "30s", "5m", "1h 30m", "1:30" (h:mm); a bare number is minutes. None: empty or
    unreadable."""
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    if m := re.fullmatch(r"(\d{1,3}):(\d{2})", raw):
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60
    if not _SPANS.fullmatch(raw):
        return None
    total = 0.0
    for number, unit in _SPAN.findall(raw):
        # A number without a unit counts as minutes.
        total += float(number.replace(",", ".")) * _UNIT_SECONDS.get(unit, 60)
    if total <= 0:
        return None
    return min(round(total), 7 * 24 * 3600)


def format_estimate(seconds: int | None) -> str | None:
    """1h 30m, 5m, 45s (TestRail's way)."""
    if not seconds:
        return None
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    parts = [f"{h}h" if h else "", f"{m}m" if m else "", f"{s}s" if s else ""]
    return " ".join(p for p in parts if p)


# -- sections --------------------------------------------------------------------------------------


def section_rows(session: Session, suite_ids: Iterable[int]) -> list[TestSection]:
    ids = sorted(set(suite_ids))
    if not ids:
        return []
    return list(
        session.scalars(
            select(TestSection)
            .where(TestSection.suite_id.in_(ids))
            .order_by(TestSection.suite_id, TestSection.position, TestSection.id)
        )
    )


def paths_of(sections: list[TestSection]) -> dict[int, str]:
    """section id -> "Parent > Child" (a broken parent link stops the path there)."""
    by_id = {s.id: s for s in sections}
    out: dict[int, str] = {}

    def path(section_id: int, depth: int = 0) -> str:
        if section_id in out:
            return out[section_id]
        node = by_id[section_id]
        parent = node.parent_id
        if parent is None or parent not in by_id or depth > SECTION_DEPTH_MAX:
            out[section_id] = node.name
        else:
            out[section_id] = f"{path(parent, depth + 1)}{PATH_SEP}{node.name}"
        return out[section_id]

    for s in sections:
        path(s.id)
    return out


def section_paths(session: Session, suite_ids: Iterable[int]) -> dict[int, str]:
    return paths_of(section_rows(session, suite_ids))


def descendants(sections: list[TestSection], root: int) -> set[int]:
    """`root` and every section below it."""
    children: dict[int | None, list[int]] = {}
    for s in sections:
        children.setdefault(s.parent_id, []).append(s.id)
    out, todo = set(), [root]
    while todo:
        node = todo.pop()
        if node in out:
            continue
        out.add(node)
        todo.extend(children.get(node, []))
    return out


def tree_order(sections: list[TestSection]) -> list[tuple[TestSection, int]]:
    """(section, depth) in the order a tree is read: each parent, then its children."""
    children: dict[int | None, list[TestSection]] = {}
    ids = {s.id for s in sections}
    for s in sections:
        parent = s.parent_id if s.parent_id in ids else None
        children.setdefault(parent, []).append(s)
    for items in children.values():
        items.sort(key=lambda s: (s.position, s.id))
    out: list[tuple[TestSection, int]] = []
    seen: set[int] = set()

    def walk(parent: int | None, depth: int) -> None:
        for s in children.get(parent, []):
            if s.id in seen or depth > SECTION_DEPTH_MAX:
                continue
            seen.add(s.id)
            out.append((s, depth))
            walk(s.id, depth + 1)

    walk(None, 0)
    return out


def section_for_path(
    session: Session,
    suite_id: int,
    path: str,
    cache: dict[tuple[int | None, str], int] | None = None,
) -> int | None:
    """The section of `path` ("A > B") in the suite, made where missing (imports, the AI)."""
    parts = [p.strip()[:200] for p in re.split(r"\s*>\s*", path or "") if p.strip()]
    if not parts:
        return None
    if cache is None:
        cache = {}
    if not cache:
        for s in session.scalars(select(TestSection).where(TestSection.suite_id == suite_id)):
            cache.setdefault((s.parent_id, s.name.casefold()), s.id)
    parent: int | None = None
    for part in parts[:SECTION_DEPTH_MAX]:
        found = cache.get((parent, part.casefold()))
        if found is None:
            top = sum(1 for (p, _) in cache if p == parent)
            node = TestSection(suite_id=suite_id, parent_id=parent, name=part, position=top + 1)
            session.add(node)
            session.flush()
            found = node.id
            cache[(parent, part.casefold())] = found
        parent = found
    return parent


# -- steps -----------------------------------------------------------------------------------------


def shared_map(session: Session, ids: Iterable[int]) -> dict[int, TestSharedStep]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    return {
        s.id: s
        for s in session.scalars(select(TestSharedStep).where(TestSharedStep.id.in_(wanted)))
    }


def shared_ids(steps: list[dict[str, Any]] | None) -> list[int]:
    return [
        int(s["shared"])
        for s in steps or []
        if isinstance(s, dict) and isinstance(s.get("shared"), int)
    ]


def expand_steps(
    steps: list[dict[str, Any]] | None, shared: dict[int, TestSharedStep]
) -> list[dict[str, Any]]:
    """The steps as a tester reads them: shared steps replaced by their own steps, each marked
    with where it came from ({"content", "expected", "shared_id"?, "shared_title"?})."""
    out: list[dict[str, Any]] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("shared"), int):
            block = shared.get(step["shared"])
            if block is None:
                out.append(
                    {
                        "content": "(bước dùng chung đã bị xoá)",
                        "expected": "",
                        "shared_id": step["shared"],
                    }
                )
                continue
            for inner in block.steps or []:
                out.append(
                    {
                        "content": str(inner.get("content") or ""),
                        "expected": str(inner.get("expected") or ""),
                        "shared_id": block.id,
                        "shared_title": block.title,
                    }
                )
        else:
            out.append(
                {
                    "content": str(step.get("content") or ""),
                    "expected": str(step.get("expected") or ""),
                }
            )
    return out


def split_numbered(text: str | None) -> list[str]:
    """The items of "1. a\\n2. b" (or "- a\\n- b"); [] when the text is not such a list."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    items: list[str] = []
    for line in lines:
        m = re.match(
            r"^(?:(?:bước|buoc|step)\s*)?(\d{1,3})[.):\-]\s*(.+)$", line, re.I
        ) or re.match(r"^[-*•]\s+(.+)$", line)
        if m:
            items.append(m.groups()[-1].strip())
        elif items:
            items[-1] = f"{items[-1]}\n{line}"
        else:
            return []
    return items if len(items) >= 2 else []


def steps_from_text(steps: str | None, expected: str | None) -> list[dict[str, str]]:
    """Template "steps" from numbered texts: the n-th expected item goes with the n-th step when
    both lists have the same length; otherwise the whole expected text goes with the last step."""
    items = split_numbered(steps)
    if not items:
        return []
    results = split_numbered(expected)
    if len(results) == len(items):
        return [{"content": a, "expected": b} for a, b in zip(items, results, strict=True)]
    out = [{"content": a, "expected": ""} for a in items]
    if (expected or "").strip():
        out[-1]["expected"] = (expected or "").strip()
    return out


def case_content(
    case: TestCase, paths: dict[int, str], shared: dict[int, TestSharedStep]
) -> dict[str, Any]:
    """The case as a test shows it (and a closed run keeps)."""
    return {
        "case_key": key("case", case.id),
        "title": case.title,
        "section": paths.get(case.section_id) if case.section_id else None,
        "template": case.template,
        "preconditions": case.preconditions,
        "steps": case.steps,
        "expected": case.expected,
        "steps_list": expand_steps(case.steps_list, shared) if case.template == "steps" else [],
        "test_data": case.test_data,
        "notes": case.notes,
        "priority": case.priority,
        "type": case.type,
        "estimate_s": case.estimate_s,
        "refs": list(case.jira_keys or []),
        "automation": case.automation,
    }


# -- results ---------------------------------------------------------------------------------------


def may_record(principal: Principal, bp_id: int, assignees: Iterable[int | None]) -> bool:
    """Recording a result: whoever holds `tests.run` over the dự án, or the person the test (or
    its run) is assigned to (§2, §13.6)."""
    from app.testcases import service as tc

    if tc.may_run(principal, bp_id):
        return True
    return tc.may_view(principal, bp_id) and principal.user_id in {a for a in assignees if a}
