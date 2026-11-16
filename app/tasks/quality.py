"""Task ↔ test quality (docs/specs/31 §19.3-§19.4): failed tests on the cases that cover a task,
a task's test coverage, its Definition of Done, a version's release readiness."""

from collections import defaultdict
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models import (
    MergeRequest,
    Task,
    TaskGitLink,
    TaskSpace,
    TaskStatus,
    TaskTestLink,
    TaskVersion,
    TestCase,
    TestRun,
    TestRunTest,
)
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


# -- Definition of Done (§19.4) ---------------------------------------------------------------

DOD_CHECKS: dict[str, str] = {
    "has_mr": "Có MR",
    "mr_merged": "MR đã merge",
    "review_score": "Điểm review đạt",
    "no_critical": "Không còn finding critical",
    "pipeline_ok": "Pipeline không lỗi",
    "tests_pass": "Testcase phủ đều Passed",
}
DEFAULT_MIN_SCORE = 7.0


def dod_settings(space: TaskSpace) -> dict[str, Any]:
    raw = dict((space.rules or {}).get("dod") or {})
    checks = [c for c in raw.get("checks") or [] if c in DOD_CHECKS]
    try:
        min_score = float(raw.get("min_score") or DEFAULT_MIN_SCORE)
    except (TypeError, ValueError):
        min_score = DEFAULT_MIN_SCORE
    return {"checks": checks, "min_score": min_score, "enforce": bool(raw.get("enforce"))}


def clean_dod(value: Any) -> dict[str, Any]:
    """A `rules.dod` value as sent by the settings screen; 422 on nonsense."""
    from fastapi import HTTPException

    if not isinstance(value, dict):
        raise HTTPException(422, "dod phải là {checks, min_score, enforce}")
    checks = value.get("checks") or []
    if not isinstance(checks, list) or any(c not in DOD_CHECKS for c in checks):
        raise HTTPException(422, f"mục DoD không biết; có: {', '.join(DOD_CHECKS)}")
    try:
        min_score = float(value.get("min_score") or DEFAULT_MIN_SCORE)
    except (TypeError, ValueError):
        raise HTTPException(422, "min_score phải là số") from None
    if not 0 <= min_score <= 10:
        raise HTTPException(422, "min_score từ 0 đến 10")
    return {
        "checks": list(dict.fromkeys(checks)),
        "min_score": min_score,
        "enforce": bool(value.get("enforce")),
    }


def dod(
    session: Session, task: Task, space: TaskSpace, git: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Each DoD check the dự án chose, ticked from real data (the task's code and tests);
    `git`: the task's git_detail when the caller has it already."""
    from app.tasks import service as ts

    conf = dod_settings(space)
    if not conf["checks"]:
        return []
    git = git if git is not None else ts.git_detail(session, task)
    summary = git["summary"]
    mrs = git["mrs"]
    cover = coverage(session, task.id)
    out = []
    for check in conf["checks"]:
        ok: bool
        detail = ""
        if check == "has_mr":
            ok = bool(mrs)
            detail = f"{len(mrs)} MR"
        elif check == "mr_merged":
            fixing = [m for m in mrs if m.get("relation") == "fixes"] or mrs
            ok = bool(fixing) and all(m.get("state") == "merged" for m in fixing)
            detail = (
                f"{sum(1 for m in fixing if m.get('state') == 'merged')}/{len(fixing)} đã merge"
            )
        elif check == "review_score":
            score = summary.get("avg_score")
            ok = score is not None and float(score) >= conf["min_score"]
            detail = f"điểm {score if score is not None else '—'} / cần ≥ {conf['min_score']:g}"
        elif check == "no_critical":
            n = int(summary.get("open_critical") or 0)
            ok, detail = n == 0, f"{n} critical mở"
        elif check == "pipeline_ok":
            n = int(summary.get("pipelines_failed") or 0)
            ok, detail = n == 0, f"{n} pipeline lỗi"
        else:  # tests_pass
            ok = cover["total"] > 0 and cover["passed"] == cover["total"]
            detail = f"{cover['passed']}/{cover['total']} Passed"
        out.append({"key": check, "label": DOD_CHECKS[check], "ok": ok, "detail": detail})
    return out


def dod_unmet(session: Session, task: Task, space: TaskSpace) -> list[str]:
    """The labels of the enforced DoD checks that fail (empty when not enforced)."""
    if not dod_settings(space)["enforce"]:
        return []
    return [c["label"] for c in dod(session, task, space) if not c["ok"]]


# -- release readiness (§19.4) ---------------------------------------------------------------


def readiness(session: Session, version: TaskVersion) -> dict[str, Any]:
    """Whether a version can ship: its tasks done, no urgent bug open, covering tests passing,
    linked MRs merged. Each check lists what holds it back."""
    from app.tasks import service as ts

    tasks = list(session.scalars(select(Task).where(Task.version_id == version.id)))
    cat = {
        int(s.id): str(s.category)
        for s in session.scalars(
            select(TaskStatus).where(TaskStatus.business_project_id == version.business_project_id)
        )
    }
    open_ = [t for t in tasks if cat.get(t.status_id) != "done" and t.type != "epic"]
    meta = ts.priority_meta(session, [version.business_project_id])
    urgent = [
        t
        for t in open_
        if t.type == "bug"
        and (meta.get((t.business_project_id, t.priority)) is not None)
        and meta[(t.business_project_id, t.priority)].level in ("highest", "high")
    ]
    cover = covering(session, [t.id for t in tasks])
    tests = latest_tests(session, sorted({c for cs in cover.values() for c in cs}))
    not_passed = sorted(
        {c for cs in cover.values() for c in cs if c not in tests or tests[c].status != "passed"}
    )
    links = list(
        session.scalars(
            select(TaskGitLink).where(
                TaskGitLink.task_id.in_([t.id for t in tasks] or [0]), TaskGitLink.kind == "mr"
            )
        )
    )
    states = {
        (int(pid or 0), int(iid)): str(state or "")
        for pid, iid, state in session.execute(
            select(MergeRequest.project_id, MergeRequest.iid, MergeRequest.state).where(
                tuple_(MergeRequest.project_id, MergeRequest.iid).in_(
                    [(lk.project_id, lk.mr_iid) for lk in links if lk.project_id and lk.mr_iid]
                    or [(0, 0)]
                )
            )
        ).all()
    }
    unmerged = []
    for lk in links:
        # The synced MR row wins over the link's own (possibly stale or empty) state; a closed
        # (abandoned) MR does not block.
        state = states.get((int(lk.project_id or 0), int(lk.mr_iid or 0))) or (lk.state or "")
        if state not in ("merged", "closed"):
            unmerged.append(f"!{lk.mr_iid}")
    checks = [
        {
            "key": "tasks_done",
            "label": "Mọi task đã xong",
            "ok": not open_,
            "items": [t.key for t in open_][:20],
            "count": len(open_),
        },
        {
            "key": "no_urgent_bugs",
            "label": "Không còn bug Cao / Cao nhất mở",
            "ok": not urgent,
            "items": [t.key for t in urgent][:20],
            "count": len(urgent),
        },
        {
            "key": "tests_pass",
            "label": "Testcase phủ task đều Passed",
            "ok": not not_passed,
            "items": [k for c in not_passed[:20] if (k := content.key("case", c))],
            "count": len(not_passed),
        },
        {
            "key": "mrs_merged",
            "label": "MR của task đã merge",
            "ok": not unmerged,
            "items": unmerged[:20],
            "count": len(unmerged),
        },
    ]
    return {"ready": all(c["ok"] for c in checks), "checks": checks, "tasks": len(tasks)}
