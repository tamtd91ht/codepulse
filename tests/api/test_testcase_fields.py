"""Custom fields (docs/specs/30 §15.8, §15.11, G5) and the delete / approve checks of the case
router (§15.9, G7): field CRUD, archive and restore, the checks of each type, required fields
(blocking the editor, not an import), defaults, the list filter, revisions, result fields,
export / import columns, isolation between dự án."""

import io
from typing import Any

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BusinessProject,
    PermissionSet,
    TestProjectSettings,
    User,
    UserPermissionSet,
)
from tests.api.conftest import World

API = "/api/v1"


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _field(client: TestClient, headers: dict[str, str], bp: int, **values: Any) -> Any:
    got = client.post(
        f"{API}/testcases/fields",
        json={"business_project_id": bp, "entity": "case", **values},
        headers=headers,
    )
    assert got.status_code == 201, got.text
    return got.json()


def _suite(client: TestClient, headers: dict[str, str], bp: int, name: str = "Bộ") -> Any:
    got = client.post(
        f"{API}/testcases/suites", json={"business_project_id": bp, "name": name}, headers=headers
    )
    assert got.status_code == 201, got.text
    return got.json()


def _new_case(client: TestClient, headers: dict[str, str], suite: int, **values: Any) -> Any:
    return client.post(
        f"{API}/testcases",
        json={"suite_id": suite, "title": "Đăng nhập", **values},
        headers=headers,
    )


def _sample(db: Session, user: User, role: str) -> None:
    set_id = db.scalar(select(PermissionSet.id).where(PermissionSet.name == f"Testcase - {role}"))
    assert set_id is not None, role
    db.add(UserPermissionSet(user_id=user.id, set_id=set_id))
    db.flush()


def test_field_crud_archive_restore_and_order(
    client: TestClient, world: World, as_user: Any
) -> None:
    bp = _bp(world)
    admin, an = as_user(world.admin), as_user(world.an)
    env = _field(
        client,
        admin,
        bp,
        name="Môi trường",
        type="dropdown",
        options=["Staging", "Production", "staging"],
        default="staging",
    )
    assert env["key"] == "moi_truong"
    assert env["options"] == ["Staging", "Production"]
    assert env["default"] == "Staging"
    # Same name refused; same slug from another name gets a suffix.
    again = client.post(
        f"{API}/testcases/fields",
        json={"business_project_id": bp, "entity": "case", "name": "môi trường", "type": "text"},
        headers=admin,
    )
    assert again.status_code == 409
    other = _field(client, admin, bp, name="Moi-truong!", type="text")
    assert other["key"] == "moi_truong_2"
    # A result field with the same name is another entity.
    res = client.post(
        f"{API}/testcases/fields",
        json={"business_project_id": bp, "entity": "result", "name": "Môi trường", "type": "url"},
        headers=admin,
    )
    assert res.status_code == 201 and res.json()["key"] == "moi_truong"

    # A member (tests.view) reads, may not write.
    listed = client.get(
        f"{API}/testcases/fields", params={"business_project_id": bp, "entity": "case"}, headers=an
    )
    assert [f["key"] for f in listed.json()] == ["moi_truong", "moi_truong_2"]
    assert (
        client.post(
            f"{API}/testcases/fields",
            json={"business_project_id": bp, "entity": "case", "name": "X", "type": "text"},
            headers=an,
        ).status_code
        == 403
    )
    assert client.delete(f"{API}/testcases/fields/{env['id']}", headers=an).status_code == 403

    # PATCH: options change drops a default no longer listed; key / type never change.
    patched = client.patch(
        f"{API}/testcases/fields/{env['id']}",
        json={"name": "Môi trường test", "options": ["Production", "UAT"], "type": "text"},
        headers=admin,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["key"] == "moi_truong" and body["type"] == "dropdown"
    assert body["name"] == "Môi trường test" and body["default"] is None
    bad_default = client.patch(
        f"{API}/testcases/fields/{env['id']}", json={"default": "Nope"}, headers=admin
    )
    assert bad_default.status_code == 422

    # Archive hides it; archived=true lists it; restore brings it back.
    assert client.delete(f"{API}/testcases/fields/{env['id']}", headers=admin).status_code == 204
    live = client.get(
        f"{API}/testcases/fields", params={"business_project_id": bp, "entity": "case"}, headers=an
    ).json()
    assert [f["key"] for f in live] == ["moi_truong_2"]
    every = client.get(
        f"{API}/testcases/fields",
        params={"business_project_id": bp, "entity": "case", "archived": True},
        headers=an,
    ).json()
    assert {f["key"]: f["archived_at"] is not None for f in every} == {
        "moi_truong": True,
        "moi_truong_2": False,
    }
    back = client.post(f"{API}/testcases/fields/{env['id']}/restore", headers=admin)
    assert back.status_code == 200 and back.json()["archived_at"] is None

    ordered = client.put(
        f"{API}/testcases/fields/order",
        json={"business_project_id": bp, "entity": "case", "ids": [other["id"]]},
        headers=admin,
    )
    assert ordered.status_code == 200, ordered.text
    assert [f["key"] for f in ordered.json()] == ["moi_truong_2", "moi_truong"]


def test_each_type_is_checked(client: TestClient, world: World, as_user: Any) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)["id"]
    for name, type_, options in (
        ("Ghi chú ngắn", "text", []),
        ("Mô tả dài", "textarea", []),
        ("Số lượng", "number", []),
        ("Hạn", "date", []),
        ("Mức", "dropdown", ["Thấp", "Cao"]),
        ("Nền tảng", "multiselect", ["Web", "iOS", "Android"]),
        ("Đã review", "checkbox", []),
        ("Chủ sở hữu", "user", []),
        ("Tài liệu", "url", []),
    ):
        _field(client, admin, bp, name=name, type=type_, options=options)
    good = {
        "ghi_chu_ngan": "  một   dòng ",
        "mo_ta_dai": "a\nb",
        "so_luong": 3.0,
        "han": "2026-12-31",
        "muc": "cao",
        "nen_tang": ["web", "Android", "Web"],
        "da_review": False,
        "chu_so_huu": world.an.id,
        "tai_lieu": "https://example.com/a",
        "unknown": "dropped",
    }
    made = _new_case(client, admin, suite, custom=good)
    assert made.status_code == 201, made.text
    assert made.json()["custom"] == {
        "ghi_chu_ngan": "một dòng",
        "mo_ta_dai": "a\nb",
        "so_luong": 3,
        "han": "2026-12-31",
        "muc": "Cao",
        "nen_tang": ["Web", "Android"],
        "da_review": False,
        "chu_so_huu": world.an.id,
        "tai_lieu": "https://example.com/a",
    }
    for key, value in (
        ("ghi_chu_ngan", "x" * 2001),
        ("mo_ta_dai", "x" * 20_001),
        ("so_luong", "3"),
        ("so_luong", True),
        ("han", "31/12/2026"),
        ("han", "2026-02-30"),
        ("muc", "Trung bình"),
        ("nen_tang", "Web"),
        ("nen_tang", ["Windows"]),
        ("da_review", "yes"),
        ("chu_so_huu", 999_999),
        ("chu_so_huu", "an"),
        ("tai_lieu", "ftp://example.com"),
        ("tai_lieu", "javascript:alert(1)"),
    ):
        got = _new_case(client, admin, suite, custom={key: value})
        assert got.status_code == 422, (key, value, got.text)
    # The message names the field.
    named = _new_case(client, admin, suite, custom={"muc": "Không có"})
    assert "Mức" in named.json()["detail"]


def test_required_blocks_the_editor_not_an_import_and_defaults(
    client: TestClient, world: World, as_user: Any
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)["id"]
    other_suite = _suite(client, admin, bp, "Bộ khác")["id"]
    _field(client, admin, bp, name="Thành phần", type="text", required=True, suite_ids=[suite])
    _field(client, admin, bp, name="Kênh", type="dropdown", options=["Web", "App"], default="App")
    _field(client, admin, bp, name="Ghi chú bước", type="text", required=True, templates=["steps"])

    missing = _new_case(client, admin, suite)
    assert missing.status_code == 422
    assert "Thành phần" in missing.json()["detail"]
    # Another suite: the field does not apply; the default is filled.
    elsewhere = _new_case(client, admin, other_suite)
    assert elsewhere.status_code == 201, elsewhere.text
    assert elsewhere.json()["custom"] == {"kenh": "App"}
    # Template "steps" brings its own required field.
    steps = _new_case(
        client,
        admin,
        other_suite,
        template="steps",
        steps_list=[{"content": "a", "expected": "b"}],
    )
    assert steps.status_code == 422 and "Ghi chú bước" in steps.json()["detail"]

    made = _new_case(client, admin, suite, custom={"thanh_phan": "Login", "kenh": "Web"})
    assert made.status_code == 201, made.text
    case = made.json()
    assert case["custom"] == {"thanh_phan": "Login", "kenh": "Web"}
    # PATCH merges; clearing a required field is refused, clearing another one works.
    cleared = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": case["revision"], "custom": {"thanh_phan": None}},
        headers=admin,
    )
    assert cleared.status_code == 422
    ok = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": case["revision"], "custom": {"kenh": None}, "title": "Mới"},
        headers=admin,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["custom"] == {"thanh_phan": "Login"}

    # An import is not blocked by a required field (the default still fills in).
    imported = client.post(
        f"{API}/testcases/import",
        json={
            "business_project_id": bp,
            "sheets": [
                {
                    "name": "Tab",
                    "suite_id": suite,
                    "header_row": 0,
                    "mapping": {"title": 0, "steps": 1},
                    "rows": [["Tiêu đề", "Các bước"], ["Nhập không có thành phần", "1. a"]],
                }
            ],
        },
        headers=admin,
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["suites"][0]["created"] == 1
    listed = client.get(
        f"{API}/testcases",
        params={"business_project_id": bp, "suite_id": suite, "q": "Nhap khong"},
        headers=admin,
    ).json()["items"]
    assert listed[0]["custom"] == {"kenh": "App"}


def test_filter_search_and_revision_restore(client: TestClient, world: World, as_user: Any) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)["id"]
    _field(client, admin, bp, name="Mức", type="dropdown", options=["Thấp", "Cao"])
    _field(client, admin, bp, name="Nền tảng", type="multiselect", options=["Web", "iOS"])
    _field(client, admin, bp, name="Đã review", type="checkbox")
    _field(client, admin, bp, name="Mô tả", type="text")
    one = _new_case(
        client,
        admin,
        suite,
        title="Một",
        custom={
            "muc": "Cao",
            "nen_tang": ["Web", "iOS"],
            "da_review": True,
            "mo_ta": "Thanh toán VNPay",
        },
    ).json()
    _new_case(client, admin, suite, title="Hai", custom={"muc": "Thấp", "nen_tang": ["iOS"]})

    def titles(**params: Any) -> list[str]:
        got = client.get(
            f"{API}/testcases", params={"business_project_id": bp, **params}, headers=admin
        )
        assert got.status_code == 200, got.text
        return sorted(c["title"] for c in got.json()["items"])

    assert titles(**{"f.muc": "cao"}) == ["Một"]
    assert titles(**{"f.nen_tang": "iOS"}) == ["Hai", "Một"]
    assert titles(**{"f.nen_tang": "Web"}) == ["Một"]
    assert titles(**{"f.da_review": "true"}) == ["Một"]
    assert titles(**{"f.da_review": "false"}) == ["Hai"]
    assert titles(**{"f.mo_ta": "vnpay"}) == ["Một"]
    assert titles(**{"f.muc": "Cao", "f.nen_tang": "iOS"}) == ["Một"]
    unknown = client.get(
        f"{API}/testcases", params={"business_project_id": bp, "f.nope": "x"}, headers=admin
    )
    assert unknown.status_code == 422
    # Search reads text, dropdown and multi-select values (accents folded).
    assert titles(q="thanh toan") == ["Một"]

    changed = client.patch(
        f"{API}/testcases/{one['id']}",
        json={"revision": one["revision"], "custom": {"muc": "Thấp"}},
        headers=admin,
    )
    assert changed.status_code == 200, changed.text
    detail = changed.json()
    assert detail["revisions"][0]["snapshot"]["custom"]["muc"] == "Cao"
    restored = client.post(f"{API}/testcases/{one['id']}/restore/1", headers=admin)
    assert restored.status_code == 200, restored.text
    assert restored.json()["custom"]["muc"] == "Cao"


def test_result_fields_required_only_with_a_status(
    client: TestClient, world: World, as_user: Any
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)["id"]
    _new_case(client, admin, suite)
    got = client.post(
        f"{API}/testcases/fields",
        json={
            "business_project_id": bp,
            "entity": "result",
            "name": "Build",
            "type": "text",
            "required": True,
        },
        headers=admin,
    )
    assert got.status_code == 201, got.text
    run = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"suite_id": suite, "all": True}},
        headers=admin,
    ).json()
    listed = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=admin).json()["items"]
    assert listed[0]["suite_id"] == suite
    test_id = listed[0]["id"]
    url = f"{API}/testcases/tests/{test_id}/results"
    refused = client.post(url, json={"status": "passed"}, headers=admin)
    assert refused.status_code == 422 and "Build" in refused.json()["detail"]
    comment = client.post(url, json={"comment": "chỉ bình luận"}, headers=admin)
    assert comment.status_code == 201, comment.text
    ok = client.post(url, json={"status": "passed", "custom": {"build": "1.2.3"}}, headers=admin)
    assert ok.status_code == 201, ok.text
    assert ok.json()["results"][0]["custom"] == {"build": "1.2.3"}
    assert ok.json()["status"] == "passed"

    bulk_url = f"{API}/testcases/runs/{run['id']}/results"
    assert (
        client.post(
            bulk_url, json={"test_ids": [test_id], "status": "failed"}, headers=admin
        ).status_code
        == 422
    )
    bulk = client.post(
        bulk_url,
        json={"test_ids": [test_id], "status": "failed", "custom": {"build": "1.2.4"}},
        headers=admin,
    )
    assert bulk.status_code == 200, bulk.text
    detail = client.get(f"{API}/testcases/tests/{test_id}", headers=admin).json()
    assert detail["results"][0]["custom"] == {"build": "1.2.4"}


def test_export_and_import_columns(client: TestClient, world: World, as_user: Any) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)["id"]
    _field(client, admin, bp, name="Mức độ rủi ro", type="dropdown", options=["Thấp", "Cao"])
    _field(client, admin, bp, name="Nền tảng", type="multiselect", options=["Web", "iOS"])
    archived = _field(client, admin, bp, name="Cũ", type="text")
    client.delete(f"{API}/testcases/fields/{archived['id']}", headers=admin)
    _new_case(
        client,
        admin,
        suite,
        title="Có rủi ro",
        custom={"muc_do_rui_ro": "Cao", "nen_tang": ["Web", "iOS"]},
    )

    got = client.get(
        f"{API}/testcases/export",
        params={"business_project_id": bp, "suite_id": suite},
        headers=admin,
    )
    assert got.status_code == 200
    ws = load_workbook(io.BytesIO(got.content)).worksheets[0]
    header = [c.value for c in ws[1]]
    assert "Mức độ rủi ro" in header and "Nền tảng" in header and "Cũ" not in header
    row = [c.value for c in ws[2]]
    assert row[header.index("Mức độ rủi ro")] == "Cao"
    assert row[header.index("Nền tảng")] == "Web, iOS"

    # Reading the file guesses the custom columns (header = field name, accents aside).
    rows = [[str(c.value or "") for c in r] for r in ws.iter_rows()]
    rows[0][header.index("Mức độ rủi ro")] = "MUC DO RUI RO"
    rows[1][header.index("Mức độ rủi ro")] = "thấp"
    rows[1][header.index("Nền tảng")] = "iOS"
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    for r in rows:
        sheet.append(r)
    buf = io.BytesIO()
    book.save(buf)
    read = client.post(
        f"{API}/testcases/import/read-file",
        params={"business_project_id": bp, "filename": "a.xlsx"},
        content=buf.getvalue(),
        headers=admin,
    )
    assert read.status_code == 200, read.text
    tab = read.json()["sheets"][0]
    assert tab["mapping"]["custom.muc_do_rui_ro"] == header.index("Mức độ rủi ro")
    assert tab["mapping"]["custom.nen_tang"] == header.index("Nền tảng")
    assert read.json()["fields"]["custom.nen_tang"] == "Nền tảng"
    done = client.post(
        f"{API}/testcases/import",
        json={
            "business_project_id": bp,
            "sheets": [
                {
                    "name": tab["name"],
                    "suite_id": suite,
                    "header_row": tab["header_row"],
                    "mapping": tab["mapping"],
                    "rows": tab["rows"],
                }
            ],
        },
        headers=admin,
    )
    assert done.status_code == 200, done.text
    assert done.json()["suites"][0]["updated"] == 1
    case = client.get(
        f"{API}/testcases", params={"business_project_id": bp, "suite_id": suite}, headers=admin
    ).json()["items"][0]
    assert case["custom"] == {"muc_do_rui_ro": "Thấp", "nen_tang": ["iOS"]}


def test_summary_report_by_a_dropdown(client: TestClient, world: World, as_user: Any) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)["id"]
    _field(client, admin, bp, name="Mức", type="dropdown", options=["Thấp", "Cao"])
    _new_case(client, admin, suite, title="A", custom={"muc": "Cao"})
    _new_case(client, admin, suite, title="B", custom={"muc": "Thấp"})
    _new_case(client, admin, suite, title="C")
    client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"suite_id": suite, "all": True}},
        headers=admin,
    )

    def total(**params: Any) -> int:
        got = client.get(
            f"{API}/testcases/reports/summary",
            params={"business_project_id": bp, **params},
            headers=admin,
        )
        assert got.status_code == 200, got.text
        return int(got.json()["tests"]["total"])

    assert total() == 3
    assert total(field="muc", value="cao") == 1
    bad = client.get(
        f"{API}/testcases/reports/summary",
        params={"business_project_id": bp, "field": "muc", "value": "Nope"},
        headers=admin,
    )
    assert bad.status_code == 422


def test_fields_are_per_project(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    other = BusinessProject(name="Khác")
    db_session.add(other)
    db_session.flush()
    mine = _field(client, admin, bp, name="Mức", type="text")
    theirs = _field(client, admin, other.id, name="Mức", type="dropdown", options=["A"])
    assert mine["key"] == theirs["key"] == "muc"
    listed = client.get(
        f"{API}/testcases/fields", params={"business_project_id": other.id}, headers=admin
    ).json()
    assert [f["id"] for f in listed] == [theirs["id"]]
    # A suite of this dự án uses this dự án's fields: "muc" is text here.
    suite = _suite(client, admin, bp)["id"]
    made = _new_case(client, admin, suite, custom={"muc": "anything"})
    assert made.status_code == 201, made.text
    # Someone who cannot see the other dự án does not find its fields.
    an = as_user(world.an)
    assert (
        client.get(
            f"{API}/testcases/fields", params={"business_project_id": other.id}, headers=an
        ).status_code
        == 404
    )
    assert client.delete(f"{API}/testcases/fields/{theirs['id']}", headers=an).status_code == 404
    # A suite_ids entry must be a suite of the dự án.
    refused = client.post(
        f"{API}/testcases/fields",
        json={
            "business_project_id": other.id,
            "entity": "case",
            "name": "X",
            "type": "text",
            "suite_ids": [suite],
        },
        headers=admin,
    )
    assert refused.status_code == 422


def test_delete_needs_tests_delete_and_approve_needs_tests_approve(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    section = client.post(
        f"{API}/testcases/sections", json={"suite_id": suite["id"], "name": "S"}, headers=admin
    ).json()
    case = _new_case(client, admin, suite["id"]).json()
    _sample(db_session, world.an, "Tester")
    tester = as_user(world.an)

    rows = {p["id"]: p for p in client.get(f"{API}/testcases/projects", headers=tester).json()}
    assert rows[bp]["can_delete"] is False and rows[bp]["can_approve"] is False
    rows = {p["id"]: p for p in client.get(f"{API}/testcases/projects", headers=admin).json()}
    assert rows[bp]["can_delete"] is True and rows[bp]["can_approve"] is True
    detail = client.get(f"{API}/testcases/{case['id']}", headers=tester).json()
    assert detail["can_edit"] is True
    assert detail["can_delete"] is False and detail["can_approve"] is False
    assert detail["approval_on"] is False
    assert client.delete(f"{API}/testcases/{case['id']}", headers=tester).status_code == 403
    assert (
        client.delete(f"{API}/testcases/sections/{section['id']}", headers=tester).status_code
        == 403
    )
    assert client.delete(f"{API}/testcases/suites/{suite['id']}", headers=tester).status_code == 403

    # With approval on, a tester may not set "approved" (create, patch); an approved case they
    # edit goes back to "review".
    db_session.add(TestProjectSettings(business_project_id=bp, require_approval=True))
    db_session.flush()
    assert _new_case(client, tester, suite["id"], status="approved").status_code == 403
    assert (
        client.patch(
            f"{API}/testcases/{case['id']}",
            json={"revision": case["revision"], "status": "approved"},
            headers=tester,
        ).status_code
        == 403
    )
    approved = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": case["revision"], "status": "approved"},
        headers=admin,
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    edited = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": approved.json()["revision"], "title": "Sửa"},
        headers=tester,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "review"
    assert edited.json()["approval_on"] is True
    # Waiting for review is a status filter.
    queue = client.get(
        f"{API}/testcases",
        params={"business_project_id": bp, "status": "review"},
        headers=tester,
    ).json()
    assert [c["id"] for c in queue["items"]] == [case["id"]]

    # The admin (every permission) deletes.
    assert client.delete(f"{API}/testcases/{case['id']}", headers=admin).status_code == 204
    assert (
        client.delete(f"{API}/testcases/sections/{section['id']}", headers=admin).status_code == 200
    )
    assert client.delete(f"{API}/testcases/suites/{suite['id']}", headers=admin).status_code == 204


def test_number_never_takes_nan_or_infinity(client: TestClient, world: World, as_user: Any) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)["id"]
    _field(client, admin, bp, name="Điểm", type="number")
    assert _new_case(client, admin, suite, custom={"diem": 2}).status_code == 201
    for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "1e999"):
        got = client.get(
            f"{API}/testcases", params={"business_project_id": bp, "f.diem": raw}, headers=admin
        )
        assert got.status_code == 422, (raw, got.text)
        assert "Điểm" in got.json()["detail"]
    ok = client.get(
        f"{API}/testcases", params={"business_project_id": bp, "f.diem": "2"}, headers=admin
    )
    assert ok.status_code == 200 and ok.json()["total"] == 1
    # A JSON body with NaN / Infinity (Python's json reads them) is refused as a value too.
    for token in ("NaN", "Infinity", "-Infinity"):
        got = client.post(
            f"{API}/testcases",
            content=f'{{"suite_id": {suite}, "title": "X", "custom": {{"diem": {token}}}}}',
            headers={**admin, "Content-Type": "application/json"},
        )
        assert got.status_code == 422, (token, got.text)
    bad_default = client.post(
        f"{API}/testcases/fields",
        content=f'{{"business_project_id": {bp}, "entity": "case", "name": "N", '
        '"type": "number", "default": NaN}',
        headers={**admin, "Content-Type": "application/json"},
    )
    assert bad_default.status_code == 422, bad_default.text


def test_a_field_name_never_starts_like_a_formula(
    client: TestClient, world: World, as_user: Any
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    for name in ("=SUM(A1)", "+1", "-x", "@cmd", "  =trim"):
        got = client.post(
            f"{API}/testcases/fields",
            json={"business_project_id": bp, "entity": "case", "name": name, "type": "text"},
            headers=admin,
        )
        assert got.status_code == 422, (name, got.text)
    ok = _field(client, admin, bp, name="Giá trị = 1", type="text")
    renamed = client.patch(
        f"{API}/testcases/fields/{ok['id']}", json={"name": "=HYPERLINK(1)"}, headers=admin
    )
    assert renamed.status_code == 422
