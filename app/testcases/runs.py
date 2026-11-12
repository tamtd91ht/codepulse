"""Test runs, plans and results, the TestRail way (docs/specs/30 §13.5-13.6, API §14.2-14.4).

A run is a list of tests (one per case) chosen by a Selection: a whole suite, folders with their
children, filters, ticked cases, or the cases merged MRs call for (§8.3). While the run is open
a test shows its case as it is now; closing the run keeps a snapshot (`content.case_content`),
which is also what a test shows once its case is deleted. A case with a parameter table gives
one test per row (§15.15), showing the content with that row's values. A run made from a
baseline (§15.13) takes the baseline's cases and shows their content as the baseline kept it,
even while open (`is_frozen`). A test's status is its latest result
that has a status; a result without one is a comment or a reassignment. A plan groups runs: each
entry gives one run per combination of the configurations picked (one per group, a cartesian
product).

Permissions are decided by the router (`docs.view` / `docs.edit` / `content.may_record`).
"""

import io
import itertools
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy import case as sql_case
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models import (
    TestBaseline,
    TestBaselineCase,
    TestCase,
    TestConfig,
    TestConfigGroup,
    TestField,
    TestResult,
    TestRun,
    TestRunTest,
    TestSection,
    TestSuite,
)
from app.models.testcases import CASE_TYPES, PRIORITIES, TEST_STATUSES
from app.testcases import content
from app.testcases import fields as tf
from app.testcases import impact as imp
from app.testcases import params as tp
from app.testcases.service import FieldError, suite_in

RUN_TESTS_MAX = 5_000
PLAN_RUNS_MAX = 50
PLAN_TESTS_MAX = 20_000  # tests one plan request (create or add entries) may make
DEFECTS_MAX = 20
TEXT_MAX = 10_000
LINE_MAX = 200
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,7})\b")
EMPTY_SELECTION = "chưa chọn testcase nào (chọn bộ, thư mục, testcase hoặc danh sách Cần test lại)"
NOTHING_FOUND = "không có testcase nào khớp lựa chọn (testcase lỗi thời không được đưa vào run)"


# -- small cleaners --------------------------------------------------------------------------------


def line(value: Any, limit: int = LINE_MAX) -> str:
    return " ".join(CONTROL.sub("", str(value or "")).split())[:limit]


def text(value: Any, limit: int = TEXT_MAX) -> str | None:
    out = CONTROL.sub("", str(value or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    return out[:limit] or None


def jira_keys(values: Iterable[str] | str | None) -> list[str]:
    """`ABC-123` keys (defects, refs), upper-cased, once each."""
    raw = values if isinstance(values, str) else " ".join(values or [])
    out: list[str] = []
    for found in JIRA_KEY.findall(raw.upper()):
        if found not in out:
            out.append(found)
    return out[:DEFECTS_MAX]


def empty_counts() -> dict[str, int]:
    return {**{s: 0 for s in TEST_STATUSES}, "total": 0}


# -- selection -------------------------------------------------------------------------------------


@dataclass
class Selection:
    suite_id: int | None = None
    section_ids: list[int] = field(default_factory=list)
    case_ids: list[int] = field(default_factory=list)
    all: bool = False
    priority: list[str] = field(default_factory=list)
    type: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    regression_days: int | None = None
    regression_branch: str | None = None

    def meta(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "suite_id": self.suite_id,
            "section_ids": self.section_ids[:500],
            "case_count": len(self.case_ids) or None,
            "all": self.all or None,
            "priority": self.priority or None,
            "type": self.type or None,
            "tags": self.tags or None,
        }
        if self.regression_days:
            out["regression"] = {
                "days": self.regression_days,
                "target_branch": self.regression_branch,
            }
        return {k: v for k, v in out.items() if v is not None}


@dataclass
class Chosen:
    cases: list[TestCase]
    meta: dict[str, Any]
    source: str
    # §15.13 G9: the baseline's cases picked (then `cases` is empty).
    frozen: list[TestBaselineCase] | None = None

    @property
    def size(self) -> int:
        """Tests the choice makes: one per parameter row of a case (§15.15), else one."""
        if self.frozen is not None:
            return sum(max(1, len(tp.rows((b.snapshot or {}).get("params")))) for b in self.frozen)
        return sum(max(1, len(tp.rows(c.params))) for c in self.cases)


def default_name(sel: Selection, today: date) -> str:
    if sel.regression_days:
        return f"Regression {sel.regression_days} ngày ({today:%d/%m})"
    return f"Test run {today:%d/%m/%Y}"


def _order(session: Session, cases: list[TestCase]) -> list[TestCase]:
    """Suite by suite, folders in tree order (the root of a suite first), then the case order."""
    suite_ids = {c.suite_id for c in cases}
    if not suite_ids:
        return cases
    suite_pos = {
        sid: (pos, sid)
        for sid, pos in session.execute(
            select(TestSuite.id, TestSuite.position).where(TestSuite.id.in_(suite_ids))
        )
    }
    rank = {
        s.id: i
        for i, (s, _depth) in enumerate(
            content.tree_order(content.section_rows(session, suite_ids))
        )
    }
    return sorted(
        cases,
        key=lambda c: (
            suite_pos.get(c.suite_id, (0, c.suite_id)),
            rank.get(c.section_id, len(rank)) if c.section_id else -1,
            c.position,
            c.id,
        ),
    )


def resolve(session: Session, bp_id: int, sel: Selection, now: datetime) -> Chosen:
    """The cases (not obsolete, in suites visible in the dự án) a selection picks, in reading
    order. Ticked cases, folders (with their children) and the regression list add up; without
    any of them the whole suite (or, with `all`, every live suite of the dự án) is taken. The
    filters then narrow it."""
    picks = bool(sel.section_ids or sel.case_ids or sel.regression_days)
    if not (picks or sel.all or sel.suite_id):
        raise HTTPException(422, EMPTY_SELECTION)
    _check_filters(sel)
    query = (
        select(TestCase)
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(suite_in([bp_id]), TestCase.status != "obsolete")
    )
    meta = sel.meta()
    source = "manual"
    if sel.suite_id is not None:
        found = session.scalar(
            select(TestSuite.id).where(TestSuite.id == sel.suite_id, suite_in([bp_id]))
        )
        if found is None:
            raise HTTPException(404, "bộ testcase không tồn tại")
        query = query.where(TestCase.suite_id == sel.suite_id)
    elif not sel.case_ids and not sel.section_ids:
        query = query.where(TestSuite.archived_at.is_(None))
    if picks:
        conds: list[ColumnElement[bool]] = []
        if sel.section_ids:
            conds.append(TestCase.section_id.in_(_sections(session, bp_id, sel.section_ids)))
        if sel.case_ids:
            conds.append(TestCase.id.in_(sorted(set(sel.case_ids))))
        if sel.regression_days:
            source = "regression"
            conds.append(TestCase.id.in_(_regression_ids(session, bp_id, sel, now, meta)))
        query = query.where(or_(*conds))
    if sel.priority:
        query = query.where(TestCase.priority.in_(sel.priority))
    if sel.type:
        query = query.where(TestCase.type.in_(sel.type))
    if sel.tags:
        query = query.where(or_(*(TestCase.tags.contains([t]) for t in sel.tags)))
    cases = list(session.scalars(query.limit(RUN_TESTS_MAX + 1)))
    if not cases:
        raise HTTPException(422, NOTHING_FOUND)
    if len(cases) > RUN_TESTS_MAX:
        raise HTTPException(422, f"một test run có tối đa {RUN_TESTS_MAX} test")
    return Chosen(_order(session, cases), meta, source)


def _check_filters(sel: Selection) -> None:
    bad = [p for p in sel.priority if p not in PRIORITIES] + [
        t for t in sel.type if t not in CASE_TYPES
    ]
    if bad:
        raise HTTPException(422, f"giá trị lọc không hợp lệ: {', '.join(bad[:5])}")


def _regression_ids(
    session: Session, bp_id: int, sel: Selection, now: datetime, meta: dict[str, Any]
) -> list[int]:
    """The cases the merged MRs of the period call for (§8.3); their MRs go into `meta`."""
    since = now - timedelta(days=sel.regression_days or 0)
    mrs = imp.merged_mrs(session, bp_id, since, now, sel.regression_branch or None)
    reasons = imp.regression(imp.for_mrs(session, mrs, [bp_id]))
    meta["mrs"] = [{"id": m.id, "iid": m.iid, "title": m.title} for m in mrs[:200]]
    return sorted(reasons) or [0]


def baseline_for_project(session: Session, bp_id: int, baseline_id: int) -> TestBaseline:
    """The baseline, when it belongs to the dự án or its suite is one the dự án sees."""
    baseline = session.get(TestBaseline, baseline_id)
    if baseline is not None and baseline.business_project_id != bp_id:
        shared = baseline.suite_id is not None and session.scalar(
            select(TestSuite.id).where(TestSuite.id == baseline.suite_id, suite_in([bp_id]))
        )
        if not shared:
            baseline = None
    if baseline is None:
        raise HTTPException(404, "baseline không tồn tại")
    return baseline


def resolve_baseline(
    session: Session, bp_id: int, baseline: TestBaseline, sel: Selection, now: datetime
) -> Chosen:
    """§15.13: the baseline's cases a selection picks, as `resolve` picks a suite's (folders
    with their children, ticked cases, the regression list, then the filters); nothing picked:
    the whole baseline. Obsolete cases never entered the baseline."""
    _check_filters(sel)
    if sel.suite_id is not None and sel.suite_id != baseline.suite_id:
        raise HTTPException(422, "bộ testcase không khớp với baseline")
    query = select(TestBaselineCase).where(TestBaselineCase.baseline_id == baseline.id)
    meta = sel.meta()
    meta["baseline_id"] = baseline.id
    meta["baseline_name"] = baseline.name
    meta["suite_id"] = baseline.suite_id
    if sel.section_ids or sel.case_ids or sel.regression_days:
        conds: list[ColumnElement[bool]] = []
        if sel.section_ids:
            ids = _baseline_sections(session, baseline, sel.section_ids)
            conds.append(TestBaselineCase.section_id.in_(ids))
        if sel.case_ids:
            conds.append(TestBaselineCase.case_id.in_(sorted(set(sel.case_ids))))
        if sel.regression_days:
            conds.append(
                TestBaselineCase.case_id.in_(_regression_ids(session, bp_id, sel, now, meta))
            )
        query = query.where(or_(*conds))
    if sel.priority:
        query = query.where(TestBaselineCase.priority.in_(sel.priority))
    if sel.type:
        query = query.where(TestBaselineCase.type.in_(sel.type))
    if sel.tags:
        tags = TestBaselineCase.snapshot["tags"]
        query = query.where(or_(*(tags.contains([t]) for t in sel.tags)))
    items = list(
        session.scalars(
            query.order_by(TestBaselineCase.position, TestBaselineCase.id).limit(RUN_TESTS_MAX + 1)
        )
    )
    if not items:
        raise HTTPException(422, "không có testcase nào của baseline khớp lựa chọn")
    if len(items) > RUN_TESTS_MAX:
        raise HTTPException(422, f"một test run có tối đa {RUN_TESTS_MAX} test")
    return Chosen([], meta, "baseline", frozen=items)


def _baseline_sections(session: Session, baseline: TestBaseline, ids: list[int]) -> list[int]:
    """The folders asked for and the ones below them now; a folder deleted since the baseline
    still picks the baseline's cases that were in it."""
    rows = content.section_rows(session, [baseline.suite_id] if baseline.suite_id else [])
    known = {s.id for s in rows} | set(
        session.scalars(
            select(TestBaselineCase.section_id)
            .where(
                TestBaselineCase.baseline_id == baseline.id,
                TestBaselineCase.section_id.is_not(None),
            )
            .distinct()
        )
    )
    if not set(ids) <= known:
        raise HTTPException(422, "thư mục không tồn tại")
    out: set[int] = set()
    for root in ids:
        out |= content.descendants(rows, root)
    return sorted(out)


def _sections(session: Session, bp_id: int, ids: list[int]) -> list[int]:
    """The folders asked for and every folder below them (each in a suite of the dự án)."""

    wanted = set(ids)
    suites = set(
        session.scalars(
            select(TestSection.suite_id)
            .join(TestSuite, TestSuite.id == TestSection.suite_id)
            .where(TestSection.id.in_(wanted), suite_in([bp_id]))
        )
    )
    rows = content.section_rows(session, suites)
    known = {s.id for s in rows}
    if not wanted <= known:
        raise HTTPException(422, "thư mục không tồn tại")
    out: set[int] = set()
    for root in wanted:
        out |= content.descendants(rows, root)
    return sorted(out)


# -- tests -----------------------------------------------------------------------------------------


def run_size(session: Session, run_id: int) -> int:
    return int(session.scalar(select(func.count()).where(TestRunTest.run_id == run_id)) or 0)


def live_contents(session: Session, cases: Iterable[TestCase]) -> dict[int, dict[str, Any]]:
    """case id -> the case as a test shows it now (`content.case_content`). Two queries whatever
    the number of cases: their folder paths and the shared steps they use."""
    items = list(cases)
    if not items:
        return {}
    paths = content.section_paths(session, {c.suite_id for c in items})
    shared = content.shared_map(
        session, itertools.chain.from_iterable(content.shared_ids(c.steps_list) for c in items)
    )
    return {c.id: content.case_content(c, paths, shared) for c in items}


def is_frozen(run: TestRun) -> bool:
    """§15.13: a run made from a baseline shows the baseline's content, never the live case,
    even while open (and after the baseline is deleted)."""
    return run.source == "baseline" or run.baseline_id is not None


def row_view(
    base: dict[str, Any], values: dict[str, Any] | None, table: dict[str, Any] | None = None
) -> dict[str, Any]:
    """§15.15: what the test of one parameter row shows: the content with the row's values,
    the title followed by "[col=value, ...]" (in the order of the columns of `table`, the case's
    parameter table: a stored `params` comes back from JSONB in its own key order). No values:
    the content as is."""
    if not values:
        return base
    columns = [c for c in (table or {}).get("columns") or [] if c in values]
    order = columns + [k for k in values if k not in columns]
    shown = {str(k): str(values[k]) for k in order}
    out = tp.render(base, shown)
    out["title"] = f"{out.get('title') or ''} {tp.label(shown)}".strip()
    return out


@dataclass
class _Source:
    """A case to add: live (`case`) or as a baseline keeps it (`frozen`)."""

    case_id: int | None
    key: str
    table: dict[str, Any]
    case: TestCase | None = None
    frozen: dict[str, Any] | None = None


def _existing(session: Session, run_id: int) -> set[tuple[str, int]]:
    """(case key, parameter row or 0) of the run's tests (a deleted case keeps its key in the
    snapshot)."""
    out: set[tuple[str, int]] = set()
    for case_id, row, snap_key in session.execute(
        select(
            TestRunTest.case_id,
            TestRunTest.param_row,
            TestRunTest.snapshot["case_key"].astext,
        ).where(TestRunTest.run_id == run_id)
    ):
        k = content.key("case", case_id) or snap_key
        if k:
            out.add((k, int(row or 0)))
    return out


def add_tests(
    session: Session,
    run: TestRun,
    cases: list[TestCase],
    shown: dict[int, dict[str, Any]] | None = None,
    *,
    frozen: list[TestBaselineCase] | None = None,
) -> int:
    """Add the cases not in the run yet (assigned to the run's assignee); returns how many tests
    were added. A case with a parameter table gives one test per row (§15.15, `param_row` from
    1, `params` its values), each row added once. `frozen` (§15.13): a baseline's cases instead
    of `cases`, showing the baseline's content. `shown` (from `live_contents`) spares computing
    the same content again for every run of a plan entry."""
    sources: list[_Source]
    if frozen is not None:
        sources = [
            _Source(
                b.case_id,
                b.key,
                dict((b.snapshot or {}).get("params") or {}),
                frozen=dict(b.snapshot or {}),
            )
            for b in frozen
        ]
    else:
        sources = [
            _Source(c.id, content.key("case", c.id) or "", dict(c.params or {}), case=c)
            for c in cases
        ]
    existing = _existing(session, run.id)
    room = RUN_TESTS_MAX - run_size(session, run.id)
    planned: list[tuple[_Source, int | None, dict[str, str]]] = []
    for src in sources:
        table = src.table if src.table.get("columns") else {}
        count = len(table.get("rows") or [])
        if not count:
            numbers: list[int | None] = [None] if (src.key, 0) not in existing else []
        elif (src.key, 0) in existing:
            # The run already has this case as one test without a row (added before the case
            # got its parameter table): it stays that one test, no row is added beside it
            # (the case would show twice). Removing that test and adding the case again gives
            # one test per row.
            numbers = []
        else:
            numbers = [n for n in range(1, count + 1) if (src.key, n) not in existing]
        # Counted before any row is built: a run never holds more than RUN_TESTS_MAX tests.
        room -= len(numbers)
        if room < 0:
            raise HTTPException(422, f"một test run có tối đa {RUN_TESTS_MAX} test")
        if not numbers:
            continue
        rows = tp.rows(table) if count else []
        for row in numbers:
            existing.add((src.key, int(row or 0)))
            planned.append((src, row, rows[row - 1] if row else {}))
    if not planned:
        return 0
    live = {src.case.id: src.case for src, _r, _v in planned if src.case is not None}
    if live and (shown is None or any(i not in shown for i in live)):
        shown = live_contents(session, live.values())
    top = int(
        session.scalar(select(func.max(TestRunTest.position)).where(TestRunTest.run_id == run.id))
        or 0
    )
    for i, (src, row, values) in enumerate(planned, start=1):
        if src.frozen is not None:
            base = src.frozen
        elif src.case is not None and shown is not None:
            base = shown[src.case.id]
        else:  # a live source always has its case, and `shown` was filled above
            continue
        snap = dict(row_view(base, values))
        snap.setdefault("case_key", src.key)
        # §15.15: the test keeps the table's columns, not the table (a baseline's content has
        # the whole table; the test's own values are in `params`).
        snap["params"] = content.param_columns(snap.get("params"))
        session.add(
            TestRunTest(
                run_id=run.id,
                case_id=src.case_id,
                title=str(snap.get("title") or src.key),
                section=snap.get("section"),
                priority=str(snap.get("priority") or "medium"),
                type=str(snap.get("type") or "functional"),
                snapshot=snap,
                param_row=row,
                params=dict(values),
                status="untested",
                assigned_to=run.assigned_to,
                position=top + i,
            )
        )
    session.flush()
    return len(planned)


def tests_with_results(session: Session, run: TestRun, test_ids: list[int]) -> int:
    """How many of these tests of the run already have a result."""
    if not test_ids:
        return 0
    return int(
        session.scalar(
            select(func.count(func.distinct(TestResult.test_id)))
            .join(TestRunTest, TestRunTest.id == TestResult.test_id)
            .where(TestRunTest.run_id == run.id, TestRunTest.id.in_(sorted(set(test_ids))))
        )
        or 0
    )


def remove_tests(session: Session, run: TestRun, test_ids: list[int]) -> list[int]:
    """Delete these tests of the run (their results go with them); returns the ids deleted."""
    if not test_ids:
        return []
    gone = session.scalars(
        delete(TestRunTest)
        .where(TestRunTest.run_id == run.id, TestRunTest.id.in_(sorted(set(test_ids))))
        .returning(TestRunTest.id)
    )
    return sorted(int(i) for i in gone)


def _views(session: Session, tests: list[TestRunTest], live: bool) -> dict[int, dict[str, Any]]:
    """test id -> what it shows: the case as it is now (open run, case still there; with the
    test's parameter values, §15.15), else the snapshot."""
    out: dict[int, dict[str, Any]] = {}
    cases: dict[int, TestCase] = {}
    if live:
        ids = {t.case_id for t in tests if t.case_id is not None}
        if ids:
            cases = {c.id: c for c in session.scalars(select(TestCase).where(TestCase.id.in_(ids)))}
    shown = live_contents(session, cases.values())
    for t in tests:
        if t.case_id is not None and t.case_id in shown:
            out[t.id] = row_view(shown[t.case_id], t.params, cases[t.case_id].params)
        else:
            snap = dict(t.snapshot or {})
            snap.setdefault("case_key", content.key("case", t.case_id))
            snap.setdefault("title", t.title)
            snap.setdefault("section", t.section)
            snap.setdefault("priority", t.priority)
            snap.setdefault("type", t.type)
            snap.setdefault("template", "text")
            snap.setdefault("steps_list", [])
            snap.setdefault("estimate_s", None)
            out[t.id] = snap
    return out


def views(session: Session, run: TestRun, tests: list[TestRunTest]) -> dict[int, dict[str, Any]]:
    return _views(session, tests, live=run.completed_at is None and not is_frozen(run))


def close_run(session: Session, run: TestRun, now: datetime) -> None:
    """Keep every test's content as it is now; the run takes no more changes or results."""
    tests = list(session.scalars(select(TestRunTest).where(TestRunTest.run_id == run.id)))
    shown = _views(session, tests, live=not is_frozen(run))
    for t in tests:
        snap = shown[t.id]
        t.snapshot = snap
        t.title = str(snap.get("title") or t.title)
        t.section = snap.get("section")
        t.priority = str(snap.get("priority") or t.priority)
        t.type = str(snap.get("type") or t.type)
    run.completed_at = now


def refresh_open_snapshots(session: Session, case_ids: list[int]) -> None:
    """Before these cases are deleted: every test of an open run pointing at one of them keeps
    the case as it is now (snapshot, title, folder, priority, type), which is what it shows once
    the case is gone. Closed runs keep the snapshot taken when they closed, runs made from a
    baseline the baseline's content. A constant number of queries whatever the number of cases
    or tests."""
    wanted = sorted(set(case_ids))
    if not wanted:
        return
    session.flush()
    rows = session.execute(
        select(
            TestRunTest.id,
            TestRunTest.case_id,
            TestRunTest.title,
            TestRunTest.priority,
            TestRunTest.type,
            TestRunTest.params,
        )
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(
            TestRunTest.case_id.in_(wanted),
            TestRun.completed_at.is_(None),
            TestRun.source != "baseline",
            TestRun.baseline_id.is_(None),
        )
    ).all()
    if not rows:
        return
    cases = {
        c.id: c
        for c in session.scalars(select(TestCase).where(TestCase.id.in_({r.case_id for r in rows})))
    }
    shown = live_contents(session, cases.values())
    values = [
        {
            "id": r.id,
            "snapshot": snap,
            "title": str(snap.get("title") or r.title),
            "section": snap.get("section"),
            "priority": str(snap.get("priority") or r.priority),
            "type": str(snap.get("type") or r.type),
        }
        for r in rows
        if (base := shown.get(r.case_id)) is not None
        for snap in [row_view(base, r.params, cases[r.case_id].params)]
    ]
    if not values:
        return
    session.execute(update(TestRunTest), values)
    # Tests already loaded in this session would otherwise keep their old values.
    changed = {v["id"] for v in values}
    for obj in list(session.identity_map.values()):
        if isinstance(obj, TestRunTest) and obj.id in changed:
            session.expire(obj)


# -- counts ----------------------------------------------------------------------------------------


def counts_by(
    session: Session, key: InstrumentedAttribute[Any], ids: Iterable[int]
) -> tuple[dict[int, dict[str, int]], dict[int, int]]:
    """Per value of `key` (a TestRun column: id, plan_id, milestone_id): the tests by status,
    and the number of runs. Two grouped queries whatever the number of ids."""
    wanted = sorted(set(ids))
    counts = {i: empty_counts() for i in wanted}
    runs = dict.fromkeys(wanted, 0)
    if not wanted:
        return counts, runs
    rows = session.execute(
        select(key, TestRunTest.status, func.count())
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(key.in_(wanted))
        .group_by(key, TestRunTest.status)
    )
    for ident, status, n in rows:
        c = counts[ident]
        c[status] = c.get(status, 0) + int(n)
        c["total"] += int(n)
    for ident, n in session.execute(
        select(key, func.count(TestRun.id)).where(key.in_(wanted)).group_by(key)
    ):
        runs[ident] = int(n)
    return counts, runs


def run_counts(session: Session, run_ids: Iterable[int]) -> dict[int, dict[str, int]]:
    return counts_by(session, TestRun.id, run_ids)[0]


# -- results ---------------------------------------------------------------------------------------


def result_extras(
    session: Session, test_ids: list[int]
) -> tuple[dict[int, list[str]], dict[int, str]]:
    """test id -> every defect its results named (oldest first), and its latest comment."""
    defects: dict[int, list[str]] = {}
    comments: dict[int, str] = {}
    if not test_ids:
        return defects, comments
    for test_id, items in session.execute(
        select(TestResult.test_id, TestResult.defects)
        .where(TestResult.test_id.in_(test_ids), func.jsonb_array_length(TestResult.defects) > 0)
        .order_by(TestResult.created_at, TestResult.id)
    ):
        seen = defects.setdefault(test_id, [])
        seen.extend(d for d in items or [] if d not in seen)
    for test_id, comment in session.execute(
        select(TestResult.test_id, TestResult.comment)
        .where(
            TestResult.test_id.in_(test_ids),
            TestResult.comment.is_not(None),
            TestResult.comment != "",
        )
        .ext(distinct_on(TestResult.test_id))
        .order_by(TestResult.test_id, TestResult.created_at.desc(), TestResult.id.desc())
    ):
        comments[test_id] = comment or ""
    return defects, comments


def step_results(raw: list[dict[str, Any]], steps: int) -> list[dict[str, Any]]:
    if raw and steps == 0:
        raise HTTPException(422, "testcase không ở dạng bước: không ghi kết quả từng bước được")
    if len(raw) > steps:
        raise HTTPException(422, f"testcase có {steps} bước, nhận {len(raw)} kết quả bước")
    return [{"status": r.get("status"), "actual": text(r.get("actual")) or ""} for r in raw]


def add_result(
    session: Session,
    test: TestRunTest,
    *,
    user_id: int | None,
    now: datetime,
    status: str | None = None,
    comment: str | None = None,
    version: str | None = None,
    elapsed_s: int | None = None,
    defects: list[str] | None = None,
    steps: list[dict[str, Any]] | None = None,
    reassign: bool = False,
    assigned_to: int | None = None,
    failed_step: int | None = None,
    attachments: list[dict[str, Any]] | None = None,
    custom: dict[str, Any] | None = None,
) -> TestResult:
    """`custom`: values of the dự án's result fields, already checked (fields.clean_custom)."""
    if reassign:
        test.assigned_to = assigned_to
    if status is not None:
        test.status = status
        test.tested_at = now
    result = TestResult(
        test_id=test.id,
        status=status,
        comment=text(comment),
        version=line(version) or None,
        elapsed_s=elapsed_s,
        defects=defects or [],
        step_results=steps or [],
        assigned_to=test.assigned_to,
        failed_step=failed_step,
        attachments=attachments or [],
        custom=dict(custom or {}),
        created_by=user_id,
        created_at=now,
    )
    session.add(result)
    _session_into_result(test, result, now)
    return result


def _session_into_result(test: TestRunTest, result: TestResult, now: datetime) -> None:
    """§15.12 G8, exploratory: a result with a status takes the session's notes (those not taken
    by an earlier result) under its comment, and the session's time as `elapsed_s` when none was
    given. The session is paused, and remembers how many notes were taken."""
    state = dict(test.session or {})
    if result.status is None or not state.get("started_at"):
        return
    from app.testcases import exploratory  # imports content, which this module imports

    taken = int(state.get("taken") or 0)
    notes = list(state.get("notes") or [])
    summary, seconds = exploratory.session_summary({**state, "notes": notes[taken:]}, now)
    if summary and (notes[taken:] or result.elapsed_s is None):
        result.comment = _with_summary(result.comment, summary)
    if result.elapsed_s is None and seconds is not None:
        result.elapsed_s = seconds
    if state.get("running"):
        state["elapsed_s"] = exploratory.elapsed(state, now)
        state["running"] = False
    state["taken"] = len(notes)
    test.session = state


def _with_summary(comment: str | None, summary: str) -> str | None:
    """The tester's comment followed by the session summary, within TEXT_MAX like any comment:
    the comment is kept whole, the summary is cut (ending "...") to what is left."""
    head = f"{comment}\n\n" if comment else ""
    room = TEXT_MAX - len(head)
    if room <= 3:
        return text(comment)
    if len(summary) > room:
        summary = summary[: room - 3].rstrip() + "..."
    return text(head + summary)


def result_custom(
    session: Session,
    bp_id: int,
    test: TestRunTest,
    values: dict[str, Any] | None,
    *,
    status: str | None,
    template: str | None,
    fields: Sequence[TestField] | None = None,
    suite_of: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    """§15.8: the custom values of a result recorded for `test`: the run's dự án's result fields
    that apply to the case's suite and template; required ones only when a status is recorded
    (defaults filled then). 422 naming the field otherwise. `suite_of` (case id -> suite id,
    see `suites_of`): read from it rather than one query per test."""
    if test.case_id is None:
        suite_id = None
    elif suite_of is not None:
        suite_id = suite_of.get(test.case_id)
    else:
        suite_id = session.scalar(select(TestCase.suite_id).where(TestCase.id == test.case_id))
    try:
        return tf.clean_custom(
            session,
            bp_id,
            "result",
            values,
            suite_id=suite_id,
            template=template,
            enforce_required=status is not None,
            defaults=status is not None,
            fields=fields,
        )
    except FieldError as exc:
        raise HTTPException(422, str(exc)) from None


def suites_of(session: Session, tests: Iterable[TestRunTest]) -> dict[int, int]:
    """case id -> suite id of the cases of `tests`, in one query."""
    case_ids = {t.case_id for t in tests if t.case_id is not None}
    if not case_ids:
        return {}
    return {
        cid: sid
        for cid, sid in session.execute(
            select(TestCase.id, TestCase.suite_id).where(TestCase.id.in_(case_ids))
        )
    }


def latest_facts(session: Session, test_ids: list[int]) -> dict[int, tuple[int | None, int | None]]:
    """test id -> (elapsed_s, failed_step) of its latest result with a status (§15 G6)."""
    if not test_ids:
        return {}
    return {
        test_id: (elapsed, failed)
        for test_id, elapsed, failed in session.execute(
            select(TestResult.test_id, TestResult.elapsed_s, TestResult.failed_step)
            .where(TestResult.test_id.in_(test_ids), TestResult.status.is_not(None))
            .ext(distinct_on(TestResult.test_id))
            .order_by(TestResult.test_id, TestResult.created_at.desc(), TestResult.id.desc())
        )
    }


@dataclass
class RunTime:
    elapsed_total_s: int = 0
    remaining_estimate_s: int = 0


def run_times(
    session: Session, runs: list[TestRun], not_done: dict[int, set[str]]
) -> dict[int, RunTime]:
    """Per run (§15 G6): the time its results took (sum of `elapsed_s`), and what is left: the
    estimates of the tests not done yet (`not_done`: per dự án, the statuses that still need a
    run), plus, for such a test without an estimate, the average elapsed of the run's results.
    An open run reads the estimate from the case as it is now, a closed one (or one made from a
    baseline) from its snapshot.
    Two grouped queries whatever the number of runs."""
    out = {r.id: RunTime() for r in runs}
    if not runs:
        return out
    ids = sorted(out)
    spent: dict[int, tuple[int, int]] = {}
    for run_id, total, n in session.execute(
        select(TestRunTest.run_id, func.sum(TestResult.elapsed_s), func.count(TestResult.elapsed_s))
        .join(TestRunTest, TestRunTest.id == TestResult.test_id)
        .where(TestRunTest.run_id.in_(ids), TestResult.elapsed_s.is_not(None))
        .group_by(TestRunTest.run_id)
    ):
        spent[run_id] = (int(total or 0), int(n or 0))
        out[run_id].elapsed_total_s = int(total or 0)
    snap_est = TestRunTest.snapshot["estimate_s"].as_integer()
    estimate = sql_case(
        (
            TestRun.completed_at.is_(None)
            & (TestRun.source != "baseline")
            & TestRun.baseline_id.is_(None)
            & TestCase.id.is_not(None),
            TestCase.estimate_s,
        ),
        else_=snap_est,
    )
    rows = session.execute(
        select(
            TestRunTest.run_id,
            TestRunTest.status,
            estimate.is_(None),
            func.count(),
            func.sum(estimate),
        )
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .outerjoin(TestCase, TestCase.id == TestRunTest.case_id)
        .where(TestRunTest.run_id.in_(ids))
        .group_by(TestRunTest.run_id, TestRunTest.status, estimate.is_(None))
    )
    project = {r.id: r.business_project_id for r in runs}
    for run_id, status, no_estimate, n, total in rows:
        if status not in not_done.get(project[run_id], {"untested"}):
            continue
        if no_estimate:
            done_s, done_n = spent.get(run_id, (0, 0))
            out[run_id].remaining_estimate_s += (done_s // done_n) * int(n) if done_n else 0
        else:
            out[run_id].remaining_estimate_s += int(total or 0)
    return out


# -- plans -----------------------------------------------------------------------------------------


def combinations(
    session: Session, bp_id: int, config_ids: list[int]
) -> list[tuple[list[int], str | None]]:
    """(config ids, "Chrome, Android") per combination: one config of each group picked, the
    groups in their order. Nothing picked: one run without configuration."""
    wanted = sorted(set(config_ids))
    if not wanted:
        return [([], None)]
    rows = session.execute(
        select(TestConfig, TestConfigGroup)
        .join(TestConfigGroup, TestConfigGroup.id == TestConfig.group_id)
        .where(TestConfig.id.in_(wanted), TestConfigGroup.business_project_id == bp_id)
        .order_by(TestConfigGroup.position, TestConfigGroup.id, TestConfig.position, TestConfig.id)
    ).all()
    if len(rows) != len(wanted):
        raise HTTPException(422, "cấu hình không tồn tại")
    groups: dict[int, list[TestConfig]] = {}
    for config, group in rows:
        groups.setdefault(group.id, []).append(config)
    return [
        ([c.id for c in combo], ", ".join(c.name for c in combo))
        for combo in itertools.product(*groups.values())
    ]


# -- export ----------------------------------------------------------------------------------------

EXPORT_HEADERS = (
    "Test",
    "Testcase",
    "Thư mục",
    "Tiêu đề",
    "Ưu tiên",
    "Loại",
    "Trạng thái",
    "Người được giao",
    "Ngày test",
    "Người test",
    "Thời gian chạy",
    "Phiên bản",
    "Bình luận",
    "Defects",
)


def write_xlsx(run_name: str, header: list[tuple[str, str]], rows: list[list[str]]) -> bytes:
    """A run as a sheet: a few lines about the run, then one row per test. Every cell is text:
    nothing runs as a formula."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import TYPE_STRING
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    book.remove(book.active)  # type: ignore[arg-type]
    ws = book.create_sheet("Test run")

    def put(r: int, c: int, value: str) -> None:
        cell = ws.cell(r, c)
        cell.value = CONTROL.sub("", value)
        cell.data_type = TYPE_STRING
        cell.alignment = Alignment(vertical="top", wrap_text=True)

    put(1, 1, run_name)
    ws.cell(1, 1).font = Font(bold=True, size=14)
    r = 2
    for label, value in header:
        put(r, 1, label)
        put(r, 2, value)
        ws.cell(r, 1).font = Font(bold=True)
        r += 1
    r += 1
    head = r
    for c, label in enumerate(EXPORT_HEADERS, start=1):
        put(r, c, label)
        cell = ws.cell(r, c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3A5F")
    for row in rows:
        r += 1
        for c, value in enumerate(row, start=1):
            put(r, c, value)
    for i, width in enumerate((10, 10, 28, 48, 12, 14, 12, 20, 18, 20, 12, 14, 40, 20), start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = f"A{head + 1}"
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()
