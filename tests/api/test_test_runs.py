"""Test runs the TestRail way (docs/specs/30 §13, API §14.2-14.4): milestones, configurations,
shared steps, runs chosen by a selection, results (steps, defects, elapsed, bulk, reassign), who
may record, closing with a snapshot, plans multiplied by configurations, a case's history and the
run's export. Cases are made with the ORM (the cases API is not under test here)."""

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import AuditLog, TestCase, TestField, TestRunTest, TestSection, TestSuite, User
from app.testcases import runs as rs
from tests.api.conftest import World, grant

API = "/api/v1"


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _tree(db: Session, bp: int) -> dict[str, Any]:
    """A suite: Auth (login, logout) > Social (google); Pay (card, obsolete); one at the root."""
    suite = TestSuite(business_project_id=bp, name="OMICX", position=1)
    db.add(suite)
    db.flush()
    auth = TestSection(suite_id=suite.id, name="Auth", position=1)
    pay = TestSection(suite_id=suite.id, name="Pay", position=2)
    db.add_all([auth, pay])
    db.flush()
    social = TestSection(suite_id=suite.id, parent_id=auth.id, name="Social", position=1)
    db.add(social)
    db.flush()

    def case(title: str, section: TestSection | None, **kw: Any) -> TestCase:
        c = TestCase(
            suite_id=suite.id,
            section_id=section.id if section else None,
            title=title,
            position=kw.pop("position", 1),
            **kw,
        )
        db.add(c)
        db.flush()
        return c

    cases = {
        "login": case(
            "Đăng nhập",
            auth,
            priority="high",
            type="smoke",
            tags=["smoke"],
            jira_keys=["OMX-1"],
            template="steps",
            steps_list=[
                {"content": "Mở trang", "expected": "Có form"},
                {"content": "Bấm vào", "expected": "Vào trang chủ"},
            ],
            estimate_s=300,
        ),
        "logout": case("Đăng xuất", auth, position=2),
        "google": case("Google", social, priority="low"),
        "card": case("Thẻ", pay, priority="critical", type="security"),
        "old": case("Cũ", pay, status="obsolete", position=2),
        "root": case("Gốc", None),
    }
    return {"suite": suite, "auth": auth, "social": social, "pay": pay, **cases}


def _run(client: TestClient, h: dict[str, str], bp: int, **body: Any) -> Any:
    got = client.post(f"{API}/testcases/runs", json={"business_project_id": bp, **body}, headers=h)
    assert got.status_code == 201, got.text
    return got.json()


def _tests(client: TestClient, h: dict[str, str], run_id: int, **params: Any) -> Any:
    got = client.get(f"{API}/testcases/runs/{run_id}/tests", params=params, headers=h)
    assert got.status_code == 200, got.text
    return got.json()


def test_setup_crud_and_permissions(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    t = _tree(db_session, bp)
    editor, viewer = as_user(world.manager), as_user(world.an)

    # Milestones: the viewer reads, cannot write.
    body = {"business_project_id": bp, "name": "Release 1.0", "due_on": "2026-12-01"}
    assert client.post(f"{API}/testcases/milestones", json=body, headers=viewer).status_code == 403
    m = client.post(f"{API}/testcases/milestones", json=body, headers=editor).json()
    assert m["key"] == f"M{m['id']}" and m["due_on"] == "2026-12-01" and m["runs"] == 0
    assert m["counts"]["total"] == 0 and m["can_edit"] is True
    done = client.patch(
        f"{API}/testcases/milestones/{m['id']}",
        json={"completed": True, "due_on": None},
        headers=editor,
    ).json()
    assert done["completed_at"] and done["due_on"] is None
    listed = client.get(
        f"{API}/testcases/milestones", params={"business_project_id": bp}, headers=viewer
    ).json()
    assert [x["id"] for x in listed] == [m["id"]] and listed[0]["can_edit"] is False
    assert client.delete(f"{API}/testcases/milestones/{m['id']}", headers=viewer).status_code == 403
    assert client.delete(f"{API}/testcases/milestones/{m['id']}", headers=editor).status_code == 204

    # Configurations.
    group = client.post(
        f"{API}/testcases/config-groups",
        json={"business_project_id": bp, "name": "Trình duyệt"},
        headers=editor,
    ).json()
    chrome = client.post(
        f"{API}/testcases/configs", json={"group_id": group["id"], "name": "Chrome"}, headers=editor
    ).json()
    assert (
        client.post(
            f"{API}/testcases/configs", json={"group_id": group["id"], "name": "X"}, headers=viewer
        ).status_code
        == 403
    )
    client.patch(
        f"{API}/testcases/configs/{chrome['id']}", json={"name": "Chrome 120"}, headers=editor
    )
    groups = client.get(
        f"{API}/testcases/configs", params={"business_project_id": bp}, headers=viewer
    ).json()
    assert groups == [
        {
            "id": group["id"],
            "name": "Trình duyệt",
            "configs": [{"id": chrome["id"], "name": "Chrome 120"}],
        }
    ]
    assert (
        client.delete(f"{API}/testcases/configs/{chrome['id']}", headers=editor).status_code == 204
    )
    assert (
        client.delete(f"{API}/testcases/config-groups/{group['id']}", headers=editor).status_code
        == 204
    )

    # Shared steps: used by a case, then not deletable.
    shared = client.post(
        f"{API}/testcases/shared-steps",
        json={
            "business_project_id": bp,
            "title": "Đăng nhập admin",
            "steps": [{"content": "Nhập admin", "expected": "OK"}],
        },
        headers=editor,
    )
    assert shared.status_code == 201, shared.text
    s = shared.json()
    assert s["key"] == f"SS{s['id']}" and s["used_by"] == 0
    t["logout"].template = "steps"
    t["logout"].steps_list = [{"shared": s["id"]}, {"content": "Bấm thoát", "expected": ""}]
    db_session.flush()
    rows = client.get(
        f"{API}/testcases/shared-steps", params={"business_project_id": bp}, headers=viewer
    ).json()
    assert rows[0]["used_by"] == 1 and rows[0]["can_edit"] is False
    edited = client.patch(
        f"{API}/testcases/shared-steps/{s['id']}",
        json={"steps": [{"content": "A", "expected": "B"}, {"content": "C"}]},
        headers=editor,
    ).json()
    assert len(edited["steps"]) == 2
    assert (
        client.delete(f"{API}/testcases/shared-steps/{s['id']}", headers=editor).status_code == 409
    )
    t["logout"].steps_list = []
    db_session.flush()
    assert (
        client.delete(f"{API}/testcases/shared-steps/{s['id']}", headers=editor).status_code == 204
    )

    # People: the dự án's people and the admin.
    people = client.get(
        f"{API}/testcases/people", params={"business_project_id": bp}, headers=viewer
    ).json()
    ids = {p["id"] for p in people}
    assert {world.admin.id, world.an.id, world.manager.id} <= ids
    # Outside the dự án: 404.
    assert (
        client.get(
            f"{API}/testcases/milestones", params={"business_project_id": 999_999}, headers=viewer
        ).status_code
        == 404
    )


def test_run_selection(client: TestClient, world: World, as_user: Any, db_session: Session) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    admin = as_user(world.admin)

    whole = _run(client, admin, bp, selection={"suite_id": t["suite"].id})
    assert whole["key"] == f"R{whole['id']}" and whole["name"].startswith("Test run ")
    assert whole["counts"]["total"] == 5 and whole["counts"]["untested"] == 5
    titles = [x["title"] for x in _tests(client, admin, whole["id"])["items"]]
    # Root first, then the tree in reading order; obsolete left out.
    assert titles == ["Gốc", "Đăng nhập", "Đăng xuất", "Google", "Thẻ"]

    folder = _run(client, admin, bp, selection={"section_ids": [t["auth"].id]})
    assert folder["counts"]["total"] == 3  # with Social below it
    filtered = _run(client, admin, bp, selection={"all": True, "priority": ["high", "critical"]})
    assert filtered["counts"]["total"] == 2
    typed = _run(client, admin, bp, selection={"all": True, "type": ["smoke"], "tags": ["smoke"]})
    assert typed["counts"]["total"] == 1
    ticked = _run(client, admin, bp, selection={"case_ids": [t["card"].id, t["old"].id]})
    assert ticked["counts"]["total"] == 1
    none = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"case_ids": [t["old"].id]}},
        headers=admin,
    )
    assert none.status_code == 422 and "không có testcase" in none.json()["detail"]
    empty = client.post(
        f"{API}/testcases/runs", json={"business_project_id": bp, "selection": {}}, headers=admin
    )
    assert empty.status_code == 422

    # Regression: a merged MR with the Jira key of "Đăng nhập".
    world.mr.merged_at = datetime.now(UTC) - timedelta(days=1)
    world.mr.jira_keys = ["OMX-1"]
    world.mr.target_branch = "main"
    db_session.flush()
    reg = _run(client, admin, bp, selection={"regression": {"days": 7}})
    assert reg["source"] == "regression" and reg["name"].startswith("Regression 7 ngày (")
    assert reg["counts"]["total"] == 1 and reg["source_meta"]["mrs"][0]["iid"] == 7

    # Add and remove tests on an open run.
    patched = client.patch(
        f"{API}/testcases/runs/{ticked['id']}",
        json={"add": {"section_ids": [t["social"].id]}, "name": "Đợt 1"},
        headers=admin,
    ).json()
    assert patched["counts"]["total"] == 2 and patched["name"] == "Đợt 1"
    items = _tests(client, admin, ticked["id"])["items"]
    gone = next(x for x in items if x["title"] == "Thẻ")
    patched = client.patch(
        f"{API}/testcases/runs/{ticked['id']}",
        json={"remove_test_ids": [gone["id"]]},
        headers=admin,
    ).json()
    assert patched["counts"]["total"] == 1
    listed = client.get(
        f"{API}/testcases/runs", params={"business_project_id": bp, "state": "open"}, headers=admin
    ).json()
    assert listed[0]["id"] == reg["id"] and len(listed) == 6


def test_results_permissions_close_and_history(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    admin, an, binh = as_user(world.admin), as_user(world.an), as_user(world.binh)
    run = _run(client, admin, bp, selection={"case_ids": [t["login"].id, t["logout"].id]})
    login, logout = _tests(client, admin, run["id"])["items"]
    assert login["key"] == f"T{login['id']}" and login["case_key"] == f"C{t['login'].id}"
    assert login["estimate_s"] == 300

    # A viewer who is not assigned may not record; assigned, they may.
    url = f"{API}/testcases/tests/{login['id']}/results"
    assert client.post(url, json={"status": "passed"}, headers=an).status_code == 403
    given = client.post(url, json={"assigned_to": world.an.id, "comment": "giao An"}, headers=admin)
    assert given.status_code == 201, given.text
    assert given.json()["assigned_to"]["id"] == world.an.id and given.json()["status"] == "untested"
    assert client.get(f"{API}/testcases/tests/{login['id']}", headers=an).json()["can_record"]
    assert client.post(url, json={"status": "passed"}, headers=binh).status_code == 403

    got = client.post(
        url,
        json={
            "status": "failed",
            "comment": "Lỗi bước 2",
            "version": "1.2.0",
            "elapsed": "1m 30s",
            "defects": ["omx-9", "rác", "OMX-9"],
            "step_results": [
                {"status": "passed", "actual": "OK"},
                {"status": "failed", "actual": "Trắng trang"},
            ],
        },
        headers=an,
    )
    assert got.status_code == 201, got.text
    detail = got.json()
    assert detail["status"] == "failed" and detail["defects"] == ["OMX-9"]
    assert detail["latest_comment"] == "Lỗi bước 2" and detail["tested_at"]
    first = detail["results"][0]
    assert first["elapsed_s"] == 90 and first["elapsed"] == "1m 30s"
    assert first["created_by"]["id"] == world.an.id and len(first["step_results"]) == 2
    assert detail["content"]["template"] == "steps" and len(detail["content"]["steps_list"]) == 2
    too_many = client.post(url, json={"step_results": [{"status": "passed"}] * 3}, headers=admin)
    assert too_many.status_code == 422
    text_case = client.post(
        f"{API}/testcases/tests/{logout['id']}/results",
        json={"step_results": [{"status": "passed"}]},
        headers=admin,
    )
    assert text_case.status_code == 422
    assert client.post(url, json={}, headers=admin).status_code == 422
    assert client.post(url, json={"elapsed": "abc"}, headers=admin).status_code == 422
    # A comment alone keeps the status.
    kept = client.post(url, json={"comment": "đang sửa"}, headers=admin).json()
    assert kept["status"] == "failed" and len(kept["results"]) == 3

    # Bulk: the editor marks both passed.
    bulk = client.post(
        f"{API}/testcases/runs/{run['id']}/results",
        json={"test_ids": [login["id"], logout["id"]], "status": "passed", "version": "1.2.1"},
        headers=admin,
    )
    assert bulk.json() == {"changed": 2}
    denied = client.post(
        f"{API}/testcases/runs/{run['id']}/results",
        json={"test_ids": [login["id"], logout["id"]], "status": "blocked"},
        headers=an,
    )
    assert denied.status_code == 403  # An is assigned to one of them only
    counts = client.get(f"{API}/testcases/runs/{run['id']}", headers=an).json()["counts"]
    assert counts["passed"] == 2 and counts["total"] == 2
    assert _tests(client, an, run["id"], status="passed")["total"] == 2
    assert _tests(client, an, run["id"], assigned_to=world.an.id)["total"] == 1
    assert _tests(client, an, run["id"], q="dang xuat")["total"] == 1

    # While open, the test shows the case as it is now.
    t["login"].title = "Đăng nhập (mới)"
    db_session.flush()
    assert client.get(f"{API}/testcases/tests/{login['id']}", headers=an).json()["title"] == (
        "Đăng nhập (mới)"
    )

    # The viewer cannot close; the editor closes: the content is kept, no more results.
    assert client.post(f"{API}/testcases/runs/{run['id']}/close", headers=an).status_code == 403
    closed = client.post(f"{API}/testcases/runs/{run['id']}/close", headers=admin)
    assert closed.status_code == 200 and closed.json()["completed_at"]
    t["login"].title = "Đổi sau khi đóng"
    db_session.flush()
    after = client.get(f"{API}/testcases/tests/{login['id']}", headers=an).json()
    assert after["title"] == "Đăng nhập (mới)" and after["can_record"] is False
    assert after["content"]["steps_list"][1]["content"] == "Bấm vào"
    assert client.post(url, json={"status": "passed"}, headers=admin).status_code == 409
    assert (
        client.patch(
            f"{API}/testcases/runs/{run['id']}", json={"name": "x"}, headers=admin
        ).status_code
        == 409
    )

    # History of the case.
    history = client.get(f"{API}/testcases/{t['login'].id}/results", headers=an).json()
    assert [h["result"]["status"] for h in history][:2] == ["passed", None]
    assert history[0]["run_key"] == run["key"] and history[0]["completed"] is True

    # A deleted case: the test keeps its snapshot.
    db_session.delete(t["logout"])
    db_session.flush()
    db_session.expire_all()  # the row set to NULL by the database
    db_session.flush()
    kept_test = client.get(f"{API}/testcases/tests/{logout['id']}", headers=an).json()
    assert kept_test["case_id"] is None and kept_test["title"] == "Đăng xuất"

    # Export opens.
    xlsx = client.get(f"{API}/testcases/runs/{run['id']}/export", headers=an)
    assert xlsx.status_code == 200
    book = load_workbook(io.BytesIO(xlsx.content))
    ws = book.active
    assert ws is not None
    values = [[c.value for c in row] for row in ws.iter_rows()]
    assert values[0][0] == run["name"]
    assert any(row[0] == login["key"] and row[6] == "Passed" for row in values)


def test_deleted_case_in_open_run_keeps_snapshot(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    admin = as_user(world.admin)
    run = _run(client, admin, bp, selection={"case_ids": [t["card"].id]})
    [test] = _tests(client, admin, run["id"])["items"]
    db_session.delete(t["card"])
    db_session.flush()
    db_session.expire_all()
    db_session.flush()
    shown = client.get(f"{API}/testcases/tests/{test['id']}", headers=admin).json()
    assert shown["title"] == "Thẻ" and shown["section"] == "Pay"
    assert shown["content"]["priority"] == "critical"


def test_plan_with_configurations(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    admin, an = as_user(world.admin), as_user(world.an)

    def group(name: str, *configs: str) -> list[int]:
        g = client.post(
            f"{API}/testcases/config-groups",
            json={"business_project_id": bp, "name": name},
            headers=admin,
        ).json()
        return [
            client.post(
                f"{API}/testcases/configs", json={"group_id": g["id"], "name": c}, headers=admin
            ).json()["id"]
            for c in configs
        ]

    chrome, safari, _firefox = group("Trình duyệt", "Chrome", "Safari", "Firefox")
    android, ios = group("Nền tảng", "Android", "iOS")
    milestone = client.post(
        f"{API}/testcases/milestones",
        json={"business_project_id": bp, "name": "Sprint 5"},
        headers=admin,
    ).json()
    made = client.post(
        f"{API}/testcases/plans",
        json={
            "business_project_id": bp,
            "name": "Release 2",
            "milestone_id": milestone["id"],
            "entries": [
                {
                    "name": "Auth",
                    "selection": {"section_ids": [t["auth"].id]},
                    "config_ids": [chrome, safari, android, ios],
                    "assigned_to": world.an.id,
                },
                {"selection": {"case_ids": [t["root"].id]}},
            ],
        },
        headers=admin,
    )
    assert made.status_code == 201, made.text
    plan = made.json()
    assert plan["key"] == f"P{plan['id']}" and len(plan["runs"]) == 5
    configs = sorted(r["config"] or "" for r in plan["runs"])
    assert configs == ["", "Chrome, Android", "Chrome, iOS", "Safari, Android", "Safari, iOS"]
    auth_run = next(r for r in plan["runs"] if r["config"] == "Chrome, iOS")
    assert auth_run["source"] == "plan" and auth_run["plan_name"] == "Release 2"
    assert auth_run["milestone_name"] == "Sprint 5" and auth_run["assigned_to"]["id"] == world.an.id
    assert plan["counts"]["total"] == 4 * 3 + 1

    # An assignee records on their run; the plan and the milestone add it up.
    [first, *_] = _tests(client, an, auth_run["id"])["items"]
    ok = client.post(
        f"{API}/testcases/tests/{first['id']}/results", json={"status": "passed"}, headers=an
    )
    assert ok.status_code == 201
    listed = client.get(
        f"{API}/testcases/plans", params={"business_project_id": bp}, headers=an
    ).json()
    assert listed[0]["runs"] == 5 and listed[0]["counts"]["passed"] == 1
    ms = client.get(
        f"{API}/testcases/milestones", params={"business_project_id": bp}, headers=an
    ).json()
    assert ms[0]["runs"] == 5 and ms[0]["counts"]["total"] == 13 and ms[0]["counts"]["passed"] == 1

    added = client.patch(
        f"{API}/testcases/plans/{plan['id']}",
        json={"add_entries": [{"selection": {"case_ids": [t["card"].id]}}]},
        headers=admin,
    ).json()
    assert len(added["runs"]) == 6
    too_many = client.patch(
        f"{API}/testcases/plans/{plan['id']}",
        json={
            "add_entries": [{"selection": {"case_ids": [t["card"].id]}, "config_ids": [chrome]}]
            * 45
        },
        headers=admin,
    )
    assert too_many.status_code == 422

    closed = client.post(f"{API}/testcases/plans/{plan['id']}/close", headers=admin).json()
    assert closed["completed_at"] and all(r["completed_at"] for r in closed["runs"])
    assert (
        client.post(
            f"{API}/testcases/tests/{first['id']}/results", json={"status": "failed"}, headers=an
        ).status_code
        == 409
    )
    assert client.delete(f"{API}/testcases/plans/{plan['id']}", headers=an).status_code == 403
    assert client.delete(f"{API}/testcases/plans/{plan['id']}", headers=admin).status_code == 204
    assert (
        client.get(
            f"{API}/testcases/runs", params={"business_project_id": bp}, headers=admin
        ).json()
        == []
    )


class _Deadlock(Exception):
    sqlstate = "40P01"


def test_result_locks_the_run_before_the_test_and_busy_is_409(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    admin = as_user(world.admin)
    run = _run(client, admin, bp, selection={"case_ids": [t["card"].id]})
    [test] = _tests(client, admin, run["id"])["items"]

    # Closing a run locks the run, then its tests: a result takes them in the same order.
    seen: list[str] = []

    def record(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        if "FOR UPDATE" in statement or "FOR SHARE" in statement:
            seen.append(statement)

    conn = db_session.connection()
    event.listen(conn, "before_cursor_execute", record)
    try:
        got = client.post(
            f"{API}/testcases/tests/{test['id']}/results", json={"status": "passed"}, headers=admin
        )
    finally:
        event.remove(conn, "before_cursor_execute", record)
    assert got.status_code == 201, got.text
    run_lock = next(i for i, s in enumerate(seen) if "FROM test_runs " in s and "FOR SHARE" in s)
    test_lock = next(i for i, s in enumerate(seen) if "FROM test_run_tests" in s)
    assert run_lock < test_lock and "FOR UPDATE" in seen[test_lock]

    # A deadlock or a lock wait that ran out answers "try again", not a 500.
    def stuck(*_: Any, **__: Any) -> None:
        raise OperationalError("UPDATE test_run_tests", {}, _Deadlock())

    monkeypatch.setattr("app.api.routers.test_runs.rs.add_result", stuck)
    busy = client.post(
        f"{API}/testcases/tests/{test['id']}/results", json={"status": "failed"}, headers=admin
    )
    assert busy.status_code == 409 and "thử lại" in busy.json()["detail"]


def test_removing_tested_tests_needs_force(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    admin = as_user(world.admin)
    run = _run(client, admin, bp, selection={"case_ids": [t["card"].id, t["root"].id]})
    a, b = _tests(client, admin, run["id"])["items"]
    client.post(
        f"{API}/testcases/tests/{a['id']}/results", json={"status": "passed"}, headers=admin
    )
    url = f"{API}/testcases/runs/{run['id']}"
    refused = client.patch(url, json={"remove_test_ids": [a["id"], b["id"]]}, headers=admin)
    assert refused.status_code == 409 and "1 test đã có kết quả" in refused.json()["detail"]
    assert _tests(client, admin, run["id"])["total"] == 2
    # A test without results goes without force.
    only_b = client.patch(url, json={"remove_test_ids": [b["id"]]}, headers=admin)
    assert only_b.status_code == 200 and only_b.json()["counts"]["total"] == 1
    forced = client.patch(url, json={"remove_test_ids": [a["id"]], "force": True}, headers=admin)
    assert forced.status_code == 200 and forced.json()["counts"]["total"] == 0
    logged = db_session.scalars(
        select(AuditLog.details).where(AuditLog.action == "testrun.tests").order_by(AuditLog.id)
    ).all()
    assert logged[-1] == {"removed": 1, "removed_ids": [a["id"]], "forced": True}
    assert logged[-2] == {"removed": 1, "removed_ids": [b["id"]]}


def test_assignees_must_belong_to_the_project(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    admin = as_user(world.admin)
    stranger = User(full_name="Người ngoài", primary_email="ngoai@x.vn")
    db_session.add(stranger)
    db_session.flush()
    sel = {"case_ids": [t["card"].id]}

    def bad(answer: Any) -> None:
        assert answer.status_code == 422, answer.text
        assert answer.json()["detail"] == "người này không thuộc dự án"

    bad(
        client.post(
            f"{API}/testcases/runs",
            json={"business_project_id": bp, "selection": sel, "assigned_to": stranger.id},
            headers=admin,
        )
    )
    run = _run(client, admin, bp, selection=sel, assigned_to=world.an.id)
    [test] = _tests(client, admin, run["id"])["items"]
    bad(
        client.patch(
            f"{API}/testcases/runs/{run['id']}", json={"assigned_to": stranger.id}, headers=admin
        )
    )
    bad(
        client.post(
            f"{API}/testcases/tests/{test['id']}/results",
            json={"assigned_to": stranger.id},
            headers=admin,
        )
    )
    bad(
        client.post(
            f"{API}/testcases/runs/{run['id']}/results",
            json={"test_ids": [test["id"]], "assigned_to": stranger.id},
            headers=admin,
        )
    )
    bad(
        client.post(
            f"{API}/testcases/plans",
            json={
                "business_project_id": bp,
                "name": "P",
                "entries": [{"selection": sel, "assigned_to": stranger.id}],
            },
            headers=admin,
        )
    )
    missing = client.post(
        f"{API}/testcases/tests/{test['id']}/results",
        json={"assigned_to": 999_999},
        headers=admin,
    )
    assert missing.status_code == 422
    # Unassigning stays possible.
    ok = client.post(
        f"{API}/testcases/tests/{test['id']}/results", json={"assigned_to": None}, headers=admin
    )
    assert ok.status_code == 201 and ok.json()["assigned_to"] is None


def test_plan_caps_tests_and_milestone_spares_closed_runs(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    admin = as_user(world.admin)
    g = client.post(
        f"{API}/testcases/config-groups",
        json={"business_project_id": bp, "name": "OS"},
        headers=admin,
    ).json()
    configs = [
        client.post(
            f"{API}/testcases/configs", json={"group_id": g["id"], "name": n}, headers=admin
        ).json()["id"]
        for n in ("Android", "iOS", "Web")
    ]
    # Positions stay within bounds.
    for bad in (-1, 1_000_001):
        assert (
            client.patch(
                f"{API}/testcases/configs/{configs[0]}", json={"position": bad}, headers=admin
            ).status_code
            == 422
        )
    assert (
        client.patch(
            f"{API}/testcases/config-groups/{g['id']}", json={"position": -5}, headers=admin
        ).status_code
        == 422
    )

    # 5 cases x 3 configurations = 15 tests: over a cap of 10, nothing is made.
    monkeypatch.setattr(rs, "PLAN_TESTS_MAX", 10)
    entry = {"selection": {"suite_id": t["suite"].id}, "config_ids": configs}
    over = client.post(
        f"{API}/testcases/plans",
        json={"business_project_id": bp, "name": "Lớn", "entries": [entry]},
        headers=admin,
    )
    assert over.status_code == 422 and "15 test" in over.json()["detail"]
    m1, m2 = (
        client.post(
            f"{API}/testcases/milestones",
            json={"business_project_id": bp, "name": n},
            headers=admin,
        ).json()["id"]
        for n in ("M1", "M2")
    )
    plan = client.post(
        f"{API}/testcases/plans",
        json={
            "business_project_id": bp,
            "name": "Nhỏ",
            "milestone_id": m1,
            "entries": [{"selection": {"case_ids": [t["card"].id]}, "config_ids": configs[:2]}],
        },
        headers=admin,
    ).json()
    assert len(plan["runs"]) == 2 and plan["counts"]["total"] == 2
    # Each run of the entry got the same content.
    snaps = db_session.scalars(
        select(TestRunTest.snapshot).where(TestRunTest.run_id.in_([r["id"] for r in plan["runs"]]))
    ).all()
    assert len(snaps) == 2 and snaps[0] == snaps[1] and snaps[0]["title"] == "Thẻ"
    over_add = client.patch(
        f"{API}/testcases/plans/{plan['id']}", json={"add_entries": [entry]}, headers=admin
    )
    assert over_add.status_code == 422

    closed_id = plan["runs"][0]["id"]
    assert client.post(f"{API}/testcases/runs/{closed_id}/close", headers=admin).status_code == 200
    moved = client.patch(
        f"{API}/testcases/plans/{plan['id']}", json={"milestone_id": m2}, headers=admin
    ).json()
    by_id = {r["id"]: r["milestone_id"] for r in moved["runs"]}
    assert by_id[closed_id] == m1 and by_id[plan["runs"][1]["id"]] == m2


def test_refresh_open_snapshots(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    admin = as_user(world.admin)
    sel = {"case_ids": [t["login"].id, t["card"].id]}
    closed = _run(client, admin, bp, selection=sel)
    client.post(f"{API}/testcases/runs/{closed['id']}/close", headers=admin)
    opened = _run(client, admin, bp, selection=sel)
    t["login"].title = "Đăng nhập mới"
    t["login"].priority = "low"
    t["card"].title = "Thẻ mới"
    db_session.flush()

    statements: list[str] = []
    conn = db_session.connection()

    def count(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        statements.append(statement)

    event.listen(conn, "before_cursor_execute", count)
    try:
        rs.refresh_open_snapshots(db_session, [t["login"].id, t["card"].id, 999_999])
    finally:
        event.remove(conn, "before_cursor_execute", count)
    assert len(statements) <= 6  # tests, cases, folders, shared steps, one batched update

    def shown_of(run_id: int) -> dict[int | None, tuple[str, str, str]]:
        rows = db_session.scalars(select(TestRunTest).where(TestRunTest.run_id == run_id))
        return {r.case_id: (r.title, r.priority, r.snapshot["title"]) for r in rows}

    open_now = shown_of(opened["id"])
    assert open_now[t["login"].id] == ("Đăng nhập mới", "low", "Đăng nhập mới")
    assert open_now[t["card"].id][0] == "Thẻ mới"
    assert shown_of(closed["id"])[t["login"].id] == ("Đăng nhập", "high", "Đăng nhập")
    rs.refresh_open_snapshots(db_session, [])  # nothing asked: nothing done

    # Then the case is deleted: the open run's test shows what it was just before.
    [login_test] = [
        x for x in _tests(client, admin, opened["id"])["items"] if x["case_id"] == t["login"].id
    ]
    db_session.delete(t["login"])
    db_session.flush()
    db_session.expire_all()
    shown = client.get(f"{API}/testcases/tests/{login_test['id']}", headers=admin).json()
    assert shown["case_id"] is None and shown["title"] == "Đăng nhập mới"
    assert shown["priority"] == "low" and len(shown["content"]["steps_list"]) == 2


def test_bulk_results_custom_fields_without_a_query_per_test(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """§15.8: the result fields of a bulk result are worked out with a fixed number of queries
    (the cases' suites in one), and not at all when the dự án has no live result field."""
    bp = _bp(world)
    admin = as_user(world.admin)
    _tree(db_session, bp)
    run = _run(client, admin, bp, name="R", selection={"all": True})
    ids = [t["id"] for t in _tests(client, admin, run["id"])["items"]]
    assert len(ids) >= 4
    url = f"{API}/testcases/runs/{run['id']}/results"
    conn = db_session.connection()

    def bulk(test_ids: list[int], **body: Any) -> list[str]:
        seen: list[str] = []

        def record(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
            seen.append(statement)

        event.listen(conn, "before_cursor_execute", record)
        try:
            got = client.post(
                url, json={"test_ids": test_ids, "status": "passed", **body}, headers=admin
            )
        finally:
            event.remove(conn, "before_cursor_execute", record)
        assert got.status_code == 200, got.text
        return seen

    def latest_custom(test_id: int) -> dict[str, Any]:
        detail = client.get(f"{API}/testcases/tests/{test_id}", headers=admin).json()
        return dict(detail["results"][0]["custom"])

    # No live result field (an archived one does not count): unknown keys dropped, no per-test
    # read of the case.
    archived = TestField(
        business_project_id=bp,
        entity="result",
        key="old",
        name="Old",
        type="text",
        archived_at=datetime.now(UTC),
    )
    db_session.add(archived)
    db_session.flush()
    plain = bulk(ids, custom={"old": "x", "nope": 1})
    assert not any("test_cases.suite_id" in s for s in plain)
    assert all(latest_custom(i) == {} for i in ids)

    # With a live one: its default is filled, and the queries do not grow with the tests.
    db_session.add(
        TestField(
            business_project_id=bp,
            entity="result",
            key="build",
            name="Build",
            type="text",
            default="1.0",
        )
    )
    db_session.flush()
    one = bulk(ids[:1])
    every = bulk(ids)
    assert latest_custom(ids[-1]) == {"build": "1.0"}

    def case_reads(seen: list[str]) -> int:
        return sum("FROM test_cases" in s for s in seen)

    assert case_reads(every) == case_reads(one)
