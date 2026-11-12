"""Test runs, plans and results, the TestRail way (docs/specs/30 §13.5-13.6, API §14.2-14.4).

A run is a list of tests (one per case) chosen by a Selection: a whole suite, folders with their
children, filters, ticked cases, or the cases merged MRs call for (§8.3). While the run is open
a test shows its case as it is now; closing the run keeps a snapshot (`content.case_content`),
which is also what a test shows once its case is deleted. A test's status is its latest result
that has a status; a result without one is a comment or a reassignment. A plan groups runs: each
entry gives one run per combination of the configurations picked (one per group, a cartesian
product).

Permissions are decided by the router (`docs.view` / `docs.edit` / `content.may_record`).
"""

import io
import itertools
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy import case as sql_case
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models import (
    TestCase,
    TestConfig,
    TestConfigGroup,
    TestResult,
    TestRun,
    TestRunTest,
    TestSection,
    TestSuite,
)
from app.models.testcases import CASE_TYPES, PRIORITIES, TEST_STATUSES
from app.testcases import content
from app.testcases import impact as imp
from app.testcases.service import suite_in

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
    bad = [p for p in sel.priority if p not in PRIORITIES] + [
        t for t in sel.type if t not in CASE_TYPES
    ]
    if bad:
        raise HTTPException(422, f"giá trị lọc không hợp lệ: {', '.join(bad[:5])}")
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
            until = now
            since = until - timedelta(days=sel.regression_days)
            mrs = imp.merged_mrs(session, bp_id, since, until, sel.regression_branch or None)
            reasons = imp.regression(imp.for_mrs(session, mrs, [bp_id]))
            meta["mrs"] = [{"id": m.id, "iid": m.iid, "title": m.title} for m in mrs[:200]]
            conds.append(TestCase.id.in_(sorted(reasons) or [0]))
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


def add_tests(
    session: Session,
    run: TestRun,
    cases: list[TestCase],
    shown: dict[int, dict[str, Any]] | None = None,
) -> int:
    """Add the cases not in the run yet (assigned to the run's assignee); returns how many.
    `shown` (from `live_contents`) spares computing the same content again for every run of a
    plan entry."""
    existing = set(session.scalars(select(TestRunTest.case_id).where(TestRunTest.run_id == run.id)))
    new = [c for c in cases if c.id not in existing]
    if not new:
        return 0
    if run_size(session, run.id) + len(new) > RUN_TESTS_MAX:
        raise HTTPException(422, f"một test run có tối đa {RUN_TESTS_MAX} test")
    if shown is None or any(c.id not in shown for c in new):
        shown = live_contents(session, new)
    top = int(
        session.scalar(select(func.max(TestRunTest.position)).where(TestRunTest.run_id == run.id))
        or 0
    )
    for i, case in enumerate(new, start=1):
        snap = dict(shown[case.id])
        session.add(
            TestRunTest(
                run_id=run.id,
                case_id=case.id,
                title=case.title,
                section=snap["section"],
                priority=case.priority,
                type=case.type,
                snapshot=snap,
                status="untested",
                assigned_to=run.assigned_to,
                position=top + i,
            )
        )
    session.flush()
    return len(new)


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
    """test id -> what it shows: the case as it is now (open run, case still there), else the
    snapshot."""
    out: dict[int, dict[str, Any]] = {}
    cases: dict[int, TestCase] = {}
    if live:
        ids = {t.case_id for t in tests if t.case_id is not None}
        if ids:
            cases = {c.id: c for c in session.scalars(select(TestCase).where(TestCase.id.in_(ids)))}
    shown = live_contents(session, cases.values())
    for t in tests:
        if t.case_id is not None and t.case_id in shown:
            out[t.id] = shown[t.case_id]
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
    return _views(session, tests, live=run.completed_at is None)


def close_run(session: Session, run: TestRun, now: datetime) -> None:
    """Keep every test's content as it is now; the run takes no more changes or results."""
    tests = list(session.scalars(select(TestRunTest).where(TestRunTest.run_id == run.id)))
    shown = _views(session, tests, live=True)
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
    the case is gone. Closed runs keep the snapshot taken when they closed. A constant number of
    queries whatever the number of cases or tests."""
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
        )
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(TestRunTest.case_id.in_(wanted), TestRun.completed_at.is_(None))
    ).all()
    if not rows:
        return
    cases = session.scalars(select(TestCase).where(TestCase.id.in_({r.case_id for r in rows})))
    shown = live_contents(session, cases)
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
        if (snap := shown.get(r.case_id)) is not None
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
) -> TestResult:
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
        created_by=user_id,
        created_at=now,
    )
    session.add(result)
    return result


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
    An open run reads the estimate from the case as it is now, a closed one from its snapshot.
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
            TestRun.completed_at.is_(None) & TestCase.id.is_not(None),
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
