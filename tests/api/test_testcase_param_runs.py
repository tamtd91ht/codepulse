"""Parameterised cases in runs (docs/specs/30 §15.15, §15.16, 30a G13): one test per parameter
row with the row's values in its title and content (live while the run is open, kept when it
closes or the case is deleted), adding a case again adds only the rows missing, removing and
results per row, plans x configurations, baselines, and CI row mapping (C12[3], C12_3, plain C12).
The parameter table is set on the case directly (the cases API is not under test here)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TestBaselineCase, TestCase, TestCiImport, TestResult, TestRunTest, TestSuite
from app.testcases import ci
from app.testcases import runs as rs
from tests.api.conftest import FakeQueue, World, grant

API = "/api/v1"
TABLE = {"columns": ["phone", "otp"], "rows": [["0901", "111"], ["0902", "222"], ["0903", "333"]]}


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _cases(db: Session, bp: int) -> tuple[TestSuite, TestCase, TestCase]:
    suite = TestSuite(business_project_id=bp, name="Tham số", position=1)
    db.add(suite)
    db.flush()
    login = TestCase(
        suite_id=suite.id,
        title="Đăng nhập {{phone}}",
        template="steps",
        steps_list=[{"content": "Nhập {{phone}}", "expected": "Gửi OTP"}],
        expected="OTP {{otp}} hợp lệ",
        params=TABLE,
        position=1,
        kind="automated",
    )
    plain = TestCase(suite_id=suite.id, title="Đăng xuất", position=2, kind="automated")
    db.add_all([login, plain])
    db.flush()
    return suite, login, plain


def _run(client: TestClient, h: dict[str, str], bp: int, selection: dict[str, Any]) -> Any:
    got = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": selection},
        headers=h,
    )
    assert got.status_code == 201, got.text
    return got.json()


def _items(client: TestClient, h: dict[str, str], run_id: int) -> list[dict[str, Any]]:
    got = client.get(f"{API}/testcases/runs/{run_id}/tests", headers=h)
    assert got.status_code == 200, got.text
    return list(got.json()["items"])


def test_one_test_per_row_with_rendered_content(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    suite, login, plain = _cases(db_session, bp)
    h = as_user(world.manager)
    run = _run(client, h, bp, {"suite_id": suite.id})
    assert run["counts"]["total"] == 4
    items = _items(client, h, run["id"])
    assert [(x["case_id"], x["param_row"]) for x in items] == [
        (login.id, 1),
        (login.id, 2),
        (login.id, 3),
        (plain.id, None),
    ]
    assert items[1]["title"] == "Đăng nhập 0902 [phone=0902, otp=222]"
    assert items[1]["params"] == {"phone": "0902", "otp": "222"}
    assert items[3]["params"] == {} and items[3]["session"] == {}
    detail = client.get(f"{API}/testcases/tests/{items[2]['id']}", headers=h).json()
    assert detail["param_row"] == 3 and detail["content"]["expected"] == "OTP 333 hợp lệ"
    assert detail["content"]["steps_list"][0]["content"] == "Nhập 0903"
    assert detail["content"]["title"] == "Đăng nhập 0903 [phone=0903, otp=333]"

    # Open run: the case's later edits show, with each test's values.
    login.expected = "Mã {{otp}} đúng"
    db_session.flush()
    live = client.get(f"{API}/testcases/tests/{items[0]['id']}", headers=h).json()
    assert live["content"]["expected"] == "Mã 111 đúng"

    # Adding the suite again: nothing new; removing a row keeps the other rows.
    again = client.patch(
        f"{API}/testcases/runs/{run['id']}", json={"add": {"suite_id": suite.id}}, headers=h
    )
    assert again.status_code == 200 and again.json()["counts"]["total"] == 4
    client.patch(
        f"{API}/testcases/runs/{run['id']}", json={"remove_test_ids": [items[1]["id"]]}, headers=h
    )
    assert [x["param_row"] for x in _items(client, h, run["id"])] == [1, 3, None]
    # Adding the case again brings back only the missing row.
    back = client.patch(
        f"{API}/testcases/runs/{run['id']}", json={"add": {"case_ids": [login.id]}}, headers=h
    )
    assert back.json()["counts"]["total"] == 4
    assert sorted(x["param_row"] or 0 for x in _items(client, h, run["id"])) == [0, 1, 2, 3]

    # Results per row.
    items = _items(client, h, run["id"])
    rows = {x["param_row"]: x for x in items}
    ok = client.post(
        f"{API}/testcases/tests/{rows[1]['id']}/results", json={"status": "passed"}, headers=h
    )
    assert ok.status_code == 201, ok.text
    bad = client.post(
        f"{API}/testcases/tests/{rows[2]['id']}/results",
        json={"status": "failed", "step_results": [{"status": "failed", "actual": "lỗi"}]},
        headers=h,
    )
    assert bad.status_code == 201, bad.text
    statuses = {x["param_row"]: x["status"] for x in _items(client, h, run["id"])}
    assert statuses == {1: "passed", 2: "failed", 3: "untested", None: "untested"}
    counts = client.get(f"{API}/testcases/runs/{run['id']}", headers=h).json()["counts"]
    assert counts["passed"] == 1 and counts["failed"] == 1 and counts["total"] == 4

    # Removing every row of the case.
    gone = [x["id"] for x in items if x["case_id"] == login.id]
    removed = client.patch(
        f"{API}/testcases/runs/{run['id']}",
        json={"remove_test_ids": gone, "force": True},
        headers=h,
    )
    assert removed.status_code == 200 and removed.json()["counts"]["total"] == 1

    # Closing keeps each row's content; deleting the case keeps it too.
    client.patch(
        f"{API}/testcases/runs/{run['id']}", json={"add": {"case_ids": [login.id]}}, headers=h
    )
    closed = client.post(f"{API}/testcases/runs/{run['id']}/close", headers=h)
    assert closed.status_code == 200, closed.text
    login.expected = "Đổi sau khi đóng {{otp}}"
    db_session.flush()
    row3 = next(x for x in _items(client, h, run["id"]) if x["param_row"] == 3)
    kept = client.get(f"{API}/testcases/tests/{row3['id']}", headers=h).json()
    assert kept["content"]["expected"] == "Mã 333 đúng"
    assert kept["title"] == "Đăng nhập 0903 [phone=0903, otp=333]"


def test_deleted_case_keeps_rendered_rows_in_open_run(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    _suite, login, _plain = _cases(db_session, bp)
    h = as_user(world.manager)
    run = _run(client, h, bp, {"case_ids": [login.id]})
    assert client.delete(f"{API}/testcases/{login.id}", headers=h).status_code in (200, 204)
    items = _items(client, h, run["id"])
    assert [x["param_row"] for x in items] == [1, 2, 3] and items[0]["case_id"] is None
    shown = client.get(f"{API}/testcases/tests/{items[1]['id']}", headers=h).json()
    assert shown["content"]["expected"] == "OTP 222 hợp lệ"
    assert shown["title"] == "Đăng nhập 0902 [phone=0902, otp=222]"


def test_plan_with_configurations_and_params(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    suite, login, _plain = _cases(db_session, bp)
    h = as_user(world.manager)
    g = client.post(
        f"{API}/testcases/config-groups", json={"business_project_id": bp, "name": "OS"}, headers=h
    ).json()
    configs = [
        client.post(
            f"{API}/testcases/configs", json={"group_id": g["id"], "name": n}, headers=h
        ).json()["id"]
        for n in ("Android", "iOS")
    ]
    made = client.post(
        f"{API}/testcases/plans",
        json={
            "business_project_id": bp,
            "name": "Release",
            "entries": [{"selection": {"suite_id": suite.id}, "config_ids": configs}],
        },
        headers=h,
    )
    assert made.status_code == 201, made.text
    plan = made.json()
    assert len(plan["runs"]) == 2 and plan["counts"]["total"] == 2 * 4
    for r in plan["runs"]:
        rows = [x["param_row"] for x in _items(client, h, r["id"]) if x["case_id"] == login.id]
        assert rows == [1, 2, 3]


def test_baseline_keeps_params_and_runs_rows(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    suite, login, _plain = _cases(db_session, bp)
    h = as_user(world.manager)
    base = client.post(
        f"{API}/testcases/baselines", json={"suite_id": suite.id, "name": "1.0"}, headers=h
    ).json()
    login.params = {"columns": ["phone", "otp"], "rows": [["0999", "999"]]}
    login.expected = "Khác {{otp}}"
    db_session.flush()
    got = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "baseline_id": base["id"], "selection": {}},
        headers=h,
    )
    assert got.status_code == 201, got.text
    items = _items(client, h, got.json()["id"])
    rows = [x for x in items if x["case_id"] == login.id]
    assert [x["param_row"] for x in rows] == [1, 2, 3]
    shown = client.get(f"{API}/testcases/tests/{rows[0]['id']}", headers=h).json()
    assert shown["content"]["expected"] == "OTP 111 hợp lệ"
    assert shown["params"] == {"phone": "0901", "otp": "111"}


def _junit(tests: list[tuple[str, str]]) -> bytes:
    rows = []
    for name, status in tests:
        inner = '<failure message="boom">trace</failure>' if status == "failed" else ""
        rows.append(f'<testcase classname="k" name="{name}" time="0.2">{inner}</testcase>')
    return f'<testsuites><testsuite name="s">{"".join(rows)}</testsuite></testsuites>'.encode()


def test_ci_maps_rows(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    suite, login, plain = _cases(db_session, bp)
    h = as_user(world.manager)

    def upload(tests: list[tuple[str, str]], **form: Any) -> dict[str, Any]:
        got = client.post(
            f"{API}/testcases/runs/import-results",
            data={"business_project_id": str(bp), **{k: str(v) for k, v in form.items()}},
            files={"file": ("junit.xml", _junit(tests), "application/octet-stream")},
            headers=h,
        )
        assert got.status_code == 202, got.text
        ci.process(db_session, got.json()["import_id"])
        return dict(
            client.get(f"{API}/testcases/ci-imports/{got.json()['import_id']}", headers=h).json()
        )

    out = upload(
        [
            (f"C{login.id}[1] ok", "passed"),
            (f"test_C{login.id}_3_otp", "failed"),
            (f"C{login.id}[7] none", "passed"),
            (f"C{plain.id}[2] plain", "passed"),
            (f"C{plain.id} plain", "passed"),
        ],
        suite_id=suite.id,
        commit_sha="c1",
    )
    assert out["status"] == "done", out
    # A row named on a case without a parameter table (C{plain}[2]) maps to the case.
    assert out["mapped"] == 4
    assert out["unmapped"] == [f"k.C{login.id}[7] none (C{login.id}[7]: không có dòng tham số này)"]
    run_id = out["run_id"]
    statuses = {
        (t.case_id, t.param_row): t.status
        for t in db_session.scalars(select(TestRunTest).where(TestRunTest.run_id == run_id))
    }
    assert statuses == {
        (login.id, 1): "passed",
        (login.id, 2): "untested",
        (login.id, 3): "failed",
        (plain.id, None): "passed",
    }

    # A plain key gives every row the result (updated in place on the same commit).
    upload([(f"C{login.id} all rows", "passed")], run_id=run_id, commit_sha="c1")
    db_session.expire_all()
    rows = list(
        db_session.scalars(
            select(TestRunTest).where(TestRunTest.run_id == run_id, TestRunTest.case_id == login.id)
        )
    )
    assert {t.param_row: t.status for t in rows} == {1: "passed", 2: "passed", 3: "passed"}
    results = list(
        db_session.scalars(select(TestResult).where(TestResult.test_id.in_([t.id for t in rows])))
    )
    assert len(results) == 3  # one CI result per row and commit


def test_case_rows_parser() -> None:
    assert ci.case_rows("C12[3] and C12_4 x", "C7_y C8_2024 [C9]") == {12: [3, 4]}
    assert ci.case_keys("C12[3] test_C13_2") == [12, 13]


def test_tests_keep_columns_not_the_table(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """A test's content keeps the table's columns only (its values are its own `params`); the
    baseline keeps the whole table, once per case."""
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    suite, login, _plain = _cases(db_session, bp)
    h = as_user(world.manager)
    run = _run(client, h, bp, {"case_ids": [login.id]})
    first = _items(client, h, run["id"])[0]
    live = client.get(f"{API}/testcases/tests/{first['id']}", headers=h).json()
    assert live["content"]["params"] == {"columns": ["phone", "otp"]}
    assert client.post(f"{API}/testcases/runs/{run['id']}/close", headers=h).status_code == 200
    db_session.expire_all()
    snaps = [
        t.snapshot
        for t in db_session.scalars(select(TestRunTest).where(TestRunTest.run_id == run["id"]))
    ]
    assert len(snaps) == 3
    assert all(s["params"] == {"columns": ["phone", "otp"]} for s in snaps)

    base = client.post(
        f"{API}/testcases/baselines", json={"suite_id": suite.id, "name": "1.0"}, headers=h
    ).json()
    kept = db_session.scalar(
        select(TestBaselineCase.snapshot).where(
            TestBaselineCase.baseline_id == base["id"], TestBaselineCase.case_id == login.id
        )
    )
    assert kept is not None and kept["params"] == TABLE
    frozen = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "baseline_id": base["id"], "selection": {}},
        headers=h,
    )
    assert frozen.status_code == 201, frozen.text
    rows = db_session.scalars(
        select(TestRunTest).where(
            TestRunTest.run_id == frozen.json()["id"], TestRunTest.case_id == login.id
        )
    ).all()
    assert [t.param_row for t in rows] == [1, 2, 3]
    assert all(t.snapshot["params"] == {"columns": ["phone", "otp"]} for t in rows)
    assert rows[1].params == {"phone": "0902", "otp": "222"}


def test_run_cap_counts_rows_before_building_them(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    suite, login, plain = _cases(db_session, bp)
    h = as_user(world.manager)
    monkeypatch.setattr(rs, "RUN_TESTS_MAX", 3)
    too_many = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"suite_id": suite.id}},
        headers=h,
    )
    assert too_many.status_code == 422 and "tối đa 3 test" in too_many.text
    # Exactly the cap: the three rows.
    run = _run(client, h, bp, {"case_ids": [login.id]})
    assert run["counts"]["total"] == 3
    # Adding the case again counts only the rows it lacks (none): no 422.
    again = client.patch(
        f"{API}/testcases/runs/{run['id']}", json={"add": {"case_ids": [login.id]}}, headers=h
    )
    assert again.status_code == 200, again.text
    more = client.patch(
        f"{API}/testcases/runs/{run['id']}", json={"add": {"case_ids": [plain.id]}}, headers=h
    )
    assert more.status_code == 422


def test_ci_cap_counts_rows(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    queue: FakeQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    suite, login, _plain = _cases(db_session, bp)
    h = as_user(world.manager)
    monkeypatch.setattr(ci, "MAX_TESTS", 2)
    got = client.post(
        f"{API}/testcases/runs/import-results",
        data={"business_project_id": str(bp), "suite_id": str(suite.id)},
        files={"file": ("junit.xml", _junit([(f"C{login.id} all", "passed")]), "text/xml")},
        headers=h,
    )
    assert got.status_code == 202, got.text
    with pytest.raises(ci.ReportError, match="tối đa 2"):
        ci.process(db_session, got.json()["import_id"])
    imp = db_session.get(TestCiImport, got.json()["import_id"])
    assert imp is not None and imp.run_id is None


def test_case_added_without_row_is_not_added_again_as_rows(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """A run has C12 as one test (no table then); C12 gains a table: adding it again leaves
    that one test (no rows beside it, the case would show twice)."""
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    _suite, login, _plain = _cases(db_session, bp)
    login.params = {}
    db_session.flush()
    h = as_user(world.manager)
    run = _run(client, h, bp, {"case_ids": [login.id]})
    assert [x["param_row"] for x in _items(client, h, run["id"])] == [None]
    login.params = TABLE
    db_session.flush()
    again = client.patch(
        f"{API}/testcases/runs/{run['id']}", json={"add": {"case_ids": [login.id]}}, headers=h
    )
    assert again.status_code == 200, again.text
    assert again.json()["counts"]["total"] == 1
    assert [x["param_row"] for x in _items(client, h, run["id"])] == [None]
