"""Task ↔ test quality (docs/specs/31 §19.3-§19.4): failed tests on the cases that cover a task,
a task's test coverage, its Definition of Done, a version's release readiness."""

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TaskTestLink, TestCase, TestRun, TestRunTest
from app.testcases import content


def latest_tests(session: Session, case_ids: list[int]) -> dict[int, TestRunTest]:
    """Each case's test in its most recent run (open runs before completed ones of the same
    age): what the case says now."""
    if not case_ids:
        return {}
    out: dict[int, TestRunTest] = {}
    rows = session.execute(
        select(TestRunTest, TestRun)
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(TestRunTest.case_id.in_(case_ids))
        .order_by(TestRun.id.desc(), TestRunTest.id.desc())
    ).all()
    for test, _run in rows:
        if test.case_id is not None and test.case_id not in out:
            out[int(test.case_id)] = test
    return out


def covering(session: Session, task_ids: list[int]) -> dict[int, list[int]]:
    """task id -> the cases linked to it as `covers`."""
    out: dict[int, list[int]] = defaultdict(list)
    if not task_ids:
        return out
    for task_id, case_id in session.execute(
        select(TaskTestLink.task_id, TaskTestLink.case_id).where(
            TaskTestLink.task_id.in_(task_ids), TaskTestLink.kind == "covers"
        )
    ).all():
        out[int(task_id)].append(int(case_id))
    return out


def failing_for(session: Session, task_ids: list[int]) -> list[dict[str, Any]]:
    """Failed tests (latest run of each case) on the cases covering these tasks."""
    cover = covering(session, task_ids)
    tests = latest_tests(session, sorted({c for cs in cover.values() for c in cs}))
    out = []
    for task_id, cases in cover.items():
        failed = [tests[c] for c in cases if c in tests and tests[c].status == "failed"]
        if failed:
            keys = ", ".join(k for t in failed[:3] if (k := content.key("case", t.case_id)))
            out.append({"task_id": task_id, "text": f"{len(failed)} testcase đang Failed ({keys})"})
    return out


def coverage(session: Session, task_id: int) -> dict[str, Any]:
    """The cases covering a task with their latest status (the Story's Testcase tab)."""
    cases = covering(session, [task_id]).get(task_id, [])
    tests = latest_tests(session, cases)
    rows = []
    by_status: dict[str, int] = defaultdict(int)
    for case in session.scalars(
        select(TestCase).where(TestCase.id.in_(cases)).order_by(TestCase.id)
    ):
        test = tests.get(case.id)
        status = (test.status if test is not None else None) or "untested"
        by_status[status] += 1
        rows.append(
            {
                "case_id": case.id,
                "key": content.key("case", case.id),
                "title": case.title,
                "status": status,
                "run_id": test.run_id if test is not None else None,
                "test_id": test.id if test is not None else None,
            }
        )
    return {
        "total": len(rows),
        "passed": by_status.get("passed", 0),
        "failed": by_status.get("failed", 0),
        "untested": by_status.get("untested", 0),
        "cases": rows,
    }
