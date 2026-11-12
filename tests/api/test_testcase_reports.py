"""Testcase reports (docs/specs/30 §15, G4): every report's numbers equal direct counts of the
seeded rows (AC2), run comparison flags regressions, release readiness of a milestone with
5 000 results stays under its time budget (AC1), the Excel export writes text only, per-person
productivity is shown to managers (others see their own row), and report schedules send once
to their webhook."""

import io
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.jira.settings import JIRA_ID_OFFSET
from app.models import (
    BusinessProject,
    Issue,
    JiraProject,
    JiraProjectScope,
    NotificationWebhook,
    ProjectGrant,
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
    TestSection,
    TestStatus,
    TestSuite,
    WebhookDelivery,
)
from app.testcases import events
from app.testcases import reports as rp
from tests.api.conftest import FakeQueue, World

pytestmark = pytest.mark.integration

API = "/api/v1"
BASE = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)  # 10:00 in Asia/Ho_Chi_Minh


@pytest.fixture(autouse=True)
def _queue(queue: FakeQueue) -> Any:
    saved = events.queue_provider
    events.queue_provider = lambda: queue
    yield
    events.queue_provider = saved


def _tz() -> str:
    return get_settings().report_tz


def _day(at: datetime) -> str:
    return at.astimezone(ZoneInfo(_tz())).date().isoformat()


@dataclass
class R:
    """One seeded result."""

    run: str
    case: int
    status: str
    user: str
    day: int
    defects: list[str] = field(default_factory=list)
    source: str = "manual"
    branch: str | None = None
    elapsed: int | None = None


@dataclass
class Seed:
    bp: int
    suite: TestSuite
    cases: list[TestCase]
    runs: dict[str, TestRun]
    tests: dict[tuple[str, int], TestRunTest]
    results: list[R]
    milestone: TestMilestone
    configs: dict[str, TestConfig]
    users: dict[str, int]


RESULTS = [
    R("chrome", 0, "passed", "an", 0, elapsed=60),
    R("chrome", 1, "failed", "an", 0, ["OMX-1"], elapsed=30),
    R("chrome", 1, "passed", "binh", 1, elapsed=20),
    R("chrome", 2, "blocked", "binh", 1, ["OMX-2"]),
    R("chrome", 3, "skipped", "an", 2),
    R("firefox", 0, "failed", "an", 1, ["OMX-1", "OMX-3"]),
    R("firefox", 1, "passed", "binh", 2),
    R("firefox", 2, "retest", "an", 2),
    R("ci", 0, "passed", "admin", 2, source="ci", branch="main", elapsed=5),
    R("ci", 4, "failed", "admin", 2, ["OMX-9"], source="ci", branch="main", elapsed=7),
    R("ci", 5, "passed", "admin", 3, source="ci", branch="dev", elapsed=9),
]


def _seed(db: Session, world: World) -> Seed:
    bp = world.project.business_project_id
    assert bp is not None
    users = {"an": world.an.id, "binh": world.binh.id, "admin": world.admin.id}
    suite = TestSuite(business_project_id=bp, name="CSKH", position=1)
    db.add(suite)
    db.flush()
    sec_a = TestSection(suite_id=suite.id, name="Khách hàng", position=1)
    sec_b = TestSection(suite_id=suite.id, name="Đơn hàng", position=2)
    db.add_all([sec_a, sec_b])
    db.flush()
    specs: list[dict[str, Any]] = [
        {"section_id": sec_a.id, "priority": "critical", "jira_keys": ["OMX-1"]},
        {"section_id": sec_a.id, "priority": "high", "jira_keys": ["OMX-2"]},
        {"section_id": sec_a.id, "priority": "medium"},
        {"section_id": sec_b.id, "priority": "low"},
        {"section_id": sec_b.id, "priority": "high", "kind": "automated", "flaky": True},
        {"section_id": None, "priority": "medium", "kind": "automated"},
    ]
    cases = []
    for i, spec in enumerate(specs):
        case = TestCase(
            suite_id=suite.id,
            title=f"Case {i}",
            position=i,
            created_by=users["an"] if i % 2 == 0 else users["binh"],
            created_at=BASE - timedelta(days=30),
            **spec,
        )
        db.add(case)
        cases.append(case)
    db.flush()
    milestone = TestMilestone(business_project_id=bp, name="Release 1")
    db.add(milestone)
    db.flush()
    plan = TestPlan(business_project_id=bp, name="Plan 1", milestone_id=milestone.id)
    group = TestConfigGroup(business_project_id=bp, name="Trình duyệt")
    db.add_all([plan, group])
    db.flush()
    configs = {
        "chrome": TestConfig(group_id=group.id, name="Chrome"),
        "firefox": TestConfig(group_id=group.id, name="Firefox"),
    }
    db.add_all(configs.values())
    db.flush()
    runs = {
        "chrome": TestRun(
            business_project_id=bp,
            plan_id=plan.id,
            name="R Chrome",
            config="Chrome",
            config_ids=[configs["chrome"].id],
            created_at=BASE - timedelta(days=1),
        ),
        "firefox": TestRun(
            business_project_id=bp,
            plan_id=plan.id,
            name="R Firefox",
            config="Firefox",
            config_ids=[configs["firefox"].id],
            created_at=BASE - timedelta(hours=20),
        ),
        "ci": TestRun(
            business_project_id=bp, name="CI", source="ci", created_at=BASE - timedelta(hours=10)
        ),
    }
    db.add_all(runs.values())
    db.flush()
    tests: dict[tuple[str, int], TestRunTest] = {}
    members = {"chrome": [0, 1, 2, 3], "firefox": [0, 1, 2, 3], "ci": [0, 4, 5]}
    for name, idx in members.items():
        for i in idx:
            t = TestRunTest(
                run_id=runs[name].id,
                case_id=cases[i].id,
                title=cases[i].title,
                priority=cases[i].priority,
                type="functional",
                section=None,
                position=i,
            )
            db.add(t)
            tests[(name, i)] = t
    db.flush()
    for r in RESULTS:
        t = tests[(r.run, r.case)]
        at = BASE + timedelta(days=r.day)
        db.add(
            TestResult(
                test_id=t.id,
                status=r.status,
                created_by=users[r.user],
                created_at=at,
                defects=r.defects,
                source=r.source,
                elapsed_s=r.elapsed,
                ci={"branch": r.branch, "import_id": 1, "commit_sha": "abc"} if r.branch else {},
            )
        )
        t.status, t.tested_at = r.status, at  # the latest result wins (seeded in order)
    # A comment-only result never counts.
    db.add(TestResult(test_id=tests[("chrome", 0)].id, status=None, comment="ghi chú"))
    db.flush()
    return Seed(bp, suite, cases, runs, tests, RESULTS, milestone, configs, users)


def _get(client: TestClient, h: dict[str, str], kind: str, **params: Any) -> Any:
    got = client.get(f"{API}/testcases/reports/{kind}", headers=h, params=params)
    assert got.status_code == 200, got.text
    return got.json()


def _jira(db: Session, bp: int, states: dict[int, str]) -> None:
    project = JiraProject(key="OMX", name="Omicx")
    db.add(project)
    db.flush()
    db.add(JiraProjectScope(jira_project_id=project.id, business_project_id=bp))
    for n, state in states.items():
        db.add(
            Issue(
                id=JIRA_ID_OFFSET + project.id * 1000 + n,
                source="jira",
                jira_project_id=project.id,
                iid=n,
                key=f"OMX-{n}",
                title=f"Việc {n}",
                state=state,
                type="feature" if n < 5 else "bug",
                created_at_src=BASE - timedelta(days=n),
            )
        )
    db.flush()


def _status_counts(
    statuses: list[str], pending: tuple[str, ...] = ("untested", "retest")
) -> dict[str, int]:
    """ "done" = not Untested / Retest (nor a custom not-done status): the runs' rule."""
    out = dict(Counter(statuses))
    out["total"] = len(statuses)
    out["done"] = len(statuses) - sum(out.get(k, 0) for k in pending)
    return out


def test_summary_equals_the_raw_data(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    s = _seed(db_session, world)
    h = as_user(world.admin)
    data = _get(client, h, "summary", business_project_id=s.bp)
    assert data["runs"] == 3
    assert data["tests"] == _status_counts([t.status for t in s.tests.values()])
    assert data["results"] == _status_counts([r.status for r in s.results])
    # Progress per day = results grouped by their local day and status.
    by_day: dict[str, Counter[str]] = {}
    for r in s.results:
        by_day.setdefault(_day(BASE + timedelta(days=r.day)), Counter())[r.status] += 1
    assert {p["day"]: p["counts"] for p in data["progress"]} == {
        d: dict(c) for d, c in by_day.items()
    }
    # By person (and source): the counts and the time.
    people: dict[tuple[int, str], Counter[str]] = {}
    elapsed: Counter[tuple[int, str]] = Counter()
    for r in s.results:
        k = (s.users[r.user], r.source)
        people.setdefault(k, Counter())[r.status] += 1
        elapsed[k] += r.elapsed or 0
    got = {(p["user_id"], p["source"]): p for p in data["by_person"]}
    assert set(got) == set(people)
    for k, c in people.items():
        assert got[k]["counts"] == dict(c) and got[k]["total"] == sum(c.values())
        assert got[k]["elapsed_s"] == elapsed[k]
    assert data["people_all"] is True
    # By configuration: the tests of each plan run.
    configs = {c["config"]: c["counts"] for c in data["by_config"]}
    assert configs == {
        "Chrome": _status_counts([s.tests[("chrome", i)].status for i in range(4)]),
        "Firefox": _status_counts([s.tests[("firefox", i)].status for i in range(4)]),
    }

    # Filters: a period, a branch, a configuration, a milestone.
    day1 = (BASE + timedelta(days=1)).astimezone(ZoneInfo(_tz())).date()
    one_day = _get(client, h, "summary", business_project_id=s.bp, **{"from": day1, "to": day1})
    assert one_day["results"] == _status_counts([r.status for r in s.results if r.day == 1])
    main = _get(client, h, "summary", business_project_id=s.bp, branch="main")
    assert main["results"] == _status_counts([r.status for r in s.results if r.branch == "main"])
    assert main["tests"]["total"] == 2  # tests with a CI result on main
    chrome = _get(client, h, "summary", business_project_id=s.bp, config_id=s.configs["chrome"].id)
    assert chrome["results"] == _status_counts([r.status for r in s.results if r.run == "chrome"])
    ms = _get(client, h, "summary", business_project_id=s.bp, milestone_id=s.milestone.id)
    assert ms["runs"] == 2
    assert ms["results"]["total"] == sum(1 for r in s.results if r.run != "ci")
    # Another dự án's milestone / a reversed period are refused.
    bad = client.get(
        f"{API}/testcases/reports/summary",
        headers=h,
        params={"business_project_id": s.bp, "from": "2026-11-05", "to": "2026-11-01"},
    )
    assert bad.status_code == 422
    assert (
        client.get(
            f"{API}/testcases/reports/summary",
            headers=h,
            params={"business_project_id": s.bp, "milestone_id": 999_999},
        ).status_code
        == 404
    )


def test_compare_flags_regressions_and_fixes(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    s = _seed(db_session, world)
    h = as_user(world.an)
    ids = f"{s.runs['firefox'].id},{s.runs['chrome'].id}"
    data = _get(client, h, "compare", business_project_id=s.bp, run_ids=ids)
    assert [r["id"] for r in data["runs"]] == [s.runs["chrome"].id, s.runs["firefox"].id]
    rows = {r["case_id"]: r for r in data["rows"]}
    c = s.cases
    assert rows[c[0].id]["statuses"] == ["passed", "failed"]
    assert rows[c[0].id]["change"] == "regression"
    assert rows[c[2].id]["statuses"] == ["blocked", "retest"]
    assert rows[c[2].id]["change"] == "changed"
    assert rows[c[1].id]["change"] is None  # passed, passed
    assert data["summary"]["regression"] == 1
    assert data["runs"][0]["counts"] == _status_counts(
        [s.tests[("chrome", i)].status for i in range(4)]
    )
    # Fixed: failed -> passed.
    ci = _get(
        client,
        h,
        "compare",
        business_project_id=s.bp,
        run_ids=f"{s.runs['firefox'].id},{s.runs['ci'].id}",
    )
    assert {r["case_id"]: r["change"] for r in ci["rows"]}[c[0].id] == "fixed"
    for bad in ("1", ",".join(str(i) for i in range(1, 13)), "a,b"):
        got = client.get(
            f"{API}/testcases/reports/compare",
            headers=h,
            params={"business_project_id": s.bp, "run_ids": bad},
        )
        assert got.status_code == 422
    other = client.get(
        f"{API}/testcases/reports/compare",
        headers=h,
        params={"business_project_id": s.bp, "run_ids": f"{s.runs['ci'].id},999999"},
    )
    assert other.status_code == 404


def test_coverage_equals_the_raw_data(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    s = _seed(db_session, world)
    _jira(db_session, s.bp, {1: "opened", 2: "closed", 3: "opened", 7: "opened"})
    h = as_user(world.an)
    data = _get(client, h, "coverage", business_project_id=s.bp)
    req = data["requirements"]
    assert (req["issues"], req["covered"], req["uncovered"]) == (4, 2, 2)
    assert {u["key"] for u in req["uncovered_list"]} == {"OMX-3", "OMX-7"}
    assert req["cases_without_requirement"] == 4
    assert {t["type"]: (t["issues"], t["covered"]) for t in req["by_type"]} == {
        "feature": (3, 2),
        "bug": (1, 0),
    }
    # Sections: cases, cases with a result, cases whose latest result passed.
    latest: dict[int, tuple[int, str]] = {}
    for n, r in enumerate(s.results):
        latest[r.case] = (n, r.status)
    sections = {(x["section_id"]): x for x in data["sections"]}
    for section_id in {c.section_id for c in s.cases}:
        idx = [i for i, c in enumerate(s.cases) if c.section_id == section_id]
        row = sections[section_id]
        assert row["cases"] == len(idx)
        assert row["executed"] == sum(1 for i in idx if i in latest)
        assert row["passed"] == sum(1 for i in idx if latest.get(i, (0, ""))[1] == "passed")
    assert data["cases"] == 6 and data["executed"] == len(latest)
    (ms,) = [m for m in data["milestones"] if m["id"] == s.milestone.id]
    assert ms["tests"] == _status_counts(
        [t.status for (run, _), t in s.tests.items() if run != "ci"]
    )


def test_defects_with_jira_state(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    s = _seed(db_session, world)
    _jira(db_session, s.bp, {1: "opened", 2: "closed", 3: "opened"})
    h = as_user(world.an)
    data = _get(client, h, "defects", business_project_id=s.bp)
    named = {d for r in s.results for d in r.defects}
    bugs = {b["key"]: b for b in data["defects"]}
    assert set(bugs) == named
    assert bugs["OMX-1"]["state"] == "open" and bugs["OMX-2"]["state"] == "closed"
    assert bugs["OMX-9"]["state"] == "unknown"
    assert bugs["OMX-1"]["results"] == sum(1 for r in s.results if "OMX-1" in r.defects)
    # OMX-1: open, and the Firefox test naming it is still failed.
    assert bugs["OMX-1"]["blocking"] is True
    # OMX-3: open, but its test (firefox case 0) is failed too -> blocking as well.
    assert bugs["OMX-3"]["blocking"] is True
    assert data["totals"] == {
        "defects": 4,
        "open": 2,
        "closed": 1,
        "unknown": 1,
        "blocking": 2,
    }
    by_case = {c["case_id"]: c for c in data["by_case"]}
    assert set(by_case[s.cases[0].id]["defects"]) == {"OMX-1", "OMX-3"}


def test_productivity_rows_by_permission(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    s = _seed(db_session, world)
    # binh approved case 1 (draft -> approved): a revision of the case before, by binh.
    case = s.cases[1]
    db_session.add(
        TestCaseRevision(
            case_id=case.id,
            number=1,
            snapshot={"status": "draft"},
            via="editor",
            changed_by=world.binh.id,
            changed_at=BASE,
        )
    )
    case.revision, case.status = 2, "approved"
    db_session.flush()
    admin = _get(client, as_user(world.admin), "productivity", business_project_id=s.bp)
    rows = {p["user_id"]: p for p in admin["people"]}
    assert admin["people_all"] is True
    manual = [r for r in s.results if r.source == "manual"]
    for name in ("an", "binh"):
        uid = s.users[name]
        assert rows[uid]["results"] == sum(1 for r in manual if r.user == name)
        assert rows[uid]["created"] == sum(1 for c in s.cases if c.created_by == uid)
    assert rows[world.binh.id]["approved"] == 1 and rows[world.an.id]["approved"] == 0
    assert admin["totals"]["ci_results"] == sum(1 for r in s.results if r.source == "ci")
    assert admin["totals"]["results"] == len(manual)
    # A member sees their own row only, the totals stay whole.
    mine = _get(client, as_user(world.an), "productivity", business_project_id=s.bp)
    assert mine["people_all"] is False
    assert [p["user_id"] for p in mine["people"]] == [world.an.id]
    assert mine["totals"] == admin["totals"]
    summary = _get(client, as_user(world.an), "summary", business_project_id=s.bp)
    assert {p["user_id"] for p in summary["by_person"]} <= {world.an.id, None}


def test_automation_trend_flaky_and_ci_time(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    s = _seed(db_session, world)
    h = as_user(world.an)
    last = (BASE + timedelta(days=3)).astimezone(ZoneInfo(_tz())).date()
    data = _get(
        client,
        h,
        "automation",
        business_project_id=s.bp,
        **{"from": last - timedelta(days=20), "to": last},
    )
    # Cases 4 and 5 are automated with a CI result in the last 30 days.
    assert data["trend"][-1]["automated"] == 2 and data["trend"][-1]["cases"] == 6
    assert data["rate"] == round(100 * 2 / 6, 1)
    assert data["flaky_count"] == 1 and data["flaky"][0]["case_id"] == s.cases[4].id
    (ci,) = data["ci_runs"]
    ci_results = [r for r in s.results if r.source == "ci"]
    assert ci["results"] == len(ci_results)
    assert ci["elapsed_s"] == sum(r.elapsed or 0 for r in ci_results)
    assert ci["failed"] == sum(1 for r in ci_results if r.status == "failed")


def test_readiness_verdict_and_reasons(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    s = _seed(db_session, world)
    _jira(db_session, s.bp, {1: "opened", 2: "closed", 3: "closed"})
    h = as_user(world.an)
    data = _get(client, h, "readiness", business_project_id=s.bp, milestone_id=s.milestone.id)
    tests = [t for (run, _), t in s.tests.items() if run != "ci"]
    assert data["tests"] == _status_counts([t.status for t in tests])
    high = [t for t in tests if t.priority in ("critical", "high")]
    assert data["high_failed"] == sum(1 for t in high if t.status == "failed")
    assert data["open_bugs"] == 1  # OMX-1
    assert data["go"] is False
    assert any("bug còn mở" in r for r in data["reasons"])
    assert any("Failed" in r for r in data["reasons"])
    trend = {t["day"]: t for t in data["trend"]}
    ms_results = [r for r in s.results if r.run != "ci"]
    for d, t in trend.items():
        day = [r for r in ms_results if _day(BASE + timedelta(days=r.day)) == d]
        assert t["results"] == len(day)
        assert t["passed"] == sum(1 for r in day if r.status == "passed")
    # Without a milestone: 422.
    no = client.get(
        f"{API}/testcases/reports/readiness", headers=h, params={"business_project_id": s.bp}
    )
    assert no.status_code == 422


def test_readiness_of_5000_results_within_budget(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """AC1: a milestone with 5 000 results answers in <= 3 s (aggregate queries only)."""
    bp = world.project.business_project_id
    assert bp is not None
    suite = TestSuite(business_project_id=bp, name="Lớn", position=1)
    milestone = TestMilestone(business_project_id=bp, name="Big")
    db_session.add_all([suite, milestone])
    db_session.flush()
    case_ids = list(
        db_session.scalars(
            insert(TestCase).returning(TestCase.id),
            [
                {
                    "suite_id": suite.id,
                    "title": f"C {i}",
                    "position": i,
                    "priority": ("critical", "high", "medium", "low")[i % 4],
                    "flaky": i % 50 == 0,
                }
                for i in range(1_000)
            ],
        )
    )
    run_ids = list(
        db_session.scalars(
            insert(TestRun).returning(TestRun.id),
            [
                {"business_project_id": bp, "milestone_id": milestone.id, "name": f"R{i}"}
                for i in range(5)
            ],
        )
    )
    statuses = ("passed", "failed", "blocked", "retest", "untested")
    test_ids = list(
        db_session.scalars(
            insert(TestRunTest).returning(TestRunTest.id),
            [
                {
                    "run_id": run_id,
                    "case_id": cid,
                    "title": "t",
                    "priority": ("critical", "high", "medium", "low")[n % 4],
                    "type": "functional",
                    "status": statuses[n % 5],
                    "position": n,
                }
                for run_id in run_ids
                for n, cid in enumerate(case_ids)
            ],
        )
    )
    db_session.execute(
        insert(TestResult),
        [
            {
                "test_id": tid,
                "status": statuses[n % 4],
                "created_at": BASE + timedelta(hours=n % 72),
                "defects": [f"OMX-{n % 30}"] if n % 10 == 0 else [],
            }
            for n, tid in enumerate(test_ids)
        ],
    )
    db_session.flush()
    assert len(test_ids) == 5_000
    h = as_user(world.admin)
    started = time.perf_counter()
    data = _get(client, h, "readiness", business_project_id=bp, milestone_id=milestone.id)
    spent = time.perf_counter() - started
    assert spent <= 3.0, spent
    assert data["tests"]["total"] == 5_000
    assert data["tests"]["passed"] == 1_000 and data["tests"]["untested"] == 1_000
    assert sum(t["results"] for t in data["trend"]) == 5_000
    assert data["flaky"] == 20


def test_excel_export_writes_text_cells(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from openpyxl import load_workbook

    s = _seed(db_session, world)
    h = as_user(world.admin)
    # A test title that looks like a formula stays text.
    s.tests[("chrome", 0)].title = "=HYPERLINK(1)"
    db_session.flush()
    for kind, extra in (
        ("summary", {}),
        ("coverage", {}),
        ("defects", {}),
        ("productivity", {}),
        ("automation", {}),
        ("readiness", {"milestone_id": s.milestone.id}),
        ("compare", {"run_ids": f"{s.runs['chrome'].id},{s.runs['firefox'].id}"}),
    ):
        got = client.get(
            f"{API}/testcases/reports/{kind}/export",
            headers=h,
            params={"business_project_id": s.bp, **extra},
        )
        assert got.status_code == 200, (kind, got.text)
        book = load_workbook(io.BytesIO(got.content))
        assert book.sheetnames, kind
        for ws in book.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    assert cell.value is None or cell.data_type == "s", (kind, cell.coordinate)
        if kind == "compare":
            values = [c.value for row in book.worksheets[0].iter_rows() for c in row]
            assert "=HYPERLINK(1)" in values


def test_report_needs_tests_view(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    s = _seed(db_session, world)
    db_session.execute(
        delete(ProjectGrant).where(
            ProjectGrant.business_project_id == s.bp, ProjectGrant.user_id == world.outsider.id
        )
    )
    db_session.flush()
    got = client.get(
        f"{API}/testcases/reports/summary",
        headers=as_user(world.outsider),
        params={"business_project_id": s.bp},
    )
    assert got.status_code == 404


def _hook(db: Session, bp: int, **kw: Any) -> NotificationWebhook:
    row = NotificationWebhook(
        name="Chat QA",
        url="https://h.example.com/qa",
        events=["run.closed"],
        format="slack",
        business_project_id=bp,
        **kw,
    )
    db.add(row)
    db.flush()
    return row


def test_schedules_crud_and_sending_once(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    s = _seed(db_session, world)
    hook = _hook(db_session, s.bp)
    admin = as_user(world.admin)
    member = as_user(world.an)
    body = {
        "business_project_id": s.bp,
        "name": "Tuần QA",
        "kind": "summary",
        "cadence": "weekly",
        "weekday": 0,
        "hour": 8,
        "webhook_id": hook.id,
        "params": {"days": 7, "junk": 1},
    }
    assert (
        client.post(f"{API}/testcases/report-schedules", headers=member, json=body).status_code
        == 403
    )
    made = client.post(f"{API}/testcases/report-schedules", headers=admin, json=body)
    assert made.status_code == 201, made.text
    sched = made.json()
    assert sched["params"] == {"days": 7} and sched["webhook_name"] == "Chat QA"
    hooks = client.get(
        f"{API}/testcases/report-schedules/webhooks",
        headers=admin,
        params={"business_project_id": s.bp},
    ).json()
    assert [x["id"] for x in hooks] == [hook.id]
    # Readiness needs a milestone; a hook of another scope is refused.
    bad = client.patch(
        f"{API}/testcases/report-schedules/{sched['id']}", headers=admin, json={"kind": "readiness"}
    )
    assert bad.status_code == 422
    other = NotificationWebhook(
        name="x", url="https://h.example.com/x", events=[], project_id=world.project.id
    )
    db_session.add(other)
    db_session.flush()
    assert (
        client.patch(
            f"{API}/testcases/report-schedules/{sched['id']}",
            headers=admin,
            json={"webhook_id": other.id},
        ).status_code
        == 422
    )
    listed = client.get(
        f"{API}/testcases/report-schedules", headers=admin, params={"business_project_id": s.bp}
    ).json()
    assert [x["id"] for x in listed] == [sched["id"]]

    # The tick: Monday 08:05 local sends once; a later tick the same day does not.
    tz = ZoneInfo(_tz())
    monday = datetime(2026, 11, 9, 8, 5, tzinfo=tz)
    assert monday.weekday() == 0
    before = datetime(2026, 11, 9, 7, 55, tzinfo=tz)
    assert rp.send_due(db_session, before.astimezone(UTC), _tz()) == 0
    assert rp.send_due(db_session, monday.astimezone(UTC), _tz()) == 1
    assert rp.send_due(db_session, (monday + timedelta(hours=3)).astimezone(UTC), _tz()) == 0
    (delivery,) = db_session.scalars(
        select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id)
    ).all()
    assert delivery.event == rp.REPORT_EVENT and delivery.status == "pending"
    assert "Tuần QA" in delivery.payload["text"]
    assert delivery.payload["report"]["period"] == {"from": "2026-11-02", "to": "2026-11-08"}
    # The period's numbers are those of the raw data.
    assert delivery.payload["report"]["results"]["total"] == len(s.results)
    assert delivery.id in [args[0] for f, args, _ in queue.jobs if f == events.JOB]
    # Tuesday: not its weekday; next Monday: again.
    assert rp.send_due(db_session, (monday + timedelta(days=1)).astimezone(UTC), _tz()) == 0
    assert rp.send_due(db_session, (monday + timedelta(days=7)).astimezone(UTC), _tz()) == 1

    # Send now, then delete.
    now = client.post(f"{API}/testcases/report-schedules/{sched['id']}/send", headers=admin)
    assert now.status_code == 200, now.text
    assert now.json()["text"].startswith("[Demo] Tuần QA")
    assert (
        client.delete(f"{API}/testcases/report-schedules/{sched['id']}", headers=admin).status_code
        == 204
    )
    assert db_session.get(TestReportSchedule, sched["id"]) is None


def test_options_list_filters(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    s = _seed(db_session, world)
    data = _get(client, as_user(world.an), "options", business_project_id=s.bp)
    assert [x["id"] for x in data["suites"]] == [s.suite.id]
    assert data["branches"] == [] or set(data["branches"]) <= {"main", "dev"}
    assert {x["key"] for x in data["statuses"]} >= {"untested", "passed", "skipped"}
    assert data["can_manage"] is False
    assert date.fromisoformat(data["runs"][0]["created_at"][:10])


def test_done_follows_the_not_done_statuses(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """ "done" (and done_rate) leave out Retest and custom not-done statuses, as the runs'
    remaining estimate does; a custom done status counts."""
    s = _seed(db_session, world)
    db_session.add_all(
        [
            TestStatus(
                business_project_id=s.bp,
                key="cho_xn",
                name="Chờ XN",
                color="warn",
                done=False,
                position=1,
            ),
            TestStatus(
                business_project_id=s.bp,
                key="bo_qua",
                name="Bỏ qua",
                color="muted",
                done=True,
                position=2,
            ),
        ]
    )
    s.tests[("chrome", 0)].status = "cho_xn"
    s.tests[("chrome", 3)].status = "bo_qua"
    db_session.flush()
    statuses = [t.status for t in s.tests.values()]
    data = _get(client, as_user(world.admin), "summary", business_project_id=s.bp)
    assert data["tests"] == _status_counts(statuses, ("untested", "retest", "cho_xn"))
    assert data["tests"]["done"] == len(statuses) - sum(
        1 for x in statuses if x in ("untested", "retest", "cho_xn")
    )
    assert data["done_rate"] == rp.pct(data["tests"]["done"], data["tests"]["total"])
    options = _get(client, as_user(world.admin), "options", business_project_id=s.bp)
    meta = {m["key"]: m["done"] for m in options["statuses"]}
    assert meta["retest"] is False and meta["untested"] is False and meta["passed"] is True
    assert meta["cho_xn"] is False and meta["bo_qua"] is True
    assert rp.counts([("passed", 2), ("retest", 1), ("untested", 1)])["done"] == 3
    assert rp.counts([("passed", 2), ("retest", 1)], {"untested", "retest"})["done"] == 2


def _daily(db: Session, bp: int, hook: NotificationWebhook, name: str) -> TestReportSchedule:
    row = TestReportSchedule(
        business_project_id=bp,
        name=name,
        kind="summary",
        cadence="daily",
        hour=8,
        webhook_id=hook.id,
        params={},
    )
    db.add(row)
    db.flush()
    return row


def test_send_skips_a_hook_no_longer_allowed(
    world: World, db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    s = _seed(db_session, world)
    hook = _hook(db_session, s.bp)
    sched = _daily(db_session, s.bp, hook, "Ngày QA")
    # Since the schedule picked it, the hook was pinned to a repo: not a schedule hook any more.
    hook.project_id = world.project.id
    db_session.flush()
    at = datetime(2026, 11, 9, 8, 5, tzinfo=ZoneInfo(_tz())).astimezone(UTC)
    with caplog.at_level("WARNING", logger="app.testcases.reports"):
        assert rp.send_due(db_session, at, _tz()) == 0
    assert "not allowed" in caplog.text
    assert (
        db_session.scalars(
            select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id)
        ).all()
        == []
    )
    assert sched.last_sent_at == at  # not retried every tick
    # Another dự án's hook is refused the same way.
    other = BusinessProject(name="Khác G4")
    db_session.add(other)
    db_session.flush()
    hook.project_id, hook.business_project_id = None, other.id
    sched.last_sent_at = None
    db_session.flush()
    assert rp.send_schedule(db_session, sched, at, _tz()) is None


def test_one_broken_schedule_does_not_stop_the_others(
    world: World,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    s = _seed(db_session, world)
    hook = _hook(db_session, s.bp)
    broken = _daily(db_session, s.bp, hook, "Hỏng")
    fine = _daily(db_session, s.bp, hook, "Tốt")
    real = rp.build_report

    def build(session: Session, sched: TestReportSchedule, today: date, tz: str) -> Any:
        if sched.id == broken.id:
            session.execute(text("SELECT 1 / 0"))  # a database error inside the savepoint
        return real(session, sched, today, tz)

    monkeypatch.setattr(rp, "build_report", build)
    at = datetime(2026, 11, 9, 8, 5, tzinfo=ZoneInfo(_tz())).astimezone(UTC)
    with caplog.at_level("ERROR", logger="app.testcases.reports"):
        assert rp.send_due(db_session, at, _tz()) == 1
    assert "report schedule failed" in caplog.text
    (delivery,) = db_session.scalars(
        select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id)
    ).all()
    assert delivery.payload["schedule"]["id"] == fine.id
    db_session.refresh(broken)
    assert broken.last_sent_at == at  # marked for the day: not retried every 5 minutes
    assert rp.send_due(db_session, at + timedelta(minutes=5), _tz()) == 0
