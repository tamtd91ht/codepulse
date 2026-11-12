"""CI results (docs/specs/30a G1): upload a report, the background job maps its tests to cases
(C<id>, automation_id, auto-create) and writes the run's results; re-upload updates; flaky;
"% tự động"; the MR's "Cần test lại"; permissions and refused files. The job is run inline
(`ci.process`) where the queue would run it."""

import json
import time
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import TestCase, TestResult, TestRun, TestRunTest, TestSection, TestSuite
from app.testcases import ci
from tests.api.conftest import FakeQueue, World, grant

API = "/api/v1"


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _suite(db: Session, bp: int, n: int, **kw: Any) -> tuple[TestSuite, list[TestCase]]:
    suite = TestSuite(business_project_id=bp, name="CI", position=1)
    db.add(suite)
    db.flush()
    cases = [
        TestCase(suite_id=suite.id, title=f"Case {i}", position=i, kind="automated", **kw)
        for i in range(n)
    ]
    db.add_all(cases)
    db.flush()
    return suite, cases


def _junit(tests: list[tuple[str, str, str]]) -> bytes:
    """(classname, name, status passed|failed|skipped) -> a JUnit report."""
    rows = []
    for klass, name, status in tests:
        inner = {
            "failed": '<failure message="boom">trace line</failure>',
            "skipped": "<skipped/>",
        }.get(status, "")
        rows.append(f'<testcase classname="{klass}" name="{name}" time="0.4">{inner}</testcase>')
    return f'<testsuites><testsuite name="s">{"".join(rows)}</testsuite></testsuites>'.encode()


def _upload(
    client: TestClient,
    h: dict[str, str],
    bp: int,
    data: bytes,
    name: str = "junit.xml",
    **form: Any,
) -> Any:
    fields = {"business_project_id": str(bp), **{k: str(v) for k, v in form.items()}}
    return client.post(
        f"{API}/testcases/runs/import-results",
        data=fields,
        files={"file": (name, data, "application/octet-stream")},
        headers=h,
    )


def _import(
    client: TestClient,
    db: Session,
    queue: FakeQueue,
    h: dict[str, str],
    bp: int,
    data: bytes,
    **f: Any,
) -> dict[str, Any]:
    got = _upload(client, h, bp, data, **f)
    assert got.status_code == 202, got.text
    import_id = got.json()["import_id"]
    assert queue.jobs[-1][0] == "app.testcases.ci.import_job" and queue.jobs[-1][1] == (import_id,)
    ci.process(db, import_id)
    out = client.get(f"{API}/testcases/ci-imports/{import_id}", headers=h)
    assert out.status_code == 200, out.text
    return dict(out.json())


def _results(db: Session, run_id: int) -> list[TestResult]:
    return list(
        db.scalars(
            select(TestResult)
            .join(TestRunTest, TestRunTest.id == TestResult.test_id)
            .where(TestRunTest.run_id == run_id)
            .order_by(TestResult.id)
        )
    )


def test_ac1_keys_map_and_unmapped_listed(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    suite, cases = _suite(db_session, bp, 45)
    tests = [
        ("pkg.T", f"C{c.id} case {i}", "failed" if i < 3 else "passed") for i, c in enumerate(cases)
    ]
    tests += [("pkg.T", f"free {i}", "passed") for i in range(5)]
    out = _import(
        client,
        db_session,
        queue,
        h,
        bp,
        _junit(tests),
        suite_id=suite.id,
        commit_sha="abcdef1234567",
        branch="main",
    )
    assert out["status"] == "done" and out["format"] == "junit" and out["total"] == 50
    assert out["mapped"] == 45 and out["created_cases"] == 0 and out["done"] == 50
    assert sorted(out["unmapped"]) == sorted(f"pkg.T.free {i}" for i in range(5))
    run = db_session.get(TestRun, out["run_id"])
    assert run is not None and run.source == "ci" and run.name == "CI main abcdef12"
    results = _results(db_session, run.id)
    assert len(results) == 45 and {r.source for r in results} == {"ci"}
    failed = [r for r in results if r.status == "failed"]
    assert len(failed) == 3 and failed[0].log == "boom" and failed[0].stack == "trace line"
    assert failed[0].ci["commit_sha"] == "abcdef1234567" and failed[0].ci["import_id"] == out["id"]
    assert failed[0].elapsed_s == 1

    # The run view: tests carry the latest status; a result shows its CI facts.
    listed = client.get(f"{API}/testcases/runs/{run.id}/tests", headers=h).json()
    assert listed["total"] == 45
    assert sum(1 for t in listed["items"] if t["status"] == "failed") == 3
    one = next(t for t in listed["items"] if t["status"] == "failed")
    detail = client.get(f"{API}/testcases/tests/{one['id']}", headers=h).json()
    res = detail["results"][0]
    assert res["source"] == "ci" and res["log"] == "boom" and res["ci"]["branch"] == "main"

    # The recent imports of the dự án.
    recent = client.get(
        f"{API}/testcases/ci-imports", params={"business_project_id": bp}, headers=h
    ).json()
    assert [r["id"] for r in recent] == [out["id"]] and recent[0]["run_key"] == f"R{run.id}"


def test_ac2_reupload_into_run_updates_without_duplicates(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    _, cases = _suite(db_session, bp, 3)
    first = [("k", f"C{c.id}", "failed") for c in cases]
    out = _import(client, db_session, queue, h, bp, _junit(first), commit_sha="c1")
    run_id = out["run_id"]
    again = [("k", f"C{cases[0].id}", "passed"), ("k", f"C{cases[1].id}", "passed")]
    out2 = _import(client, db_session, queue, h, bp, _junit(again), commit_sha="c1", run_id=run_id)
    assert out2["status"] == "done" and out2["run_id"] == run_id
    results = _results(db_session, run_id)
    assert len(results) == 3  # updated in place
    by_case = {
        db_session.get(TestRunTest, r.test_id).case_id: r  # type: ignore[union-attr]
        for r in results
    }
    assert by_case[cases[0].id].status == "passed" and by_case[cases[2].id].status == "failed"
    assert by_case[cases[0].id].ci["attempts"] == ["failed", "passed"]
    statuses = dict(
        db_session.execute(
            select(TestRunTest.case_id, TestRunTest.status).where(TestRunTest.run_id == run_id)
        ).all()
    )
    assert statuses == {cases[0].id: "passed", cases[1].id: "passed", cases[2].id: "failed"}
    # Passed and failed on one commit: flaky.
    db_session.expire_all()
    assert db_session.get(TestCase, cases[0].id).flaky is True  # type: ignore[union-attr]
    assert db_session.get(TestCase, cases[2].id).flaky is False  # type: ignore[union-attr]

    # Another commit into the same run: a new result (history), close the run.
    out3 = _import(
        client,
        db_session,
        queue,
        h,
        bp,
        _junit(again),
        commit_sha="c2",
        run_id=run_id,
        close_run="true",
    )
    assert out3["status"] == "done" and len(_results(db_session, run_id)) == 5
    db_session.expire_all()
    assert db_session.get(TestRun, run_id).completed_at is not None  # type: ignore[union-attr]
    # A closed run takes no more.
    assert _upload(client, h, bp, _junit(again), run_id=run_id).status_code == 409


def test_ac3_five_thousand_tests_with_auto_create(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    suite = TestSuite(business_project_id=bp, name="Big", position=1)
    db_session.add(suite)
    db_session.flush()
    rows = [
        ("com.acme.Big" + str(i // 100), f"test_{i}", "failed" if i % 50 == 0 else "passed")
        for i in range(5000)
    ]
    data = _junit(rows)
    started = time.monotonic()
    out = _import(client, db_session, queue, h, bp, data, suite_id=suite.id, auto_create="true")
    elapsed = time.monotonic() - started
    assert out["status"] == "done", out
    assert out["total"] == 5000 and out["created_cases"] == 5000 and out["mapped"] == 5000
    assert elapsed < 30, elapsed
    assert len(_results(db_session, out["run_id"])) == 5000
    section = db_session.scalar(
        select(TestSection).where(TestSection.suite_id == suite.id, TestSection.name == "Unmapped")
    )
    assert section is not None and section.parent_id is not None
    made = db_session.scalar(
        select(TestCase).where(
            TestCase.suite_id == suite.id, TestCase.automation_id == "com.acme.Big0.test_1"
        )
    )
    assert made is not None and made.kind == "automated" and made.status == "draft"
    assert made.source == "import" and made.section_id == section.id and made.title == "test_1"


def test_automation_id_mapping_and_editor(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    suite, (a, b) = _suite(db_session, bp, 2)
    got = client.patch(
        f"{API}/testcases/{a.id}",
        json={"revision": a.revision, "automation_id": "tests.login.test_ok"},
        headers=h,
    )
    assert got.status_code == 200, got.text
    assert got.json()["automation_id"] == "tests.login.test_ok" and got.json()["flaky"] is False
    taken = client.patch(
        f"{API}/testcases/{b.id}",
        json={"revision": b.revision, "automation_id": "tests.login.test_ok"},
        headers=h,
    )
    assert taken.status_code == 422 and "automation ID" in taken.json()["detail"]
    out = _import(
        client,
        db_session,
        queue,
        h,
        bp,
        _junit([("tests.login", "test_ok", "passed"), ("tests.login", "test_other", "passed")]),
        suite_id=suite.id,
    )
    assert out["mapped"] == 1 and out["unmapped"] == ["tests.login.test_other"]
    tests = client.get(f"{API}/testcases/runs/{out['run_id']}/tests", headers=h).json()["items"]
    assert [t["case_id"] for t in tests] == [a.id]


def test_flaky_flips_and_stats(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    _, (case,) = _suite(db_session, bp, 1)
    for i, status in enumerate(("passed", "failed", "passed")):
        _import(
            client,
            db_session,
            queue,
            h,
            bp,
            _junit([("x", f"C{case.id}", status)]),
            commit_sha=f"s{i}",
        )
    db_session.expire_all()
    assert db_session.get(TestCase, case.id).flaky is False  # type: ignore[union-attr]
    out = _import(
        client, db_session, queue, h, bp, _junit([("x", f"C{case.id}", "failed")]), commit_sha="s3"
    )
    db_session.expire_all()
    assert db_session.get(TestCase, case.id).flaky is True  # type: ignore[union-attr]
    tests = client.get(f"{API}/testcases/runs/{out['run_id']}/tests", headers=h).json()["items"]
    assert tests[0]["flaky"] is True
    assert client.get(f"{API}/testcases/{case.id}", headers=h).json()["flaky"] is True
    stats = client.get(f"{API}/testcases/stats", params={"business_project_id": bp}, headers=h)
    assert stats.json()["automated"] == 1 and stats.json()["automated_ci"] == 1


def test_ac4_mr_iid_shows_in_mr_impact(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    _, (case, other) = _suite(db_session, bp, 2)
    _import(
        client,
        db_session,
        queue,
        h,
        bp,
        _junit([("x", f"C{case.id}", "failed")]),
        mr_iid=world.mr.iid,
        commit_sha="deadbeefcafe",
        project="demo/p",
    )
    # Another repo's MR with the same iid does not count.
    _import(
        client,
        db_session,
        queue,
        h,
        bp,
        _junit([("x", f"C{other.id}", "failed")]),
        mr_iid=world.mr.iid,
        project="demo/other",
    )
    got = client.get(f"{API}/testcases/impact/mr/{world.mr.id}", headers=h)
    assert got.status_code == 200, got.text
    found = {c["case"]["id"]: c["reasons"] for c in got.json()["cases"]}
    assert other.id not in found
    assert any(r.startswith("CI: Failed (R") and "deadbeef" in r for r in found[case.id])


def test_playwright_and_cucumber_reports(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    _, (a, b) = _suite(db_session, bp, 2)
    pw = {
        "config": {},
        "suites": [
            {
                "title": "a.spec.ts",
                "file": "a.spec.ts",
                "specs": [
                    {
                        "title": f"works @C{a.id}",
                        "tests": [
                            {
                                "status": "unexpected",
                                "results": [
                                    {
                                        "status": "failed",
                                        "duration": 1200,
                                        "error": {"message": "expect failed", "stack": "at a"},
                                        "attachments": [{"name": "s", "path": "/r/s.png"}],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    out = _import(client, db_session, queue, h, bp, json.dumps(pw).encode(), name="r.json")
    assert out["format"] == "playwright" and out["mapped"] == 1
    [r] = _results(db_session, out["run_id"])
    assert r.status == "failed" and r.log == "expect failed" and r.stack == "at a"
    assert r.attachments[0]["path"] == "/r/s.png" and r.elapsed_s == 2
    cuc = [
        {
            "name": "F",
            "elements": [
                {
                    "type": "scenario",
                    "name": "S",
                    "tags": [{"name": f"@C{b.id}"}],
                    "steps": [{"result": {"status": "passed"}}],
                },
                {
                    "type": "scenario",
                    "name": "Skip",
                    "tags": [{"name": f"@C{a.id}"}],
                    "steps": [{"result": {"status": "skipped"}}],
                },
            ],
        }
    ]
    out = _import(client, db_session, queue, h, bp, json.dumps(cuc).encode(), name="c.json")
    assert out["format"] == "cucumber" and out["mapped"] == 2 and out["skipped"] == 1
    results = _results(db_session, out["run_id"])
    assert [x.status for x in results] == ["passed"]  # a skipped test writes no result
    statuses = dict(
        db_session.execute(
            select(TestRunTest.case_id, TestRunTest.status).where(
                TestRunTest.run_id == out["run_id"]
            )
        ).all()
    )
    assert statuses == {a.id: "untested", b.id: "passed"}


def test_permissions_and_refused_files(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    admin, manager, viewer = as_user(world.admin), as_user(world.manager), as_user(world.an)
    outsider = as_user(world.outsider)
    _, (case,) = _suite(db_session, bp, 1)
    report = _junit([("x", f"C{case.id}", "passed")])
    # A viewer may not make a run.
    assert _upload(client, viewer, bp, report).status_code == 403
    assert _upload(client, outsider, bp, report).status_code in (403, 404)
    # Auto-create needs a suite.
    assert _upload(client, manager, bp, report, auto_create="true").status_code == 422
    # Malicious or unknown files are refused before anything is queued.
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;">]><testsuites><testcase name="&lol2;"/></testsuites>'
    )
    refused = _upload(client, manager, bp, bomb)
    assert refused.status_code == 422 and "DOCTYPE" in refused.json()["detail"]
    assert _upload(client, manager, bp, b"hello").status_code == 422
    assert _upload(client, manager, bp, b"").status_code == 422
    assert queue.jobs == []
    # A run of the dự án: a viewer still may not record results into it.
    out = _import(client, db_session, queue, admin, bp, report)
    assert _upload(client, viewer, bp, report, run_id=out["run_id"]).status_code == 403
    assert _upload(client, manager, bp, report, run_id=10**9).status_code == 404
    # An import is read by who sees the dự án.
    assert client.get(f"{API}/testcases/ci-imports/{10**9}", headers=viewer).status_code == 404
    assert client.get(f"{API}/testcases/ci-imports/{out['id']}", headers=viewer).status_code == 200


def test_job_marks_failure(db_session: Session, world: World) -> None:
    from app.models import TestCiImport

    bp = _bp(world)
    imp = TestCiImport(business_project_id=bp, status="queued", data=b"<testsuites><broken")
    db_session.add(imp)
    db_session.flush()
    try:
        ci.process(db_session, imp.id)
    except ci.ReportError as exc:
        ci.fail(db_session, imp.id, str(exc))
    db_session.expire_all()
    got = db_session.get(TestCiImport, imp.id)
    assert got is not None and got.status == "failed" and "XML" in (got.error or "")
    assert got.data is None
    count = db_session.scalar(
        select(func.count()).select_from(TestRun).where(TestRun.source == "ci")
    )
    assert count == 0


def test_failed_ci_results_raise_result_failed_webhooks(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    from app.models import NotificationWebhook, WebhookDelivery
    from app.testcases import events

    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    _, (a, b) = _suite(db_session, bp, 2)
    hook = NotificationWebhook(
        name="ci", url="https://h.example.com/ci", events=["result.failed"], format="json"
    )
    db_session.add(hook)
    db_session.flush()
    saved = events.queue_provider
    events.queue_provider = lambda: queue
    try:
        report = _junit([("x", f"C{a.id}", "failed"), ("x", f"C{b.id}", "passed")])
        _import(client, db_session, queue, h, bp, report)
    finally:
        events.queue_provider = saved
    rows = list(
        db_session.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id))
    )
    assert [r.payload["case_key"] for r in rows] == [f"C{a.id}"]
    assert rows[0].payload["failed_count"] == 1
    assert any(f == events.JOB for f, _args, _kw in queue.jobs)


def test_many_failed_ci_results_raise_one_event(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    from app.models import NotificationWebhook, WebhookDelivery
    from app.testcases import events

    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    _, cases = _suite(db_session, bp, 25)
    hook = NotificationWebhook(
        name="ci", url="https://h.example.com/ci", events=["result.failed"], format="json"
    )
    db_session.add(hook)
    db_session.flush()
    saved = events.queue_provider
    events.queue_provider = lambda: queue
    try:
        report = _junit([("x", f"C{c.id}", "failed") for c in cases])
        out = _import(client, db_session, queue, h, bp, report)
    finally:
        events.queue_provider = saved
    (row,) = db_session.scalars(
        select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id)
    )
    assert row.payload["failed_count"] == 25 and row.payload["run_key"] == f"R{out['run_id']}"
    assert len(row.payload["tests"]) == events.FAILED_LISTED
    keys = {f"C{c.id}" for c in cases}
    assert {t["case_key"] for t in row.payload["tests"]} <= keys
    assert "test_key" not in row.payload  # several tests: listed, not at the top level
    assert sum(1 for f, _a, _k in queue.jobs if f == events.JOB) == 1


def _give(db: Session, user_id: int, items: dict[str, str]) -> None:
    from app.models import PermissionSet, PermissionSetItem, UserPermissionSet

    row = PermissionSet(name=f"ci {user_id} {'-'.join(sorted(items))}", builtin=False)
    db.add(row)
    db.flush()
    db.add_all(
        [PermissionSetItem(set_id=row.id, permission=k, scope=v) for k, v in items.items()]
        + [UserPermissionSet(user_id=user_id, set_id=row.id)]
    )
    db.flush()


def test_close_run_needs_manage(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    """A tests.run-only user records results into a run but may not close it."""
    bp = _bp(world)
    _give(db_session, world.binh.id, {"tests.run": "all"})
    _, (case,) = _suite(db_session, bp, 1)
    report = _junit([("x", f"C{case.id}", "passed")])
    out = _import(client, db_session, queue, as_user(world.admin), bp, report)
    runner = as_user(world.binh)
    refused = _upload(client, runner, bp, report, run_id=out["run_id"], close_run="true")
    assert refused.status_code == 403 and "quản lý" in refused.json()["detail"]
    ok = _upload(client, runner, bp, report, run_id=out["run_id"])
    assert ok.status_code == 202, ok.text


def test_one_import_at_a_time_per_run_and_stale_ones_expire(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.api.routers import test_ci
    from app.models import TestCiImport

    bp = _bp(world)
    h = as_user(world.admin)
    _, (case,) = _suite(db_session, bp, 1)
    report = _junit([("x", f"C{case.id}", "passed")])
    out = _import(client, db_session, queue, h, bp, report)
    run_id = out["run_id"]
    first = _upload(client, h, bp, report, run_id=run_id, commit_sha="c1")
    assert first.status_code == 202
    second = _upload(client, h, bp, report, run_id=run_id, commit_sha="c1")
    assert second.status_code == 409 and "chưa xong" in second.json()["detail"]
    # Another run is not blocked.
    assert _upload(client, h, bp, report).status_code == 202
    # The first one's job died: past 2 x the job timeout it is failed, the run takes uploads.
    imp = db_session.get(TestCiImport, first.json()["import_id"])
    assert imp is not None
    imp.created_at = datetime.now(UTC) - test_ci.STALE_AFTER - timedelta(minutes=1)
    db_session.commit()
    listed = client.get(
        f"{API}/testcases/ci-imports", params={"business_project_id": bp}, headers=h
    ).json()
    stale = next(r for r in listed if r["id"] == imp.id)
    assert stale["status"] == "failed" and "quá thời gian" in stale["error"]
    assert _upload(client, h, bp, report, run_id=run_id).status_code == 202
    # A job that runs after its import was marked failed does nothing.
    db_session.expire_all()
    assert ci.process(db_session, imp.id).status == "failed"


def test_run_closed_meanwhile_stops_the_job(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    from datetime import UTC, datetime

    from app.models import TestCiImport

    bp = _bp(world)
    h = as_user(world.admin)
    _, (case,) = _suite(db_session, bp, 1)
    report = _junit([("x", f"C{case.id}", "failed")])
    out = _import(client, db_session, queue, h, bp, report)
    got = _upload(client, h, bp, report, run_id=out["run_id"], commit_sha="c9")
    assert got.status_code == 202
    run = db_session.get(TestRun, out["run_id"])
    assert run is not None
    run.completed_at = datetime.now(UTC)
    db_session.commit()
    imp = db_session.get(TestCiImport, got.json()["import_id"])
    assert imp is not None
    imp.done, imp.total = 3, 10
    try:
        ci.lock_run(db_session, imp, run.id)
        raise AssertionError("a closed run must stop the job")
    except ci.ReportError as exc:
        assert "trong lúc nhập" in str(exc)
    db_session.rollback()


def test_obsolete_automation_id_is_not_mapped_nor_created_again(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    h = as_user(world.admin)
    suite, (old,) = _suite(db_session, bp, 1, status="obsolete", automation_id="t.gone")
    report = _junit([("t", "gone", "failed"), ("t", "new", "passed")])
    out = _import(client, db_session, queue, h, bp, report, suite_id=suite.id, auto_create="true")
    assert out["status"] == "done", out
    assert out["created_cases"] == 1 and out["mapped"] == 1
    assert out["unmapped"] == [f"t.gone (case lỗi thời C{old.id})"]
    count = db_session.scalar(
        select(func.count()).select_from(TestCase).where(TestCase.suite_id == suite.id)
    )
    assert count == 2
    # Uploading the same again: the case made the first time is found, not made twice.
    again = _import(client, db_session, queue, h, bp, report, suite_id=suite.id, auto_create="true")
    assert again["created_cases"] == 0 and again["mapped"] == 1


def _pw(projects: dict[str, list[str]], title: str) -> bytes:
    """A Playwright report: one spec, one test per project with these attempt statuses."""
    return json.dumps(
        {
            "suites": [
                {
                    "title": "a.spec.ts",
                    "file": "a.spec.ts",
                    "specs": [
                        {
                            "title": title,
                            "tests": [
                                {
                                    "projectName": name,
                                    "status": "flaky",
                                    "results": [{"status": s} for s in statuses],
                                }
                                for name, statuses in projects.items()
                            ],
                        }
                    ],
                }
            ]
        }
    ).encode()


def test_flaky_per_test_identity(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    h = as_user(world.admin)
    _, (a, b) = _suite(db_session, bp, 2)
    # chromium passes, webkit fails on one commit: a real failure, not flaky.
    report = _pw({"chromium": ["passed"], "webkit": ["failed"]}, f"works @C{a.id}")
    _import(client, db_session, queue, h, bp, report, name="r.json", commit_sha="k1")
    db_session.expire_all()
    assert db_session.get(TestCase, a.id).flaky is False  # type: ignore[union-attr]
    # The same test failed then passed on retry, on one commit: flaky.
    report = _pw({"chromium": ["failed", "passed"]}, f"works @C{b.id}")
    _import(client, db_session, queue, h, bp, report, name="r.json", commit_sha="k1")
    db_session.expire_all()
    assert db_session.get(TestCase, b.id).flaky is True  # type: ignore[union-attr]


def test_mr_without_repo_counts_only_with_a_single_repo(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    from app.models import Project

    bp = _bp(world)
    h = as_user(world.admin)
    _, (case,) = _suite(db_session, bp, 1)
    _import(
        client,
        db_session,
        queue,
        h,
        bp,
        _junit([("x", f"C{case.id}", "failed")]),
        mr_iid=world.mr.iid,
    )

    def found() -> bool:
        got = client.get(f"{API}/testcases/impact/mr/{world.mr.id}", headers=h)
        assert got.status_code == 200, got.text
        return case.id in {c["case"]["id"] for c in got.json()["cases"]}

    assert found()  # one repo: the iid can only be its MR
    db_session.add(
        Project(
            connection_id=world.project.connection_id,
            external_id=1002,
            path_with_namespace="demo/q",
            business_project_id=bp,
        )
    )
    db_session.commit()
    assert not found()  # two repos: which MR !7 is unknown


def test_cli_multipart_body_is_accepted(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    queue: FakeQueue,
    tmp_path: Any,
) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("codepulse_cli", "tools/codepulse_cli.py")
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    bp = _bp(world)
    _, (case,) = _suite(db_session, bp, 1)
    report = tmp_path / "junit.xml"
    report.write_bytes(_junit([("x", f"C{case.id}", "passed")]))
    body, ctype = cli.multipart(
        {"business_project_id": str(bp), "commit_sha": "abc", "close_run": "true"},
        "file",
        str(report),
    )
    got = client.post(
        f"{API}/testcases/runs/import-results",
        content=body,
        headers={**as_user(world.admin), "Content-Type": ctype},
    )
    assert got.status_code == 202, got.text
    ci.process(db_session, got.json()["import_id"])
    out = client.get(
        f"{API}/testcases/ci-imports/{got.json()['import_id']}", headers=as_user(world.admin)
    )
    assert out.json()["status"] == "done" and out.json()["close_run"] is True
    assert out.json()["file_name"] == "junit.xml" and out.json()["commit_sha"] == "abc"
