"""Baselines (docs/specs/30 §15.13, 30a G9): a suite frozen at a release.

A baseline keeps every case it took (obsolete ones are left out) as it was then: the full content
a test shows (`content.case_content`, shared steps expanded) plus the parameter table, custom
values, tags, folder path and position. It is never edited, only made or deleted; a run made from
it shows that content and never the live case (`runs.is_frozen`).

Comparing two baselines (or a baseline with the suite as it is now) matches cases by their key
C<id> (the case id, which a deleted case still has in the baseline) and lists the cases added,
removed and changed (field by field), and how many stayed the same. The lists come a page at a
time (`offset` / `limit`, with their totals) and a changed value longer than VALUE_SHOWN
characters is cut, so a comparison of two large baselines stays a small response.
"""

import itertools
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.models import TestBaseline, TestBaselineCase, TestCase, TestSection, TestSuite
from app.testcases import content
from app.testcases import params as tp

CASES_MAX = 20_000  # cases one baseline may hold
CHUNK = 2_000  # rows per INSERT statement (one batch of executemany)
COMPARE_LIMIT = 200  # cases per list of a comparison page, by default
COMPARE_LIMIT_MAX = 500
VALUE_SHOWN = 2_000  # characters of a changed value a comparison shows
# Fields compared between two snapshots of a case, in the order they are listed.
COMPARED = (
    "title",
    "section",
    "template",
    "preconditions",
    "steps",
    "expected",
    "steps_list",
    "bdd",
    "charter",
    "test_data",
    "notes",
    "priority",
    "type",
    "estimate_s",
    "refs",
    "automation",
    "tags",
    "params",
    "custom",
)
SCOPE = "scope_section_ids"  # in each case's snapshot: the folders the baseline was made from


def snapshot(case: TestCase, paths: dict[int, str], shared: dict[int, Any]) -> dict[str, Any]:
    """What a baseline keeps of a case (also what "current" is compared with)."""
    snap = content.case_content(case, paths, shared)
    snap.setdefault("bdd", case.bdd)
    snap.setdefault("charter", case.charter)
    # The whole parameter table, once per case (a test's content keeps its columns only).
    snap["params"] = dict(case.params or {})
    snap.setdefault("custom", dict(case.custom or {}))
    snap["tags"] = list(case.tags or [])
    snap["kind"] = case.kind
    snap["status"] = case.status
    snap["code"] = case.code
    snap["section_id"] = case.section_id
    snap["position"] = case.position
    snap["milestone_id"] = case.milestone_id
    return snap


def _ordered(session: Session, suite_id: int, cases: list[TestCase]) -> list[TestCase]:
    """Folders in tree order (the root of the suite first), then the case order."""
    rank = {
        s.id: i
        for i, (s, _d) in enumerate(content.tree_order(content.section_rows(session, [suite_id])))
    }
    return sorted(
        cases,
        key=lambda c: (
            rank.get(c.section_id, len(rank)) if c.section_id else -1,
            c.position,
            c.id,
        ),
    )


def live_cases(session: Session, suite: TestSuite, section_ids: list[int] | None) -> list[TestCase]:
    """The suite's cases that are not obsolete, limited to these folders and the ones below
    them when given (422 for a folder not in the suite), in reading order."""
    query = select(TestCase).where(TestCase.suite_id == suite.id, TestCase.status != "obsolete")
    if section_ids:
        rows = content.section_rows(session, [suite.id])
        known = {s.id for s in rows}
        if not set(section_ids) <= known:
            raise HTTPException(422, "thư mục không thuộc bộ testcase này")
        wanted: set[int] = set()
        for root in section_ids:
            wanted |= content.descendants(rows, root)
        query = query.where(TestCase.section_id.in_(sorted(wanted)))
    cases = list(session.scalars(query.limit(CASES_MAX + 1)))
    if len(cases) > CASES_MAX:
        raise HTTPException(422, f"một baseline có tối đa {CASES_MAX} testcase")
    return _ordered(session, suite.id, cases)


def snapshots(session: Session, suite_id: int, cases: list[TestCase]) -> list[dict[str, Any]]:
    """Each case's snapshot, in two queries whatever the number (folder paths, shared steps)."""
    paths = content.section_paths(session, [suite_id])
    shared = content.shared_map(
        session, itertools.chain.from_iterable(content.shared_ids(c.steps_list) for c in cases)
    )
    return [snapshot(c, paths, shared) for c in cases]


def create(
    session: Session,
    suite: TestSuite,
    *,
    name: str,
    description: str | None,
    milestone_id: int | None,
    section_ids: list[int] | None,
    user_id: int | None,
) -> TestBaseline:
    cases = live_cases(session, suite, section_ids)
    if not cases:
        raise HTTPException(
            422, "không có testcase nào để chốt baseline (testcase lỗi thời không vào)"
        )
    snaps = snapshots(session, suite.id, cases)
    scope = sorted(set(section_ids or []))
    baseline = TestBaseline(
        business_project_id=suite.business_project_id,
        suite_id=suite.id,
        suite_name=suite.name,
        name=name,
        description=description,
        milestone_id=milestone_id,
        case_count=len(cases),
        created_by=user_id,
    )
    session.add(baseline)
    session.flush()
    rows = [
        {
            "baseline_id": baseline.id,
            "case_id": c.id,
            "key": content.key("case", c.id),
            "title": c.title,
            "section": snap["section"],
            "section_id": c.section_id,
            "priority": c.priority,
            "type": c.type,
            "snapshot": {**snap, SCOPE: scope} if scope else snap,
            "position": i,
        }
        for i, (c, snap) in enumerate(zip(cases, snaps, strict=True), start=1)
    ]
    for start in range(0, len(rows), CHUNK):
        session.execute(insert(TestBaselineCase), rows[start : start + CHUNK])
    return baseline


def cases_of(session: Session, baseline_id: int) -> list[TestBaselineCase]:
    return list(
        session.scalars(
            select(TestBaselineCase)
            .where(TestBaselineCase.baseline_id == baseline_id)
            .order_by(TestBaselineCase.position, TestBaselineCase.id)
        )
    )


# -- compare ---------------------------------------------------------------------------------------


@dataclass
class Side:
    """One side of a comparison: key -> snapshot, in reading order."""

    items: dict[str, dict[str, Any]]


def side_of_baseline(session: Session, baseline: TestBaseline) -> Side:
    rows = session.execute(
        select(TestBaselineCase.key, TestBaselineCase.snapshot)
        .where(TestBaselineCase.baseline_id == baseline.id)
        .order_by(TestBaselineCase.position, TestBaselineCase.id)
    ).all()
    return Side({str(k): dict(snap or {}) for k, snap in rows})


def side_of_current(session: Session, baseline: TestBaseline) -> Side:
    """The baseline's suite as it is now, over the same folders as the baseline (folders deleted
    since are left out)."""
    if baseline.suite_id is None:
        return Side({})
    suite = session.get(TestSuite, baseline.suite_id)
    if suite is None:
        return Side({})
    first = session.scalar(
        select(TestBaselineCase.snapshot)
        .where(TestBaselineCase.baseline_id == baseline.id)
        .order_by(TestBaselineCase.position)
        .limit(1)
    )
    scope = [int(i) for i in (first or {}).get(SCOPE) or []]
    if scope:
        known = set(session.scalars(select(TestSection.id).where(TestSection.suite_id == suite.id)))
        scope = [i for i in scope if i in known]
        if not scope:
            return Side({})
    cases = live_cases(session, suite, scope or None)
    snaps = snapshots(session, suite.id, cases)
    return Side({content.key("case", c.id) or "": s for c, s in zip(cases, snaps, strict=True)})


def _brief(key: str, snap: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": content.case_id_of(key),
        "key": key,
        "title": snap.get("title"),
        "section": snap.get("section"),
    }


def _shown(value: Any) -> Any:
    """A changed value as the comparison shows it: cut to VALUE_SHOWN characters (with "...");
    a list or an object that long comes as its JSON text, cut."""
    if isinstance(value, str):
        return value if len(value) <= VALUE_SHOWN else value[:VALUE_SHOWN] + "..."
    if isinstance(value, (list, dict)):
        raw = json.dumps(value, ensure_ascii=False, default=str)
        return value if len(raw) <= VALUE_SHOWN else raw[:VALUE_SHOWN] + "..."
    return value


def compare(a: Side, b: Side, *, offset: int = 0, limit: int = COMPARE_LIMIT) -> dict[str, Any]:
    """a = before, b = after. `added`, `removed` and `changed` are each the page
    [offset, offset + limit) of their list, `total_*` their full length; `same` a count."""
    limit = max(1, min(limit, COMPARE_LIMIT_MAX))
    offset = max(0, offset)
    end = offset + limit
    added_keys = [k for k in b.items if k not in a.items]
    removed_keys = [k for k in a.items if k not in b.items]
    changed: list[dict[str, Any]] = []
    total_changed = same = 0
    for k, before in a.items.items():
        after = b.items.get(k)
        if after is None:
            continue
        differ = [f for f in COMPARED if _norm(f, before.get(f)) != _norm(f, after.get(f))]
        if not differ:
            same += 1
            continue
        if offset <= total_changed < end:
            fields = [
                {"field": f, "before": _shown(before.get(f)), "after": _shown(after.get(f))}
                for f in differ
            ]
            changed.append({**_brief(k, after), "fields": fields})
        total_changed += 1
    return {
        "added": [_brief(k, b.items[k]) for k in added_keys[offset:end]],
        "removed": [_brief(k, a.items[k]) for k in removed_keys[offset:end]],
        "changed": changed,
        "same": same,
        "total_added": len(added_keys),
        "total_removed": len(removed_keys),
        "total_changed": total_changed,
        "offset": offset,
        "limit": limit,
    }


def _norm(field: str, value: Any) -> Any:
    """Empty values compare equal ("" / None / [] / {}); a parameter table without rows is none."""
    if field == "params":
        if not (isinstance(value, dict) and value.get("columns")):
            return None
        try:
            return tp.clean(value)
        except tp.ParamError:
            return value
    if value in (None, "", [], {}):
        return None
    return value
