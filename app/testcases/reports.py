"""Reports and dashboards of the Testcase module (docs/specs/30 §15, G4).

Every report is computed live from the rows (no cache, no stored aggregate) with a few grouped
queries, whatever the number of results. Filters (`Filters`): the dự án (required), a suite (the
case's suite), a milestone (runs of the milestone, or of a plan of the milestone), a plan, a
configuration (runs whose `config_ids` holds it), a CI branch (results whose `ci.branch` is it)
and a period (`date_from` .. `date_to`, days in REPORT_TZ, both included).

Two populations, kept apart on purpose:

- *tests*: the TestRunTest rows of the matching runs and their current status. With a period,
  only runs active in it (created before its end, not closed before its start); with a branch,
  only tests having a CI result on that branch.
- *results*: the TestResult rows (status recorded, not a comment only) recorded in the period.

Statuses are counted generically (status -> n): a dự án may add its own (TestStatus); "done"
means not a status that still needs running (Untested, Retest, a custom not-done one), the rule
of the runs' remaining estimate (`statuses.not_done`). Productivity per person is a coaching
signal: the router shows each person's row to whoever has `tests.manage` on the dự án or to
the person themself, the others see the totals only.
"""

import itertools
import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Date,
    Select,
    and_,
    case,
    cast,
    exists,
    func,
    literal_column,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, aliased

from app.jira.scopes import jira_issue_in
from app.models import (
    BusinessProject,
    Issue,
    NotificationWebhook,
    TestCase,
    TestCaseRevision,
    TestConfig,
    TestConfigGroup,
    TestMilestone,
    TestPlan,
    TestReportSchedule,
    TestResult,
    TestRun,
    TestRunTest,
    TestStatus,
    TestSuite,
    User,
    WebhookDelivery,
)
from app.models.testcases import TEST_STATUSES
from app.testcases import content
from app.testcases import service as tc
from app.testcases import statuses as tst

log = logging.getLogger(__name__)

KINDS = ("summary", "compare", "coverage", "defects", "productivity", "automation", "readiness")
SCHEDULE_KINDS = ("summary", "coverage", "defects", "automation", "readiness")
KIND_LABEL = {
    "summary": "Tổng kết run / plan",
    "compare": "So sánh run",
    "coverage": "Độ phủ",
    "defects": "Defect",
    "productivity": "Năng suất",
    "automation": "Automation",
    "readiness": "Sẵn sàng release",
}
COMPARE_MIN, COMPARE_MAX = 2, 10
LIST_MAX = 200  # rows of a list in a report (uncovered issues, flaky cases, failing tests)
CI_RUNS_MAX = 50
WEEKS_DEFAULT = 12
PASS_RATE_GO = 95.0  # % passed a milestone needs for "Go" (overridable per call)
HIGH = ("critical", "high")
REPORT_EVENT = "testcase.report"
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
STATUS_COLOR = {
    "untested": "muted",
    "passed": "good",
    "failed": "bad",
    "blocked": "warn",
    "retest": "info",
    "skipped": "muted",
}
STATUS_NAME = {**content.TEST_STATUS_LABEL, "skipped": "Skipped"}


# -- filters ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Filters:
    bp_id: int
    suite_id: int | None = None
    milestone_id: int | None = None
    plan_id: int | None = None
    config_id: int | None = None
    branch: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    tz: str = "UTC"

    def start(self) -> datetime | None:
        if self.date_from is None:
            return None
        return datetime.combine(self.date_from, time.min, ZoneInfo(self.tz))

    def end(self) -> datetime | None:
        """Exclusive: the start of the day after `date_to`."""
        if self.date_to is None:
            return None
        return datetime.combine(self.date_to + timedelta(days=1), time.min, ZoneInfo(self.tz))

    def echo(self) -> dict[str, Any]:
        return {
            "business_project_id": self.bp_id,
            "suite_id": self.suite_id,
            "milestone_id": self.milestone_id,
            "plan_id": self.plan_id,
            "config_id": self.config_id,
            "branch": self.branch,
            "from": self.date_from.isoformat() if self.date_from else None,
            "to": self.date_to.isoformat() if self.date_to else None,
            "tz": self.tz,
        }


def milestone_runs(milestone_id: int) -> ColumnElement[bool]:
    """Runs of a milestone: their own, or their plan's."""
    return or_(
        TestRun.milestone_id == milestone_id,
        TestRun.plan_id.in_(select(TestPlan.id).where(TestPlan.milestone_id == milestone_id)),
    )


def _ci(field: str) -> Any:
    """`ci ->> field` with the key inlined, so the same expression can be grouped by."""
    return func.jsonb_extract_path_text(TestResult.ci, literal_column(f"'{field}'"))


def _branch() -> Any:
    return _ci("branch")


def _run_where(f: Filters) -> list[ColumnElement[bool]]:
    where: list[ColumnElement[bool]] = [TestRun.business_project_id == f.bp_id]
    if f.milestone_id:
        where.append(milestone_runs(f.milestone_id))
    if f.plan_id:
        where.append(TestRun.plan_id == f.plan_id)
    if f.config_id:
        where.append(TestRun.config_ids.contains([f.config_id]))
    return where


def _suite_cases(suite_id: int) -> Any:
    return select(TestCase.id).where(TestCase.suite_id == suite_id)


def test_where(f: Filters) -> list[ColumnElement[bool]]:
    """Tests (TestRunTest joined to TestRun) the filters keep."""
    where = _run_where(f)
    if f.suite_id:
        where.append(TestRunTest.case_id.in_(_suite_cases(f.suite_id)))
    if f.branch:
        where.append(exists().where(TestResult.test_id == TestRunTest.id, _branch() == f.branch))
    start, end = f.start(), f.end()
    if start is not None:
        where.append(or_(TestRun.completed_at.is_(None), TestRun.completed_at >= start))
    if end is not None:
        where.append(TestRun.created_at < end)
    return where


def result_where(f: Filters) -> list[ColumnElement[bool]]:
    """Results (TestResult joined to TestRunTest and TestRun) with a status the filters keep."""
    where = [*_run_where(f), TestResult.status.is_not(None)]
    if f.suite_id:
        where.append(TestRunTest.case_id.in_(_suite_cases(f.suite_id)))
    if f.branch:
        where.append(_branch() == f.branch)
    start, end = f.start(), f.end()
    if start is not None:
        where.append(TestResult.created_at >= start)
    if end is not None:
        where.append(TestResult.created_at < end)
    return where


def _tests(*cols: Any) -> Select[Any]:
    return select(*cols).select_from(TestRunTest).join(TestRun, TestRun.id == TestRunTest.run_id)


def _results(*cols: Any) -> Select[Any]:
    return (
        select(*cols)
        .select_from(TestResult)
        .join(TestRunTest, TestRunTest.id == TestResult.test_id)
        .join(TestRun, TestRun.id == TestRunTest.run_id)
    )


def _day(f: Filters, column: Any) -> ColumnElement[date]:
    return cast(func.timezone(f.tz, column), Date)


def _live_cases(f: Filters) -> list[ColumnElement[bool]]:
    """Live cases of the dự án (suites made there or shared with it, not archived, case not
    obsolete); joined with TestSuite."""
    where = [tc.suite_in([f.bp_id]), TestSuite.archived_at.is_(None), TestCase.status != "obsolete"]
    if f.suite_id:
        where.append(TestCase.suite_id == f.suite_id)
    return where


# -- shared helpers --------------------------------------------------------------------------------


def counts(
    rows: Iterable[tuple[Any, Any]], pending: Iterable[str] = ("untested",)
) -> dict[str, int]:
    """(status, n) rows -> {status: n, ..., "total", "done"}; "done" = not in `pending`, the
    statuses that still need running (`pending_of`: the rule of the runs' remaining estimate)."""
    out: dict[str, int] = {}
    total = 0
    for status, n in rows:
        if status is None:
            continue
        out[str(status)] = out.get(str(status), 0) + int(n)
        total += int(n)
    out["total"] = total
    out["done"] = total - sum(out.get(k, 0) for k in set(pending))
    return out


def pending_of(session: Session, bp_id: int) -> set[str]:
    """Statuses of the dự án whose tests are not done (Untested, Retest, custom not-done ones),
    as in the runs' remaining estimate (`statuses.not_done`)."""
    return tst.not_done(session, [bp_id])[bp_id]


def pct(part: int | float, whole: int | float) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


BUILTIN_DONE = {d.key: d.done for d in (*tst.BUILTIN, tst.UNTESTED)}


def status_meta(session: Session, bp_id: int) -> list[dict[str, Any]]:
    """Every status a report may show: the built-in ones, then the dự án's own."""
    out = [
        {
            "key": s,
            "name": STATUS_NAME.get(s, s),
            "color": STATUS_COLOR.get(s, "muted"),
            "done": BUILTIN_DONE.get(s, s != "untested"),
        }
        for s in TEST_STATUSES
    ]
    for row in session.scalars(
        select(TestStatus)
        .where(TestStatus.business_project_id == bp_id)
        .order_by(TestStatus.position, TestStatus.id)
    ):
        out.append({"key": row.key, "name": row.name, "color": row.color, "done": row.done})
    return out


def names(session: Session, ids: Iterable[int | None]) -> dict[int, str]:
    wanted = sorted({i for i in ids if i is not None})
    if not wanted:
        return {}
    return {
        int(uid): (full or email or f"#{uid}")
        for uid, full, email in session.execute(
            select(User.id, User.full_name, User.primary_email).where(User.id.in_(wanted))
        )
    }


def _issue_states(session: Session, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    wanted = sorted(set(keys))
    if not wanted:
        return {}
    return {
        str(k): {"title": t, "state": s, "url": u, "type": ty}
        for k, t, s, u, ty in session.execute(
            select(Issue.key, Issue.title, Issue.state, Issue.web_url, Issue.type).where(
                Issue.source == "jira", Issue.key.in_(wanted)
            )
        )
    }


def bug_state(state: str | None) -> str:
    """The synced Jira state as the reports show it: open | closed | unknown (not synced)."""
    if state == "closed":
        return "closed"
    if state is None:
        return "unknown"
    return "open"


# -- 1. run / plan summary -------------------------------------------------------------------------


def summary(session: Session, f: Filters) -> dict[str, Any]:
    pending = pending_of(session, f.bp_id)
    tests = counts(
        session.execute(
            _tests(TestRunTest.status, func.count())
            .where(*test_where(f))
            .group_by(TestRunTest.status)
        ),
        pending,
    )
    runs_n = int(
        session.scalar(_tests(func.count(func.distinct(TestRun.id))).where(*test_where(f))) or 0
    )
    results = counts(
        session.execute(
            _results(TestResult.status, func.count())
            .where(*result_where(f))
            .group_by(TestResult.status)
        ),
        pending,
    )
    day = _day(f, TestResult.created_at)
    progress: dict[date, dict[str, int]] = defaultdict(dict)
    for d, status, n in session.execute(
        _results(day, TestResult.status, func.count())
        .where(*result_where(f))
        .group_by(day, TestResult.status)
        .order_by(day)
    ):
        progress[d][str(status)] = int(n)
    people: dict[tuple[int | None, str], dict[str, Any]] = {}
    for uid, source, status, n, elapsed in session.execute(
        _results(
            TestResult.created_by,
            TestResult.source,
            TestResult.status,
            func.count(),
            func.coalesce(func.sum(TestResult.elapsed_s), 0),
        )
        .where(*result_where(f))
        .group_by(TestResult.created_by, TestResult.source, TestResult.status)
    ):
        row = people.setdefault(
            (uid, str(source)),
            {"user_id": uid, "source": str(source), "counts": {}, "total": 0, "elapsed_s": 0},
        )
        row["counts"][str(status)] = row["counts"].get(str(status), 0) + int(n)
        row["total"] += int(n)
        row["elapsed_s"] += int(elapsed or 0)
    who = names(session, (k[0] for k in people))
    fallback = {"ci": "CI"}
    by_person = sorted(
        (
            {
                **r,
                "name": who.get(r["user_id"], f"#{r['user_id']}")
                if r["user_id"]
                else fallback.get(r["source"], "(không rõ)"),
            }
            for r in people.values()
        ),
        key=lambda r: (-int(r["total"]), str(r["name"])),
    )
    by_config: dict[str, dict[str, Any]] = {}
    for config, status, n in session.execute(
        _tests(TestRun.config, TestRunTest.status, func.count())
        .where(*test_where(f), TestRun.config.is_not(None), TestRun.config != "")
        .group_by(TestRun.config, TestRunTest.status)
    ):
        row = by_config.setdefault(str(config), {"config": str(config), "rows": []})
        row["rows"].append((status, n))
    configs = [
        {"config": c["config"], "counts": counts(c["rows"], pending)}
        for c in sorted(by_config.values(), key=lambda c: str(c["config"]))
    ]
    for c in configs:
        c["pass_rate"] = pct(c["counts"].get("passed", 0), c["counts"]["total"])
    return {
        "filters": f.echo(),
        "runs": runs_n,
        "tests": tests,
        "pass_rate": pct(tests.get("passed", 0), tests["total"]),
        "done_rate": pct(tests["done"], tests["total"]),
        "results": results,
        "progress": [
            {"day": d.isoformat(), "counts": c, "total": sum(c.values())}
            for d, c in sorted(progress.items())
        ],
        "by_person": by_person,
        "by_config": configs,
    }


# -- 2. run comparison -----------------------------------------------------------------------------


def compare(session: Session, bp_id: int, run_ids: Sequence[int]) -> dict[str, Any] | None:
    """Case x run matrix of 2..10 runs of the dự án (oldest first). `change`: "regression" when
    a case went passed -> failed between two runs that ran it (in run order), "fixed" for
    failed -> passed, "changed" for any other difference. None: a run is not of the dự án."""
    wanted = list(dict.fromkeys(run_ids))
    runs = list(
        session.scalars(
            select(TestRun)
            .where(TestRun.id.in_(wanted), TestRun.business_project_id == bp_id)
            .order_by(TestRun.created_at, TestRun.id)
        )
    )
    if len(runs) != len(wanted):
        return None
    index = {r.id: i for i, r in enumerate(runs)}
    rows: dict[Any, dict[str, Any]] = {}
    for run_id, case_id, title, section, priority, status in session.execute(
        select(
            TestRunTest.run_id,
            TestRunTest.case_id,
            TestRunTest.title,
            TestRunTest.section,
            TestRunTest.priority,
            TestRunTest.status,
        )
        .where(TestRunTest.run_id.in_(wanted))
        .order_by(TestRunTest.run_id, TestRunTest.position, TestRunTest.id)
    ):
        ident = case_id if case_id is not None else f"t:{title}"
        row = rows.setdefault(
            ident,
            {
                "case_id": case_id,
                "case_key": content.key("case", case_id),
                "title": title,
                "section": section,
                "priority": priority,
                "statuses": [None] * len(runs),
            },
        )
        row["statuses"][index[run_id]] = status
    summary_n = {"regression": 0, "fixed": 0, "changed": 0, "same": 0}
    for row in rows.values():
        seen = [s for s in row["statuses"] if s is not None]
        pairs = list(itertools.pairwise(seen))
        if any(a == "passed" and b == "failed" for a, b in pairs):
            change = "regression"
        elif any(a == "failed" and b == "passed" for a, b in pairs):
            change = "fixed"
        elif len(set(seen)) > 1 or len(seen) < len(runs):
            change = "changed"
        else:
            change = "same"
        row["change"] = None if change == "same" else change
        summary_n[change] += 1
    pending = pending_of(session, bp_id)
    run_counts = {
        r.id: counts(
            session.execute(
                select(TestRunTest.status, func.count())
                .where(TestRunTest.run_id == r.id)
                .group_by(TestRunTest.status)
            ),
            pending,
        )
        for r in runs
    }
    order = {"regression": 0, "fixed": 1, "changed": 2, None: 3}
    return {
        "runs": [
            {
                "id": r.id,
                "key": content.key("run", r.id),
                "name": r.name,
                "config": r.config,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "counts": run_counts[r.id],
                "pass_rate": pct(run_counts[r.id].get("passed", 0), run_counts[r.id]["total"]),
            }
            for r in runs
        ],
        "rows": sorted(rows.values(), key=lambda r: (order[r["change"]], str(r["title"]))),
        "summary": summary_n,
    }


# -- 3. coverage -----------------------------------------------------------------------------------


def _latest_results(f: Filters) -> Any:
    """The latest result (in the filters) of each case: (case_id, status)."""
    return (
        _results(TestRunTest.case_id.label("case_id"), TestResult.status.label("status"))
        .where(*result_where(f), TestRunTest.case_id.is_not(None))
        .ext(distinct_on(TestRunTest.case_id))
        .order_by(TestRunTest.case_id, TestResult.created_at.desc(), TestResult.id.desc())
        .subquery()
    )


def coverage(session: Session, f: Filters) -> dict[str, Any]:
    live = _live_cases(f)
    named = (
        select(func.jsonb_array_elements_text(TestCase.jira_keys))
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(*live)
    )
    is_covered = Issue.key.in_(named)
    issue_where = [Issue.source == "jira", Issue.parent_key.is_(None), jira_issue_in([f.bp_id])]
    by_type = [
        {"type": t or "(khác)", "issues": int(n), "covered": int(c), "rate": pct(int(c), int(n))}
        for t, n, c in session.execute(
            select(Issue.type, func.count(), func.count().filter(is_covered))
            .where(*issue_where)
            .group_by(Issue.type)
            .order_by(func.count().desc())
        )
    ]
    issues_n = sum(int(r["issues"] or 0) for r in by_type)
    covered_n = sum(int(r["covered"] or 0) for r in by_type)
    uncovered = [
        {"key": k, "title": t, "type": ty, "state": bug_state(s), "url": u}
        for k, t, ty, s, u in session.execute(
            select(Issue.key, Issue.title, Issue.type, Issue.state, Issue.web_url)
            .where(*issue_where, ~is_covered)
            .order_by(Issue.created_at_src.desc().nulls_last(), Issue.id.desc())
            .limit(LIST_MAX)
        )
    ]
    without_req = int(
        session.scalar(
            select(func.count())
            .select_from(TestCase)
            .join(TestSuite, TestSuite.id == TestCase.suite_id)
            .where(*live, func.jsonb_array_length(TestCase.jira_keys) == 0)
        )
        or 0
    )
    # Sections: cases, cases with a result in the period, cases whose latest result is passed.
    latest = _latest_results(f)
    cases_by: dict[tuple[int, int | None], int] = {}
    for suite_id, section_id, n in session.execute(
        select(TestCase.suite_id, TestCase.section_id, func.count())
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(*live)
        .group_by(TestCase.suite_id, TestCase.section_id)
    ):
        cases_by[(int(suite_id), section_id)] = int(n)
    run_by: dict[tuple[int, int | None], tuple[int, int, int]] = {}
    for suite_id, section_id, n, passed, failed in session.execute(
        select(
            TestCase.suite_id,
            TestCase.section_id,
            func.count(),
            func.count().filter(latest.c.status == "passed"),
            func.count().filter(latest.c.status == "failed"),
        )
        .join(latest, latest.c.case_id == TestCase.id)
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(*live)
        .group_by(TestCase.suite_id, TestCase.section_id)
    ):
        run_by[(int(suite_id), section_id)] = (int(n), int(passed), int(failed))
    suites = dict(
        session.execute(
            select(TestSuite.id, TestSuite.name).where(
                TestSuite.id.in_(sorted({k[0] for k in cases_by}) or [0])
            )
        ).all()
    )
    paths = content.section_paths(session, list(suites))
    sections = []
    for (suite_id, section_id), n in cases_by.items():
        executed, passed, failed = run_by.get((suite_id, section_id), (0, 0, 0))
        sections.append(
            {
                "suite_id": suite_id,
                "suite": suites.get(suite_id, ""),
                "section_id": section_id,
                "section": paths.get(section_id, "") if section_id else "(không thư mục)",
                "cases": n,
                "executed": executed,
                "passed": passed,
                "failed": failed,
                "executed_rate": pct(executed, n),
                "passed_rate": pct(passed, n),
            }
        )
    sections.sort(key=lambda s: (str(s["suite"]), str(s["section"])))
    # Milestones: cases planned for each, and the tests of its runs by status.
    milestones = list(
        session.scalars(
            select(TestMilestone)
            .where(TestMilestone.business_project_id == f.bp_id)
            .order_by(TestMilestone.due_on.desc().nulls_last(), TestMilestone.id.desc())
        )
    )
    planned = dict(
        session.execute(
            select(TestCase.milestone_id, func.count())
            .join(TestSuite, TestSuite.id == TestCase.suite_id)
            .where(*live, TestCase.milestone_id.is_not(None))
            .group_by(TestCase.milestone_id)
        ).all()
    )
    ms_of = func.coalesce(TestRun.milestone_id, TestPlan.milestone_id)
    ms_rows: dict[int, list[tuple[Any, Any]]] = defaultdict(list)
    pending = pending_of(session, f.bp_id)
    mf = Filters(bp_id=f.bp_id, suite_id=f.suite_id, config_id=f.config_id, tz=f.tz)
    for mid, status, n in session.execute(
        _tests(ms_of, TestRunTest.status, func.count())
        .outerjoin(TestPlan, TestPlan.id == TestRun.plan_id)
        .where(*test_where(mf), ms_of.is_not(None))
        .group_by(ms_of, TestRunTest.status)
    ):
        ms_rows[int(mid)].append((status, n))
    ms_out = []
    for m in milestones:
        c = counts(ms_rows.get(m.id, []), pending)
        ms_out.append(
            {
                "id": m.id,
                "key": content.key("milestone", m.id),
                "name": m.name,
                "due_on": m.due_on.isoformat() if m.due_on else None,
                "completed": m.completed_at is not None,
                "cases": int(planned.get(m.id, 0)),
                "tests": c,
                "pass_rate": pct(c.get("passed", 0), c["total"]),
                "done_rate": pct(c["done"], c["total"]),
            }
        )
    total_cases = sum(cases_by.values())
    executed_cases = sum(v[0] for v in run_by.values())
    passed_cases = sum(v[1] for v in run_by.values())
    return {
        "filters": f.echo(),
        "requirements": {
            "issues": issues_n,
            "covered": covered_n,
            "uncovered": issues_n - covered_n,
            "rate": pct(covered_n, issues_n),
            "by_type": by_type,
            "uncovered_list": uncovered,
            "cases_without_requirement": without_req,
        },
        "cases": total_cases,
        "executed": executed_cases,
        "passed": passed_cases,
        "executed_rate": pct(executed_cases, total_cases),
        "passed_rate": pct(passed_cases, total_cases),
        "sections": sections,
        "milestones": ms_out,
    }


# -- 4. defects ------------------------------------------------------------------------------------


def defects(session: Session, f: Filters) -> dict[str, Any]:
    """Bugs named by the results in the filters, their synced Jira state; "blocking" = open and
    a test naming it is still failed or blocked."""
    defect = func.jsonb_array_elements_text(TestResult.defects).label("defect")
    rows = session.execute(
        _results(
            defect,
            TestRunTest.id,
            TestRunTest.case_id,
            TestRunTest.title,
            TestRunTest.section,
            TestRunTest.status,
        ).where(*result_where(f), func.jsonb_array_length(TestResult.defects) > 0)
    )
    bugs: dict[str, dict[str, Any]] = {}
    by_case: dict[Any, dict[str, Any]] = {}
    for key, test_id, case_id, title, section, status in rows:
        bug = bugs.setdefault(
            key, {"key": key, "results": 0, "cases": set(), "tests": set(), "failing": False}
        )
        bug["results"] += 1
        bug["cases"].add(case_id if case_id is not None else f"t:{title}")
        bug["tests"].add(test_id)
        if status in ("failed", "blocked"):
            bug["failing"] = True
        ident = case_id if case_id is not None else f"t:{title}"
        c = by_case.setdefault(
            ident,
            {
                "case_id": case_id,
                "case_key": content.key("case", case_id),
                "title": title,
                "section": section or "",
                "defects": [],
            },
        )
        if key not in c["defects"]:
            c["defects"].append(key)
    states = _issue_states(session, bugs)
    out = []
    for key, bug in bugs.items():
        info = states.get(key, {})
        state = bug_state(info.get("state")) if info else "unknown"
        out.append(
            {
                "key": key,
                "title": info.get("title"),
                "url": info.get("url"),
                "state": state,
                "results": bug["results"],
                "cases": len(bug["cases"]),
                "tests": len(bug["tests"]),
                "blocking": state == "open" and bool(bug["failing"]),
            }
        )
    out.sort(key=lambda b: (not b["blocking"], b["state"] != "open", -b["cases"], b["key"]))
    state_of = {b["key"]: b["state"] for b in out}
    cases_out = []
    sections: dict[str, dict[str, Any]] = {}
    for c in by_case.values():
        c["open"] = sum(1 for k in c["defects"] if state_of.get(k) == "open")
        cases_out.append(c)
        s = sections.setdefault(
            c["section"], {"section": c["section"] or "(không thư mục)", "keys": set()}
        )
        s["keys"].update(c["defects"])
    cases_out.sort(key=lambda c: (-len(c["defects"]), str(c["title"])))
    by_section = sorted(
        (
            {
                "section": s["section"],
                "defects": len(s["keys"]),
                "open": sum(1 for k in s["keys"] if state_of.get(k) == "open"),
            }
            for s in sections.values()
        ),
        key=lambda s: (-int(s["defects"]), str(s["section"])),
    )
    totals = {
        "defects": len(out),
        "open": sum(1 for b in out if b["state"] == "open"),
        "closed": sum(1 for b in out if b["state"] == "closed"),
        "unknown": sum(1 for b in out if b["state"] == "unknown"),
        "blocking": sum(1 for b in out if b["blocking"]),
    }
    return {
        "filters": f.echo(),
        "totals": totals,
        "defects": out,
        "by_case": cases_out[:LIST_MAX],
        "by_section": by_section,
    }


# -- 5. productivity -------------------------------------------------------------------------------


def productivity(session: Session, f: Filters) -> dict[str, Any]:
    """Per person over the period: cases created, cases approved (a change that moved a case to
    "approved"), results recorded by hand (by status) and their time. CI results are a total."""
    start, end = f.start(), f.end()
    people: dict[int, dict[str, Any]] = {}

    def person(uid: int) -> dict[str, Any]:
        return people.setdefault(
            uid,
            {
                "user_id": uid,
                "created": 0,
                "approved": 0,
                "results": 0,
                "counts": {},
                "elapsed_s": 0,
            },
        )

    # Obsolete cases were still created in the period: every case of a live suite counts.
    created_where = [
        tc.suite_in([f.bp_id]),
        TestSuite.archived_at.is_(None),
        TestCase.created_by.is_not(None),
    ]
    if f.suite_id:
        created_where.append(TestCase.suite_id == f.suite_id)
    if start is not None:
        created_where.append(TestCase.created_at >= start)
    if end is not None:
        created_where.append(TestCase.created_at < end)
    for uid, n in session.execute(
        select(TestCase.created_by, func.count())
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(*created_where)
        .group_by(TestCase.created_by)
    ):
        person(int(uid or 0))["created"] = int(n)
    # A revision is the case before one change by `changed_by`; the status after it is the next
    # revision's, or the case's own when that change was the last.
    nxt = aliased(TestCaseRevision)
    after = func.coalesce(
        nxt.snapshot["status"].astext,
        case((TestCase.revision == TestCaseRevision.number + 1, TestCase.status)),
    )
    approved_where = [
        tc.suite_in([f.bp_id]),
        TestSuite.archived_at.is_(None),
        TestCaseRevision.changed_by.is_not(None),
        func.coalesce(TestCaseRevision.snapshot["status"].astext, "") != "approved",
        after == "approved",
    ]
    if f.suite_id:
        approved_where.append(TestCase.suite_id == f.suite_id)
    if start is not None:
        approved_where.append(TestCaseRevision.changed_at >= start)
    if end is not None:
        approved_where.append(TestCaseRevision.changed_at < end)
    for uid, n in session.execute(
        select(TestCaseRevision.changed_by, func.count(func.distinct(TestCaseRevision.case_id)))
        .join(TestCase, TestCase.id == TestCaseRevision.case_id)
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .outerjoin(
            nxt,
            and_(
                nxt.case_id == TestCaseRevision.case_id, nxt.number == TestCaseRevision.number + 1
            ),
        )
        .where(*approved_where)
        .group_by(TestCaseRevision.changed_by)
    ):
        person(int(uid or 0))["approved"] = int(n)
    ci_total = 0
    for uid, source, status, n, elapsed in session.execute(
        _results(
            TestResult.created_by,
            TestResult.source,
            TestResult.status,
            func.count(),
            func.coalesce(func.sum(TestResult.elapsed_s), 0),
        )
        .where(*result_where(f))
        .group_by(TestResult.created_by, TestResult.source, TestResult.status)
    ):
        if source == "ci" or uid is None:
            ci_total += int(n)
            continue
        p = person(int(uid))
        p["results"] += int(n)
        p["counts"][str(status)] = p["counts"].get(str(status), 0) + int(n)
        p["elapsed_s"] += int(elapsed or 0)
    who = names(session, people)
    rows = sorted(
        ({**p, "name": who.get(uid, f"#{uid}")} for uid, p in people.items()),
        key=lambda p: (-(p["results"] + p["created"] + p["approved"]), str(p["name"])),
    )
    return {
        "filters": f.echo(),
        "totals": {
            "people": len(rows),
            "created": sum(p["created"] for p in rows),
            "approved": sum(p["approved"] for p in rows),
            "results": sum(p["results"] for p in rows),
            "ci_results": ci_total,
            "elapsed_s": sum(p["elapsed_s"] for p in rows),
        },
        "people": rows,
    }


# -- 6. automation ---------------------------------------------------------------------------------

AUTOMATED_DAYS = 30


def automation(session: Session, f: Filters, today: date | None = None) -> dict[str, Any]:
    """% automated per week (live automated cases with a CI result in the 30 days before the
    week's end, over the live cases existing then), flaky cases, CI runs and their time."""
    tz = ZoneInfo(f.tz)
    last = f.date_to or today or datetime.now(tz).date()
    first = f.date_from or (last - timedelta(weeks=WEEKS_DEFAULT) + timedelta(days=1))
    week0 = first - timedelta(days=first.weekday())
    weeks: list[tuple[date, datetime]] = []
    ws = week0
    while ws <= last:
        end_day = min(ws + timedelta(days=7), last + timedelta(days=1))
        weeks.append((ws, datetime.combine(end_day, time.min, tz)))
        ws += timedelta(days=7)
    live = _live_cases(f)
    cases = session.execute(
        select(TestCase.id, TestCase.kind, TestCase.created_at)
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(*live)
    ).all()
    automated_ids = {int(c.id) for c in cases if c.kind == "automated"}
    since = weeks[0][1] - timedelta(days=7 + AUTOMATED_DAYS) if weeks else None
    ci_where = [
        TestRun.business_project_id == f.bp_id,
        TestResult.source == "ci",
        TestRunTest.case_id.in_(sorted(automated_ids) or [0]),
    ]
    if f.branch:
        ci_where.append(_branch() == f.branch)
    if since is not None:
        ci_where.append(TestResult.created_at >= since)
    seen: dict[int, list[datetime]] = defaultdict(list)
    for case_id, at in session.execute(
        _results(TestRunTest.case_id, func.max(TestResult.created_at))
        .where(*ci_where)
        .group_by(TestRunTest.case_id, _day(f, TestResult.created_at))
    ):
        seen[int(case_id)].append(at)
    trend = []
    for ws, end in weeks:
        window = end - timedelta(days=AUTOMATED_DAYS)
        total = sum(1 for c in cases if c.created_at is None or c.created_at < end)
        auto = sum(1 for cid, ats in seen.items() if any(window <= a < end for a in ats))
        trend.append(
            {
                "week": ws.isoformat(),
                "cases": total,
                "automated": auto,
                "rate": pct(auto, total),
            }
        )
    flaky_rows = session.execute(
        select(TestCase.id, TestCase.title, TestCase.suite_id, TestCase.section_id, TestSuite.name)
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(*live, TestCase.flaky.is_(True))
        .order_by(TestSuite.name, TestCase.id)
        .limit(LIST_MAX)
    ).all()
    paths = content.section_paths(session, {int(r.suite_id) for r in flaky_rows})
    flaky_n = int(
        session.scalar(
            select(func.count())
            .select_from(TestCase)
            .join(TestSuite, TestSuite.id == TestCase.suite_id)
            .where(*live, TestCase.flaky.is_(True))
        )
        or 0
    )
    flaky = [
        {
            "case_id": int(r.id),
            "case_key": content.key("case", int(r.id)),
            "title": r.title,
            "suite": r.name,
            "section": paths.get(r.section_id, "") if r.section_id else "",
        }
        for r in flaky_rows
    ]
    imp = _ci("import_id")
    ci_rows = session.execute(
        _results(
            TestRun.id,
            TestRun.name,
            imp,
            func.min(TestResult.created_at),
            func.count(),
            func.count().filter(TestResult.status == "failed"),
            func.coalesce(func.sum(TestResult.elapsed_s), 0),
            func.max(_branch()),
            func.max(_ci("commit_sha")),
        )
        .where(*result_where(f), TestResult.source == "ci")
        .group_by(TestRun.id, TestRun.name, imp)
        .order_by(func.min(TestResult.created_at).desc())
        .limit(CI_RUNS_MAX)
    )
    ci_runs = [
        {
            "run_id": int(run_id),
            "run_key": content.key("run", int(run_id)),
            "run_name": name,
            "import_id": int(import_id) if import_id and str(import_id).isdigit() else None,
            "at": at.isoformat() if at else None,
            "results": int(n),
            "failed": int(failed),
            "elapsed_s": int(elapsed or 0),
            "branch": branch,
            "commit_sha": (commit or "")[:12] or None,
        }
        for run_id, name, import_id, at, n, failed, elapsed, branch, commit in ci_rows
    ]
    now_rate = trend[-1]["rate"] if trend else None
    return {
        "filters": f.echo(),
        "cases": len(cases),
        "automated_kind": len(automated_ids),
        "rate": now_rate,
        "trend": trend,
        "flaky_count": flaky_n,
        "flaky": flaky,
        "ci_runs": ci_runs,
    }


# -- 7 / 8. release readiness and the milestone dashboard ------------------------------------------


@dataclass
class Verdict:
    go: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def readiness(
    session: Session,
    bp_id: int,
    milestone_id: int,
    *,
    min_pass_rate: float = PASS_RATE_GO,
    tz: str = "UTC",
) -> dict[str, Any] | None:
    """Go / No-go of a milestone from the tests of its runs (their current status): % passed,
    critical / high tests not run or failed, open bugs named by its results, flaky cases; and
    the pass rate per day of its results. A handful of grouped queries (AC: <= 3 s at 5 000
    results). None: the milestone is not of the dự án."""
    m = session.get(TestMilestone, milestone_id)
    if m is None or m.business_project_id != bp_id:
        return None
    where = [TestRun.business_project_id == bp_id, milestone_runs(milestone_id)]
    by_prio: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
    all_rows: list[tuple[Any, Any]] = []
    for prio, status, n in session.execute(
        _tests(TestRunTest.priority, TestRunTest.status, func.count())
        .where(*where)
        .group_by(TestRunTest.priority, TestRunTest.status)
    ):
        by_prio[str(prio)].append((status, n))
        all_rows.append((status, n))
    pending = pending_of(session, bp_id)
    tests = counts(all_rows, pending)
    runs_n = int(session.scalar(select(func.count()).select_from(TestRun).where(*where)) or 0)
    high_counts = counts([r for p in HIGH for r in by_prio.get(p, [])], pending)
    high_untested = high_counts.get("untested", 0)
    high_failed = high_counts.get("failed", 0)
    risky = [
        {
            "test_id": int(r.id),
            "test_key": content.key("test", int(r.id)),
            "case_id": r.case_id,
            "case_key": content.key("case", r.case_id),
            "title": r.title,
            "priority": r.priority,
            "status": r.status,
            "run_id": int(r.run_id),
            "run_key": content.key("run", int(r.run_id)),
        }
        for r in session.execute(
            _tests(
                TestRunTest.id,
                TestRunTest.case_id,
                TestRunTest.title,
                TestRunTest.priority,
                TestRunTest.status,
                TestRunTest.run_id,
            )
            .where(
                *where,
                TestRunTest.priority.in_(HIGH),
                TestRunTest.status.in_(("untested", "failed")),
            )
            .order_by(
                (TestRunTest.status == "untested").asc(),
                (TestRunTest.priority == "high").asc(),
                TestRunTest.id,
            )
            .limit(LIST_MAX)
        ).all()
    ]
    flaky_n = int(
        session.scalar(
            _tests(func.count(func.distinct(TestRunTest.case_id)))
            .join(TestCase, TestCase.id == TestRunTest.case_id)
            .where(*where, TestCase.flaky.is_(True))
        )
        or 0
    )
    keys = [
        str(k)
        for (k,) in session.execute(
            _results(func.jsonb_array_elements_text(TestResult.defects))
            .where(*where, func.jsonb_array_length(TestResult.defects) > 0)
            .distinct()
        )
    ]
    states = _issue_states(session, keys)
    bugs = [
        {
            "key": k,
            "title": states.get(k, {}).get("title"),
            "url": states.get(k, {}).get("url"),
            "state": bug_state(states[k]["state"]) if k in states else "unknown",
        }
        for k in sorted(keys)
    ]
    open_bugs = [b for b in bugs if b["state"] == "open"]
    f = Filters(bp_id=bp_id, milestone_id=milestone_id, tz=tz)
    day = _day(f, TestResult.created_at)
    trend = [
        {
            "day": d.isoformat(),
            "results": int(n),
            "passed": int(p),
            "failed": int(fl),
            "rate": pct(int(p), int(n)),
        }
        for d, n, p, fl in session.execute(
            _results(
                day,
                func.count(),
                func.count().filter(TestResult.status == "passed"),
                func.count().filter(TestResult.status == "failed"),
            )
            .where(*where, TestResult.status.is_not(None))
            .group_by(day)
            .order_by(day)
        )
    ]
    rate = pct(tests.get("passed", 0), tests["total"])
    verdict = Verdict(go=True)
    if not tests["total"]:
        verdict.reasons.append("Milestone chưa có test nào trong run")
    elif rate is not None and rate < min_pass_rate:
        verdict.reasons.append(f"Tỷ lệ passed {rate}% < {min_pass_rate:g}%")
    if high_failed:
        verdict.reasons.append(f"{high_failed} test ưu tiên Rất cao / Cao đang Failed")
    if high_untested:
        verdict.reasons.append(f"{high_untested} test ưu tiên Rất cao / Cao chưa chạy")
    if open_bugs:
        verdict.reasons.append(f"{len(open_bugs)} bug còn mở")
    if flaky_n:
        verdict.warnings.append(f"{flaky_n} testcase flaky")
    unknown = sum(1 for b in bugs if b["state"] == "unknown")
    if unknown:
        verdict.warnings.append(f"{unknown} bug chưa đồng bộ trạng thái từ Jira")
    verdict.go = not verdict.reasons
    return {
        "milestone": {
            "id": m.id,
            "key": content.key("milestone", m.id),
            "name": m.name,
            "due_on": m.due_on.isoformat() if m.due_on else None,
            "completed": m.completed_at is not None,
        },
        "min_pass_rate": min_pass_rate,
        "runs": runs_n,
        "tests": tests,
        "pass_rate": rate,
        "done_rate": pct(tests["done"], tests["total"]),
        "by_priority": {p: counts(rows, pending) for p, rows in sorted(by_prio.items())},
        "high_untested": high_untested,
        "high_failed": high_failed,
        "risky_tests": risky,
        "bugs": bugs,
        "open_bugs": len(open_bugs),
        "flaky": flaky_n,
        "trend": trend,
        "go": verdict.go,
        "reasons": verdict.reasons,
        "warnings": verdict.warnings,
    }


# -- filter options --------------------------------------------------------------------------------


def options(session: Session, bp_id: int) -> dict[str, Any]:
    suites = session.execute(
        select(TestSuite.id, TestSuite.name)
        .where(tc.suite_in([bp_id]), TestSuite.archived_at.is_(None))
        .order_by(TestSuite.position, TestSuite.name)
    ).all()
    milestones = session.execute(
        select(TestMilestone.id, TestMilestone.name, TestMilestone.completed_at)
        .where(TestMilestone.business_project_id == bp_id)
        .order_by(TestMilestone.completed_at.is_not(None), TestMilestone.due_on.desc().nulls_last())
    ).all()
    plans = session.execute(
        select(TestPlan.id, TestPlan.name)
        .where(TestPlan.business_project_id == bp_id)
        .order_by(TestPlan.id.desc())
        .limit(100)
    ).all()
    configs = session.execute(
        select(TestConfig.id, TestConfig.name, TestConfigGroup.name)
        .join(TestConfigGroup, TestConfigGroup.id == TestConfig.group_id)
        .where(TestConfigGroup.business_project_id == bp_id)
        .order_by(TestConfigGroup.position, TestConfig.position, TestConfig.id)
    ).all()
    runs = session.execute(
        select(TestRun.id, TestRun.name, TestRun.config, TestRun.created_at, TestRun.completed_at)
        .where(TestRun.business_project_id == bp_id)
        .order_by(TestRun.id.desc())
        .limit(200)
    ).all()
    recent = datetime.now(UTC) - timedelta(days=180)
    branches = [
        str(b)
        for (b,) in session.execute(
            _results(_branch())
            .where(
                TestRun.business_project_id == bp_id,
                TestResult.source == "ci",
                TestResult.created_at >= recent,
                _branch().is_not(None),
            )
            .group_by(_branch())
            .order_by(func.max(TestResult.created_at).desc())
            .limit(50)
        )
    ]
    return {
        "suites": [{"id": i, "name": n} for i, n in suites],
        "milestones": [{"id": i, "name": n, "completed": c is not None} for i, n, c in milestones],
        "plans": [{"id": i, "name": n} for i, n in plans],
        "configs": [{"id": i, "name": f"{g}: {n}"} for i, n, g in configs],
        "runs": [
            {
                "id": i,
                "key": content.key("run", i),
                "name": n,
                "config": c,
                "created_at": at.isoformat() if at else None,
                "completed": done is not None,
            }
            for i, n, c, at, done in runs
        ],
        "branches": branches,
        "statuses": status_meta(session, bp_id),
    }


# -- 9. Excel export -------------------------------------------------------------------------------

Sheet = tuple[str, list[str], list[list[Any]]]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Có" if value else "Không"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_cell(v) for v in value)
    return CONTROL.sub("", str(value))


def _status_cols(meta: list[dict[str, Any]], rows: Iterable[dict[str, int]]) -> list[str]:
    """Statuses to show as columns: the known ones present, then any other seen."""
    present: set[str] = set()
    for c in rows:
        present.update(k for k, v in c.items() if k not in ("total", "done") and v)
    known = [m["key"] for m in meta if m["key"] in present]
    return known + sorted(present - set(known))


def sheets_for(kind: str, data: dict[str, Any], meta: list[dict[str, Any]]) -> list[Sheet]:
    """One sheet per section of a report."""
    label = {m["key"]: m["name"] for m in meta}
    out: list[Sheet] = []
    if kind == "summary":
        cols = _status_cols(meta, [data["tests"], data["results"]])
        out.append(
            (
                "Tổng quan",
                ["Chỉ số", "Giá trị"],
                [
                    ["Số run", data["runs"]],
                    ["Số test", data["tests"]["total"]],
                    ["% passed", data["pass_rate"]],
                    ["% đã xong", data["done_rate"]],
                    ["Số kết quả trong kỳ", data["results"]["total"]],
                    *[[f"Test {label.get(s, s)}", data["tests"].get(s, 0)] for s in cols],
                ],
            )
        )
        pcols = _status_cols(meta, [p["counts"] for p in data["progress"]])
        out.append(
            (
                "Tiến độ theo ngày",
                ["Ngày", *[label.get(s, s) for s in pcols], "Tổng"],
                [
                    [p["day"], *[p["counts"].get(s, 0) for s in pcols], p["total"]]
                    for p in data["progress"]
                ],
            )
        )
        bcols = _status_cols(meta, [p["counts"] for p in data["by_person"]])
        out.append(
            (
                "Theo người",
                ["Người", "Nguồn", *[label.get(s, s) for s in bcols], "Tổng", "Thời gian (s)"],
                [
                    [
                        p["name"],
                        p["source"],
                        *[p["counts"].get(s, 0) for s in bcols],
                        p["total"],
                        p["elapsed_s"],
                    ]
                    for p in data["by_person"]
                ],
            )
        )
        ccols = _status_cols(meta, [c["counts"] for c in data["by_config"]])
        out.append(
            (
                "Theo cấu hình",
                ["Cấu hình", *[label.get(s, s) for s in ccols], "Tổng", "% passed"],
                [
                    [
                        c["config"],
                        *[c["counts"].get(s, 0) for s in ccols],
                        c["counts"]["total"],
                        c["pass_rate"],
                    ]
                    for c in data["by_config"]
                ],
            )
        )
    elif kind == "compare":
        runs = data["runs"]
        change = {"regression": "Hồi quy", "fixed": "Đã sửa", "changed": "Thay đổi", None: ""}
        out.append(
            (
                "So sánh",
                [
                    "Testcase",
                    "Tiêu đề",
                    "Thư mục",
                    "Ưu tiên",
                    *[f"{r['key']} {r['name']}" for r in runs],
                    "Thay đổi",
                ],
                [
                    [
                        r["case_key"],
                        r["title"],
                        r["section"],
                        r["priority"],
                        *[label.get(s, s) if s else "" for s in r["statuses"]],
                        change.get(r["change"], ""),
                    ]
                    for r in data["rows"]
                ],
            )
        )
        out.append(
            (
                "Các run",
                ["Run", "Tên", "Cấu hình", "Số test", "% passed"],
                [
                    [r["key"], r["name"], r["config"], r["counts"]["total"], r["pass_rate"]]
                    for r in runs
                ],
            )
        )
    elif kind == "coverage":
        req = data["requirements"]
        out.append(
            (
                "Yêu cầu Jira",
                ["Loại", "Issue", "Có testcase", "% phủ"],
                [[r["type"], r["issues"], r["covered"], r["rate"]] for r in req["by_type"]]
                + [["Tổng", req["issues"], req["covered"], req["rate"]]],
            )
        )
        out.append(
            (
                "Issue chưa có testcase",
                ["Issue", "Tiêu đề", "Loại", "Trạng thái"],
                [[u["key"], u["title"], u["type"], u["state"]] for u in req["uncovered_list"]],
            )
        )
        out.append(
            (
                "Theo thư mục",
                [
                    "Bộ",
                    "Thư mục",
                    "Testcase",
                    "Đã chạy",
                    "Passed",
                    "Failed",
                    "% đã xong",
                    "% passed",
                ],
                [
                    [
                        s["suite"],
                        s["section"],
                        s["cases"],
                        s["executed"],
                        s["passed"],
                        s["failed"],
                        s["executed_rate"],
                        s["passed_rate"],
                    ]
                    for s in data["sections"]
                ],
            )
        )
        out.append(
            (
                "Theo milestone",
                ["Milestone", "Tên", "Hạn", "Testcase", "Test", "% đã xong", "% passed"],
                [
                    [
                        m["key"],
                        m["name"],
                        m["due_on"],
                        m["cases"],
                        m["tests"]["total"],
                        m["done_rate"],
                        m["pass_rate"],
                    ]
                    for m in data["milestones"]
                ],
            )
        )
    elif kind == "defects":
        state = {"open": "Đang mở", "closed": "Đã đóng", "unknown": "Chưa đồng bộ"}
        out.append(
            (
                "Bug",
                ["Bug", "Tiêu đề", "Trạng thái", "Chặn release", "Testcase", "Kết quả"],
                [
                    [
                        b["key"],
                        b["title"],
                        state.get(b["state"], b["state"]),
                        b["blocking"],
                        b["cases"],
                        b["results"],
                    ]
                    for b in data["defects"]
                ],
            )
        )
        out.append(
            (
                "Theo testcase",
                ["Testcase", "Tiêu đề", "Thư mục", "Bug", "Bug mở"],
                [
                    [c["case_key"], c["title"], c["section"], c["defects"], c["open"]]
                    for c in data["by_case"]
                ],
            )
        )
        out.append(
            (
                "Theo thư mục",
                ["Thư mục", "Bug", "Bug mở"],
                [[s["section"], s["defects"], s["open"]] for s in data["by_section"]],
            )
        )
    elif kind == "productivity":
        out.append(
            (
                "Năng suất",
                ["Người", "Testcase tạo", "Testcase duyệt", "Kết quả ghi", "Thời gian (s)"],
                [
                    [p["name"], p["created"], p["approved"], p["results"], p["elapsed_s"]]
                    for p in data["people"]
                ]
                + [
                    [
                        "Tổng",
                        data["totals"]["created"],
                        data["totals"]["approved"],
                        data["totals"]["results"],
                        data["totals"]["elapsed_s"],
                    ]
                ],
            )
        )
    elif kind == "automation":
        out.append(
            (
                "% tự động theo tuần",
                ["Tuần (từ)", "Testcase", "Tự động có CI 30 ngày", "%"],
                [[t["week"], t["cases"], t["automated"], t["rate"]] for t in data["trend"]],
            )
        )
        out.append(
            (
                "Flaky",
                ["Testcase", "Tiêu đề", "Bộ", "Thư mục"],
                [[c["case_key"], c["title"], c["suite"], c["section"]] for c in data["flaky"]],
            )
        )
        out.append(
            (
                "Lượt CI",
                ["Run", "Tên", "Lúc", "Nhánh", "Commit", "Kết quả", "Failed", "Tổng thời gian (s)"],
                [
                    [
                        c["run_key"],
                        c["run_name"],
                        c["at"],
                        c["branch"],
                        c["commit_sha"],
                        c["results"],
                        c["failed"],
                        c["elapsed_s"],
                    ]
                    for c in data["ci_runs"]
                ],
            )
        )
    elif kind == "readiness":
        out.append(
            (
                "Kết luận",
                ["Chỉ số", "Giá trị"],
                [
                    ["Milestone", data["milestone"]["name"]],
                    ["Kết luận", "Go" if data["go"] else "No-go"],
                    *[["Lý do", r] for r in data["reasons"]],
                    *[["Lưu ý", w] for w in data["warnings"]],
                    ["Số run", data["runs"]],
                    ["Số test", data["tests"]["total"]],
                    ["% passed", data["pass_rate"]],
                    ["% đã xong", data["done_rate"]],
                    ["Test Rất cao / Cao chưa chạy", data["high_untested"]],
                    ["Test Rất cao / Cao Failed", data["high_failed"]],
                    ["Bug còn mở", data["open_bugs"]],
                    ["Testcase flaky", data["flaky"]],
                ],
            )
        )
        out.append(
            (
                "Test rủi ro",
                ["Test", "Testcase", "Tiêu đề", "Ưu tiên", "Trạng thái", "Run"],
                [
                    [
                        t["test_key"],
                        t["case_key"],
                        t["title"],
                        t["priority"],
                        label.get(t["status"], t["status"]),
                        t["run_key"],
                    ]
                    for t in data["risky_tests"]
                ],
            )
        )
        out.append(
            (
                "Bug",
                ["Bug", "Tiêu đề", "Trạng thái"],
                [[b["key"], b["title"], b["state"]] for b in data["bugs"]],
            )
        )
        out.append(
            (
                "Pass rate theo ngày",
                ["Ngày", "Kết quả", "Passed", "Failed", "% passed"],
                [
                    [t["day"], t["results"], t["passed"], t["failed"], t["rate"]]
                    for t in data["trend"]
                ],
            )
        )
    return out


def _sheet_title(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Sheet"
    title, n = base, 2
    while title in used:
        title = f"{base[:28]} {n}"
        n += 1
    used.add(title)
    return title


def to_xlsx(title: str, filters: list[tuple[str, str]], sheets: list[Sheet]) -> bytes:
    """One sheet per section; every cell written as text (no formula runs)."""
    import io

    from openpyxl import Workbook
    from openpyxl.cell.cell import TYPE_STRING
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    book.remove(book.active)  # type: ignore[arg-type]
    used: set[str] = set()
    for name, header, rows in sheets:
        ws = book.create_sheet(_sheet_title(name, used))

        def put(r: int, c: int, value: Any, ws: Any = ws) -> None:
            cell = ws.cell(r, c)
            cell.value = _cell(value)
            cell.data_type = TYPE_STRING

        put(1, 1, f"{title} - {name}")
        ws.cell(1, 1).font = Font(bold=True, size=13)
        r = 2
        for label, value in filters:
            put(r, 1, label)
            put(r, 2, value)
            r += 1
        r += 1
        for c, label in enumerate(header, start=1):
            put(r, c, label)
            cell = ws.cell(r, c)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F3A5F")
        for row in rows:
            r += 1
            for c, value in enumerate(row, start=1):
                put(r, c, value)
        for c in range(1, len(header) + 1):
            ws.column_dimensions[get_column_letter(c)].width = 14 if c > 2 else 28
    if not book.sheetnames:
        book.create_sheet("Trống")
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


# -- 10. schedules ---------------------------------------------------------------------------------


def schedule_hooks(session: Session, bp_id: int) -> list[NotificationWebhook]:
    """Webhooks a schedule of this dự án may send to: enabled, of the dự án (not pinned to a
    repo) or of everything."""
    return list(
        session.scalars(
            select(NotificationWebhook)
            .where(
                NotificationWebhook.project_id.is_(None),
                or_(
                    NotificationWebhook.business_project_id == bp_id,
                    NotificationWebhook.business_project_id.is_(None),
                ),
            )
            .order_by(NotificationWebhook.name, NotificationWebhook.id)
        )
    )


def _period(cadence: str, params: dict[str, Any], today: date) -> tuple[date, date]:
    days = params.get("days")
    span = (
        int(days)
        if isinstance(days, int) and 1 <= days <= 366
        else (1 if cadence == "daily" else 7)
    )
    # The period ends yesterday: a report at 08:00 covers whole days.
    end = today - timedelta(days=1)
    return end - timedelta(days=span - 1), end


def _num(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def build_report(
    session: Session, sched: TestReportSchedule, today: date, tz: str
) -> tuple[str, dict[str, Any]]:
    """(one-line text, compact JSON data) of a schedule's report."""
    p = dict(sched.params or {})
    start, end = _period(sched.cadence, p, today)
    f = Filters(
        bp_id=sched.business_project_id,
        suite_id=_num(p.get("suite_id")),
        milestone_id=_num(p.get("milestone_id")),
        plan_id=_num(p.get("plan_id")),
        config_id=_num(p.get("config_id")),
        branch=str(p["branch"]) if isinstance(p.get("branch"), str) and p["branch"] else None,
        date_from=start,
        date_to=end,
        tz=tz,
    )
    period = f"{start.strftime('%d/%m')} - {end.strftime('%d/%m/%Y')}"
    kind = sched.kind
    data: dict[str, Any]
    if kind == "readiness":
        if f.milestone_id is None:
            return f"{sched.name}: chưa chọn milestone", {"error": "milestone_id"}
        rate = p.get("min_pass_rate")
        r = readiness(
            session,
            f.bp_id,
            f.milestone_id,
            min_pass_rate=float(rate) if isinstance(rate, (int, float)) else PASS_RATE_GO,
            tz=tz,
        )
        if r is None:
            return f"{sched.name}: milestone không còn", {"error": "milestone"}
        verdict = "GO" if r["go"] else "NO-GO"
        why = "; ".join(r["reasons"]) or "đạt mọi điều kiện"
        text = (
            f"{sched.name}: {r['milestone']['name']} {verdict} ({why}). "
            f"{r['tests']['total']} test, passed {r['pass_rate'] or 0}%, "
            f"{r['open_bugs']} bug mở, {r['flaky']} flaky"
        )
        data = {
            k: r[k]
            for k in (
                "milestone",
                "go",
                "reasons",
                "warnings",
                "tests",
                "pass_rate",
                "open_bugs",
                "flaky",
            )
        }
    elif kind == "coverage":
        c = coverage(session, f)
        req = c["requirements"]
        text = (
            f"{sched.name} ({period}): {c['cases']} testcase, đã chạy {c['executed_rate'] or 0}%, "
            f"passed {c['passed_rate'] or 0}%; "
            f"issue Jira có testcase {req['covered']}/{req['issues']}"
        )
        data = {
            "cases": c["cases"],
            "executed": c["executed"],
            "passed": c["passed"],
            "requirements": {k: req[k] for k in ("issues", "covered", "rate")},
        }
    elif kind == "defects":
        d = defects(session, f)
        t = d["totals"]
        text = (
            f"{sched.name} ({period}): {t['defects']} bug, {t['open']} đang mở, "
            f"{t['blocking']} chặn release"
        )
        data = {"totals": t, "blocking": [b["key"] for b in d["defects"] if b["blocking"]][:20]}
    elif kind == "automation":
        a = automation(session, f, today=end)
        text = (
            f"{sched.name}: tự động {a['rate'] or 0}% ({a['automated_kind']} case automated), "
            f"{a['flaky_count']} flaky, {len(a['ci_runs'])} lượt CI ({period})"
        )
        data = {"rate": a["rate"], "flaky": a["flaky_count"], "ci_runs": len(a["ci_runs"])}
    else:
        s = summary(session, f)
        res = s["results"]
        facts = ", ".join(f"{v} {k}" for k, v in res.items() if k not in ("total", "done") and v)
        text = (
            f"{sched.name} ({period}): {res['total']} kết quả ({facts or 'không có'}); "
            f"{s['runs']} run, {s['tests']['total']} test, passed {s['pass_rate'] or 0}%"
        )
        data = {"runs": s["runs"], "tests": s["tests"], "results": res, "pass_rate": s["pass_rate"]}
    data["period"] = {"from": start.isoformat(), "to": end.isoformat()}
    return text, data


def due(sched: TestReportSchedule, local_now: datetime) -> bool:
    """Due once a day (daily) or once a week on its weekday, from its hour on (a missed tick
    still sends later that day); `last_sent_at` on the same local day means already sent."""
    if not sched.enabled or sched.webhook_id is None:
        return False
    if local_now.hour < sched.hour:
        return False
    if sched.cadence == "weekly" and (sched.weekday or 0) != local_now.weekday():
        return False
    if sched.last_sent_at is not None:
        sent = sched.last_sent_at.astimezone(local_now.tzinfo).date()
        if sent >= local_now.date():
            return False
    return True


def send_schedule(
    session: Session, sched: TestReportSchedule, now: datetime, tz: str
) -> WebhookDelivery | None:
    """Store one delivery of the schedule's report to its webhook (queued after the caller
    commits, app/testcases/events.py) and mark it sent. None: no usable webhook (gone,
    disabled, or no longer one the schedule's dự án may send to: `schedule_hooks`)."""
    from app.testcases import events

    hook = session.get(NotificationWebhook, sched.webhook_id) if sched.webhook_id else None
    if hook is None or not hook.enabled:
        return None
    if hook.id not in {h.id for h in schedule_hooks(session, sched.business_project_id)}:
        # Moved to another dự án or pinned to a repo since the schedule picked it.
        log.warning(
            "report schedule webhook not allowed for its project, skipped",
            extra={"schedule_id": sched.id, "webhook_id": hook.id},
        )
        return None
    local = now.astimezone(ZoneInfo(tz))
    text, data = build_report(session, sched, local.date(), tz)
    bp = session.get(BusinessProject, sched.business_project_id)
    name = bp.name if bp else None
    payload = {
        "sent_at": now.isoformat(),
        "business_project": {"id": sched.business_project_id, "name": name},
        "schedule": {"id": sched.id, "name": sched.name, "kind": sched.kind},
        "report": data,
        "text": f"[{name}] {text}" if name else text,
    }
    row = WebhookDelivery(webhook_id=hook.id, event=REPORT_EVENT, payload=payload, status="pending")
    session.add(row)
    session.flush()
    session.info.setdefault(events.PENDING, []).append(int(row.id))
    sched.last_sent_at = now
    return row


def send_due(session: Session, now: datetime | None = None, tz: str = "UTC") -> int:
    """The scheduler's tick: send every due schedule once (rows locked, so two schedulers do
    not both send). Each schedule in its own savepoint: one that fails is logged and marked
    sent for the day (no retry every tick), the others still go. Commits once at the end."""
    from app.testcases import events

    at = now or datetime.now(UTC)
    local = at.astimezone(ZoneInfo(tz))
    rows = session.scalars(
        select(TestReportSchedule)
        .where(TestReportSchedule.enabled.is_(True), TestReportSchedule.webhook_id.is_not(None))
        .order_by(TestReportSchedule.id)
        .with_for_update(skip_locked=True)
    ).all()
    sent = 0
    for sched in rows:
        if not due(sched, local):
            continue
        sched_id = sched.id
        queued = len(session.info.get(events.PENDING, []))
        try:
            with session.begin_nested():
                row = send_schedule(session, sched, at, tz)
        except Exception:
            log.exception("report schedule failed", extra={"schedule_id": sched_id})
            del session.info.get(events.PENDING, [])[queued:]  # rolled back with the savepoint
            row = None
        if row is not None:
            sent += 1
        else:
            sched.last_sent_at = at  # nothing sent today: do not retry every tick
    session.commit()
    return sent


ATTACH_GC_TICK_MIN = 5


def schedules_job() -> int:
    """APScheduler entry point (app/scheduler, `testcase-reports`, every 5 minutes); also
    drops the result attachments nobody used (`statuses.gc_attachments`) once an hour."""
    from app.config import get_settings
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        sent = send_due(session, tz=get_settings().report_tz)
    if datetime.now(UTC).minute < ATTACH_GC_TICK_MIN:  # about once an hour on the 5-minute tick
        try:
            with get_sessionmaker()() as session:
                tst.gc_attachments(session)
                session.commit()
        except Exception:
            log.exception("attachment cleanup failed")
    return sent
