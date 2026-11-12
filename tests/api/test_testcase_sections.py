"""Testcase on the TestRail model (docs/specs/30 §13, §14.1): the section tree, the steps template
with shared steps, estimates, milestones, bulk changes, TestRail CSV out and in."""

import io
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import BusinessProject, TestMilestone, TestSharedStep
from tests.api.conftest import World

API = "/api/v1"


def _suite(client: TestClient, headers: dict[str, str], bp: int, name: str = "Đăng nhập") -> Any:
    got = client.post(
        f"{API}/testcases/suites", json={"business_project_id": bp, "name": name}, headers=headers
    )
    assert got.status_code == 201, got.text
    return got.json()


def _section(
    client: TestClient, headers: dict[str, str], suite: int, name: str, parent: int | None = None
) -> Any:
    got = client.post(
        f"{API}/testcases/sections",
        json={"suite_id": suite, "parent_id": parent, "name": name},
        headers=headers,
    )
    assert got.status_code == 201, got.text
    return got.json()


def _case(client: TestClient, headers: dict[str, str], suite: int, **values: Any) -> Any:
    got = client.post(
        f"{API}/testcases",
        json={"suite_id": suite, "title": "Đăng nhập", **values},
        headers=headers,
    )
    assert got.status_code == 201, got.text
    return got.json()


def _list(client: TestClient, headers: dict[str, str], bp: int, **params: Any) -> list[int]:
    got = client.get(
        f"{API}/testcases", params={"business_project_id": bp, **params}, headers=headers
    )
    assert got.status_code == 200, got.text
    return [c["id"] for c in got.json()["items"]]


def test_section_tree_move_and_delete(client: TestClient, world: World, as_user: Any) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin, an = as_user(world.admin), as_user(world.an)
    suite = _suite(client, admin, bp)
    other = _suite(client, admin, bp, "Khác")
    web = _section(client, admin, suite["id"], "Web")
    login = _section(client, admin, suite["id"], "Đăng nhập", web["id"])
    form = _section(client, admin, suite["id"], "Form", login["id"])
    mobile = _section(client, admin, suite["id"], "Mobile")
    assert form["path"] == "Web > Đăng nhập > Form" and form["depth"] == 2
    assert (
        client.post(
            f"{API}/testcases/sections",
            json={"suite_id": suite["id"], "name": "x"},
            headers=an,
        ).status_code
        == 403
    )
    # A parent of another suite is refused.
    elsewhere = _section(client, admin, other["id"], "Ngoài")
    refused = client.post(
        f"{API}/testcases/sections",
        json={"suite_id": suite["id"], "parent_id": elsewhere["id"], "name": "x"},
        headers=admin,
    )
    assert refused.status_code == 422

    a = _case(client, admin, suite["id"], section_id=web["id"])
    b = _case(client, admin, suite["id"], section_id=form["id"], title="Sai mật khẩu")
    gone = _case(client, admin, suite["id"], section_id=form["id"], status="obsolete")
    root = _case(client, admin, suite["id"], title="Ở gốc")
    assert b["section"] == "Web > Đăng nhập > Form" and root["section"] is None
    bad = client.post(
        f"{API}/testcases",
        json={"suite_id": suite["id"], "title": "x", "section_id": elsewhere["id"]},
        headers=admin,
    )
    assert bad.status_code == 422

    tree = client.get(
        f"{API}/testcases/sections", params={"suite_id": suite["id"]}, headers=an
    ).json()
    assert [(s["name"], s["depth"], s["cases"], s["total"]) for s in tree] == [
        ("Web", 0, 1, 2),
        ("Đăng nhập", 1, 0, 1),
        ("Form", 2, 1, 1),  # the obsolete one is not counted
        ("Mobile", 0, 0, 0),
    ]
    # The filter takes the sections below.
    assert set(_list(client, an, bp, section_id=web["id"])) == {a["id"], b["id"]}
    assert _list(client, an, bp, section_id=form["id"]) == [b["id"]]

    # Never into itself or below itself.
    for target in (web["id"], form["id"]):
        cycle = client.patch(
            f"{API}/testcases/sections/{web['id']}", json={"parent_id": target}, headers=admin
        )
        assert cycle.status_code == 422, cycle.text
    moved = client.patch(
        f"{API}/testcases/sections/{login['id']}",
        json={"parent_id": mobile["id"], "name": "Login"},
        headers=admin,
    )
    assert moved.status_code == 200 and moved.json()["path"] == "Mobile > Login"
    got = client.get(f"{API}/testcases/{b['id']}", headers=admin).json()
    assert got["section"] == "Mobile > Login > Form"
    to_root = client.patch(
        f"{API}/testcases/sections/{login['id']}",
        json={"parent_id": None, "position": 0},
        headers=admin,
    ).json()
    assert to_root["parent_id"] is None and to_root["depth"] == 0 and to_root["position"] == 0

    deleted = client.delete(f"{API}/testcases/sections/{login['id']}", headers=admin)
    assert deleted.status_code == 200 and deleted.json() == {"sections": 2, "cases": 2}
    assert client.get(f"{API}/testcases/{b['id']}", headers=admin).status_code == 404
    assert client.get(f"{API}/testcases/{gone['id']}", headers=admin).status_code == 404
    names = [
        s["name"]
        for s in client.get(
            f"{API}/testcases/sections", params={"suite_id": suite["id"]}, headers=admin
        ).json()
    ]
    assert names == ["Web", "Mobile"]


def test_steps_template_shared_steps_estimate_and_bulk(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    elsewhere = BusinessProject(name="Dự án khác")
    db_session.add(elsewhere)
    db_session.flush()
    login = TestSharedStep(
        business_project_id=bp,
        title="Đăng nhập quản trị",
        steps=[{"content": "Mở /login", "expected": ""}, {"content": "Nhập", "expected": "Vào"}],
    )
    foreign = TestSharedStep(business_project_id=elsewhere.id, title="Khác", steps=[])
    sprint = TestMilestone(business_project_id=bp, name="Sprint 1")
    foreign_ms = TestMilestone(business_project_id=elsewhere.id, name="Khác")
    db_session.add_all([login, foreign, sprint, foreign_ms])
    db_session.flush()

    case = _case(
        client,
        admin,
        suite["id"],
        template="steps",
        steps_list=[{"shared": login.id}, {"content": "Bấm Đơn hàng", "expected": ""}],
        estimate="1h 30m",
        type="smoke",
        milestone_id=sprint.id,
    )
    assert case["estimate_s"] == 5400 and case["estimate"] == "1h 30m"
    assert case["milestone_id"] == sprint.id and case["type"] == "smoke"
    assert [s["content"] for s in case["steps_view"]] == ["Mở /login", "Nhập", "Bấm Đơn hàng"]
    assert case["steps_view"][0]["shared_id"] == login.id
    assert case["steps_view"][0]["shared_title"] == "Đăng nhập quản trị"
    assert case["problems"] == []
    empty = _case(client, admin, suite["id"], template="steps", title="Trống")
    assert empty["problems"] == ["thiếu các bước"]
    no_result = _case(
        client,
        admin,
        suite["id"],
        template="steps",
        title="Không kết quả",
        steps_list=[{"content": "Bấm", "expected": ""}],
    )
    assert no_result["problems"] == ["thiếu kết quả mong đợi"]
    flagged = _list(client, admin, bp, suite_id=suite["id"], problem=True)
    assert set(flagged) == {empty["id"], no_result["id"]}

    for values in (
        {"steps_list": [{"shared": foreign.id}]},  # a shared step of another dự án
        {"milestone_id": foreign_ms.id},
        {"estimate": "mai"},
        {"type": "nope"},
        {"steps_list": [{"content": "x" * 5001}]},
    ):
        refused = client.post(
            f"{API}/testcases",
            json={"suite_id": suite["id"], "title": "x", "template": "steps", **values},
            headers=admin,
        )
        assert refused.status_code == 422, (values, refused.text)

    edited = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": 1, "estimate": "", "milestone_id": None},
        headers=admin,
    ).json()
    assert edited["estimate_s"] is None and edited["milestone_id"] is None
    restored = client.post(f"{API}/testcases/{case['id']}/restore/1", headers=admin).json()
    assert restored["estimate_s"] == 5400 and restored["milestone_id"] == sprint.id
    assert restored["steps_list"][0] == {"shared": login.id}

    folder = _section(client, admin, suite["id"], "Đơn hàng")
    ids = [case["id"], empty["id"]]
    for body in (
        {"action": "section", "section_id": folder["id"]},
        {"action": "type", "type": "regression"},
        {"action": "milestone", "milestone_id": sprint.id},
    ):
        got = client.post(f"{API}/testcases/bulk", json={"ids": ids, **body}, headers=admin)
        assert got.status_code == 200 and got.json()["changed"] >= 1, got.text
    assert set(_list(client, admin, bp, section_id=folder["id"])) == set(ids)
    assert set(_list(client, admin, bp, type="regression")) == set(ids)
    assert set(_list(client, admin, bp, milestone_id=sprint.id)) == set(ids)
    wrong = client.post(
        f"{API}/testcases/bulk",
        json={"ids": ids, "action": "milestone", "milestone_id": foreign_ms.id},
        headers=admin,
    )
    assert wrong.status_code == 422
    back = client.post(
        f"{API}/testcases/bulk",
        json={"ids": ids, "action": "section", "section_id": None},
        headers=admin,
    )
    assert back.json() == {"changed": 2}
    assert _list(client, admin, bp, section_id=folder["id"]) == []


def test_testrail_export_and_import_again(client: TestClient, world: World, as_user: Any) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    web = _section(client, admin, suite["id"], "Web")
    form = _section(client, admin, suite["id"], "Form", web["id"])
    steps = [
        {"content": "Mở trang", "expected": "Hiện form"},
        {"content": "Nhập", "expected": ""},
        {"content": "Bấm", "expected": "Vào"},
    ]
    case = _case(
        client,
        admin,
        suite["id"],
        section_id=form["id"],
        template="steps",
        steps_list=steps,
        estimate="5m",
        jira_keys=["OMX-1"],
        priority="critical",
    )
    text = _case(client, admin, suite["id"], title="Đăng xuất", steps="Bấm", expected="Ra")
    out = client.get(
        f"{API}/testcases/export",
        params={"business_project_id": bp, "suite_id": suite["id"], "format": "testrail"},
        headers=admin,
    )
    assert out.status_code == 200 and out.headers["content-type"].startswith("text/csv")
    lines = out.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("ID,Title,Section,Template,Type,Priority,Estimate,References")
    assert len(lines) == 1 + 3 + 1  # header, three step rows, the text case
    assert lines[1].startswith(f"C{case['id']},Đăng nhập,Web > Form,Test Case (Steps)")

    # Edited in the file (title, a step), then imported again: the same cases (by C<id>).
    data = out.content.decode("utf-8-sig").replace("Đăng xuất", "Đăng xuất hẳn")
    data = data.replace("Nhập,", "Nhập email,")
    read = client.post(
        f"{API}/testcases/import/read-file",
        params={"business_project_id": bp, "filename": "testrail.csv"},
        content=data.encode("utf-8"),
        headers=admin,
    )
    assert read.status_code == 200, read.text
    [sheet] = read.json()["sheets"]
    assert {"case_id", "step_content", "step_expected", "template"} <= set(sheet["mapping"])
    done = client.post(
        f"{API}/testcases/import",
        json={
            "business_project_id": bp,
            "sheets": [
                {
                    "name": sheet["name"],
                    "suite_id": suite["id"],
                    "header_row": sheet["header_row"],
                    "mapping": sheet["mapping"],
                    "rows": sheet["rows"],
                }
            ],
        },
        headers=admin,
    )
    assert done.status_code == 200, done.text
    [result] = done.json()["suites"]
    assert (result["created"], result["updated"], result["unchanged"]) == (0, 2, 0)
    got = client.get(f"{API}/testcases/{case['id']}", headers=admin).json()
    assert got["steps_list"][1] == {"content": "Nhập email", "expected": ""}
    assert got["section_id"] == form["id"] and got["estimate_s"] == 300
    assert got["priority"] == "critical" and got["jira_keys"] == ["OMX-1"]
    assert client.get(f"{API}/testcases/{text['id']}", headers=admin).json()["title"] == (
        "Đăng xuất hẳn"
    )


def test_import_makes_section_paths_and_templates(
    client: TestClient, world: World, as_user: Any
) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin = as_user(world.admin)
    rows = [
        ["Section", "Title", "Steps", "Expected Result", "Type", "Estimate"],
        ["Auth > Web", "Đăng nhập", "1. Mở\n2. Bấm", "1. Hiện form\n2. Vào", "Smoke", "2m"],
        ["Auth > Web > Lỗi", "Sai mật khẩu", "Nhập sai", "Báo lỗi", "", ""],
        ["", "Ở gốc", "Bấm", "Ra", "", ""],
    ]
    done = client.post(
        f"{API}/testcases/import",
        json={
            "business_project_id": bp,
            "sheets": [
                {
                    "name": "Auth",
                    "header_row": 0,
                    "mapping": {
                        "section": 0,
                        "title": 1,
                        "steps": 2,
                        "expected": 3,
                        "type": 4,
                        "estimate": 5,
                    },
                    "rows": rows,
                }
            ],
        },
        headers=admin,
    )
    assert done.status_code == 200, done.text
    suite_id = done.json()["suites"][0]["suite_id"]
    tree = client.get(
        f"{API}/testcases/sections", params={"suite_id": suite_id}, headers=admin
    ).json()
    assert [s["path"] for s in tree] == ["Auth", "Auth > Web", "Auth > Web > Lỗi"]
    cases = client.get(
        f"{API}/testcases", params={"business_project_id": bp, "suite_id": suite_id}, headers=admin
    ).json()["items"]
    first = next(c for c in cases if c["title"] == "Đăng nhập")
    assert first["template"] == "steps" and first["section"] == "Auth > Web"
    assert first["steps_list"][1] == {"content": "Bấm", "expected": "Vào"}
    assert first["type"] == "smoke" and first["estimate"] == "2m"
    assert first["code"] is None and first["key"] == f"C{first['id']}"
    second = next(c for c in cases if c["title"] == "Sai mật khẩu")
    assert second["template"] == "text" and second["section"] == "Auth > Web > Lỗi"


def test_old_sheet_code_finds_the_case_by_section_and_title(
    client: TestClient, world: World, as_user: Any
) -> None:
    """A "TC-0001" of an export made before §13 (the code dropped since) is not a duplicate."""
    bp = world.project.business_project_id
    assert bp is not None
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    web = _section(client, admin, suite["id"], "Web")
    case = _case(client, admin, suite["id"], section_id=web["id"], expected="Vào")
    assert case["code"] is None
    rows = [
        ["Mã", "Chức năng", "Tiêu đề", "Kết quả mong đợi"],
        ["TC-0001", "Web", "Đăng nhập", "OK"],
    ]
    body = {
        "business_project_id": bp,
        "sheets": [
            {
                "name": "Auth",
                "suite_id": suite["id"],
                "header_row": 0,
                "mapping": {"code": 0, "section": 1, "title": 2, "expected": 3},
                "rows": rows,
            }
        ],
    }
    done = client.post(f"{API}/testcases/import", json=body, headers=admin)
    assert done.status_code == 200, done.text
    [result] = done.json()["suites"]
    assert (result["created"], result["updated"]) == (0, 1)
    got = client.get(f"{API}/testcases/{case['id']}", headers=admin).json()
    assert got["code"] == "TC-0001" and got["expected"] == "OK"
    assert _list(client, admin, bp, suite_id=suite["id"]) == [case["id"]]
    # Again: now found by its code.
    again = client.post(f"{API}/testcases/import", json=body, headers=admin).json()["suites"][0]
    assert (again["created"], again["unchanged"]) == (0, 1)


def test_unchanged_references_stay_after_unsharing(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    fe = BusinessProject(name="OMICX FE")
    db_session.add(fe)
    db_session.flush()
    shared_with = client.put(
        f"{API}/testcases/suites/{suite['id']}/projects",
        json={"project_ids": [fe.id]},
        headers=admin,
    )
    assert shared_with.status_code == 200, shared_with.text
    step = TestSharedStep(
        business_project_id=fe.id, title="Mở app", steps=[{"content": "Mở", "expected": ""}]
    )
    sprint, later = (
        TestMilestone(business_project_id=fe.id, name="FE 1"),
        TestMilestone(business_project_id=fe.id, name="FE 2"),
    )
    db_session.add_all([step, sprint, later])
    db_session.flush()
    case = _case(
        client,
        admin,
        suite["id"],
        template="steps",
        steps_list=[{"shared": step.id}],
        milestone_id=sprint.id,
    )
    unshared = client.put(
        f"{API}/testcases/suites/{suite['id']}/projects", json={"project_ids": []}, headers=admin
    )
    assert unshared.status_code == 200, unshared.text
    kept = client.patch(
        f"{API}/testcases/{case['id']}",
        json={
            "revision": 1,
            "title": "Đăng nhập lại",
            "milestone_id": sprint.id,
            "steps_list": [{"shared": step.id}, {"content": "Bấm", "expected": "Vào"}],
        },
        headers=admin,
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["steps_list"][0] == {"shared": step.id}
    assert kept.json()["milestone_id"] == sprint.id
    # A new reference to that dự án is refused.
    refused = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": 2, "milestone_id": later.id},
        headers=admin,
    )
    assert refused.status_code == 422


def test_deletes_refresh_open_runs_and_positions_are_bounded(
    client: TestClient, world: World, as_user: Any, monkeypatch: Any
) -> None:
    from app.api.routers import testcases as router
    from app.testcases import runs

    calls: list[list[int]] = []

    def record(_session: Session, case_ids: list[int]) -> None:
        calls.append(sorted(case_ids))

    monkeypatch.setattr(router, "refresh_open_snapshots", record)
    monkeypatch.setattr(runs, "refresh_open_snapshots", record)
    bp = world.project.business_project_id
    assert bp is not None
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    web = _section(client, admin, suite["id"], "Web")
    form = _section(client, admin, suite["id"], "Form", web["id"])
    one, two, three, four = (
        _case(client, admin, suite["id"], title=t)["id"] for t in ("Một", "Hai", "Ba", "Bốn")
    )
    in_web = _case(client, admin, suite["id"], section_id=web["id"])["id"]
    in_form = _case(client, admin, suite["id"], section_id=form["id"])["id"]
    assert client.delete(f"{API}/testcases/{one}", headers=admin).status_code == 204
    bulk = client.post(
        f"{API}/testcases/bulk", json={"ids": [two, three], "action": "delete"}, headers=admin
    )
    assert bulk.status_code == 200, bulk.text
    assert client.delete(f"{API}/testcases/sections/{web['id']}", headers=admin).json() == {
        "sections": 2,
        "cases": 2,
    }
    assert calls == [[one], sorted([two, three]), sorted([in_web, in_form])]
    assert client.get(f"{API}/testcases/{four}", headers=admin).status_code == 200

    mobile = _section(client, admin, suite["id"], "Mobile")
    for position in (-1, 1_000_001):
        bad = client.patch(
            f"{API}/testcases/sections/{mobile['id']}", json={"position": position}, headers=admin
        )
        assert bad.status_code == 422
        bad = client.patch(
            f"{API}/testcases/suites/{suite['id']}", json={"position": position}, headers=admin
        )
        assert bad.status_code == 422


def test_import_template_files(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    xlsx = client.get(f"{API}/testcases/import/template", headers=admin)
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    from openpyxl import load_workbook

    head = [c.value for c in load_workbook(io.BytesIO(xlsx.content)).worksheets[0][1]]
    assert head[:4] == ["ID", "Mã gốc", "Thư mục", "Tiêu đề"]
    trail = client.get(
        f"{API}/testcases/import/template", params={"format": "testrail"}, headers=admin
    )
    assert trail.content.decode("utf-8-sig").startswith("ID,Title,Section,Template")
