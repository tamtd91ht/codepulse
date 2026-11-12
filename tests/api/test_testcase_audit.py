"""The testcase audit trail through the API (docs/specs/30 §15.14, §15.16, G12): what the listener
records for real endpoints (a case edit, a run closed, a permission set's items, a token), the
list with its filters, pages and permissions, the facets and the export."""

import csv
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.routers import test_audit
from app.models import AuditLog, BusinessProject, TestCase, TestSuite
from tests.api.conftest import World, grant

API = "/api/v1"


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _trail(db: Session, action: str, target: str | None = None) -> list[AuditLog]:
    query = select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
    if target is not None:
        query = query.where(AuditLog.target == target)
    return list(db.scalars(query))


def _changes(row: AuditLog) -> dict[str, tuple[Any, Any]]:
    assert row.details is not None
    return {c["field"]: (c["before"], c["after"]) for c in row.details["changes"]}


def _suite_and_case(client: TestClient, h: dict[str, str], bp: int) -> tuple[Any, Any]:
    suite = client.post(
        f"{API}/testcases/suites", json={"business_project_id": bp, "name": "Đăng nhập"}, headers=h
    )
    assert suite.status_code == 201, suite.text
    case = client.post(
        f"{API}/testcases",
        json={"suite_id": suite.json()["id"], "title": "Đăng nhập đúng", "priority": "high"},
        headers=h,
    )
    assert case.status_code == 201, case.text
    return suite.json(), case.json()


def test_case_edit_and_run_close_are_recorded(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    suite, case = _suite_and_case(client, h, bp)

    created = _trail(db_session, "case.create", f"case:{case['id']}")
    assert len(created) == 1
    row = created[0]
    assert row.actor_user_id == world.manager.id and row.business_project_id == bp
    assert row.details is not None and row.details["entity"] == "case"
    assert row.details["id"] == case["id"] and row.details["op"] == "create"
    got = _changes(row)
    assert got["title"] == (None, "Đăng nhập đúng") and got["priority"] == (None, "high")
    assert "search" not in got and "updated_at" not in got and "created_at" not in got
    suite_row = _trail(db_session, "suite.create", f"suite:{suite['id']}")[0]
    assert suite_row.business_project_id == bp

    edited = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": case["revision"], "title": "Đăng nhập bằng email", "priority": "low"},
        headers=h,
    )
    assert edited.status_code == 200, edited.text
    upd = _trail(db_session, "case.update", f"case:{case['id']}")
    assert len(upd) == 1
    got = _changes(upd[0])
    assert got["title"] == ("Đăng nhập đúng", "Đăng nhập bằng email")
    assert got["priority"] == ("high", "low")
    assert "search" not in got and "updated_at" not in got
    assert upd[0].actor_user_id == world.manager.id and upd[0].business_project_id == bp

    # A run, closed: the close is an update of the run with its completed_at.
    run = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"case_ids": [case["id"]]}},
        headers=h,
    )
    assert run.status_code == 201, run.text
    rid = run.json()["id"]
    assert _trail(db_session, "run.create", f"run:{rid}")[0].business_project_id == bp
    closed = client.post(f"{API}/testcases/runs/{rid}/close", headers=h)
    assert closed.status_code == 200, closed.text
    closes = [
        r for r in _trail(db_session, "run.update", f"run:{rid}") if "completed_at" in _changes(r)
    ]
    assert len(closes) == 1
    before, after = _changes(closes[0])["completed_at"]
    assert before is None and after
    assert closes[0].actor_user_id == world.manager.id

    deleted = client.delete(f"{API}/testcases/{case['id']}", headers=h)
    assert deleted.status_code == 204, deleted.text
    gone = _trail(db_session, "case.delete", f"case:{case['id']}")
    assert len(gone) == 1 and gone[0].business_project_id == bp
    assert _changes(gone[0])["title"] == ("Đăng nhập bằng email", None)


def test_permission_set_items_and_token_secret(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    base = f"{API}/admin/permission-sets"
    made = client.post(
        base,
        json={"name": "Kiểm toán", "items": [{"permission": "review.view", "scope": "all"}]},
        headers=admin,
    )
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    # The endpoint's own row and the trail's are one row: its details plus the before / after.
    rows = _trail(db_session, "permission_set.create", f"permission_set:{sid}")
    assert len(rows) == 1 and rows[0].details is not None
    assert rows[0].details["name"] == "Kiểm toán" and _changes(rows[0])["name"] == (
        None,
        "Kiểm toán",
    )
    assert rows[0].business_project_id is None
    item = f"permission_set_item:{sid}:review.view"
    assert len(_trail(db_session, "permission_set_item.create", item)) == 1

    # The items are rewritten (deleted in bulk, added again): the same key is one update.
    patched = client.patch(
        f"{base}/{sid}",
        json={"items": [{"permission": "review.view", "scope": "team"}]},
        headers=admin,
    )
    assert patched.status_code == 200, patched.text
    upd = _trail(db_session, "permission_set_item.update", item)
    assert len(upd) == 1 and _changes(upd[0]) == {"scope": ("all", "team")}
    assert upd[0].actor_user_id == world.admin.id
    assert _trail(db_session, "permission_set_item.delete", item) == []
    # Unchanged on a second identical write: nothing recorded.
    again = client.patch(
        f"{base}/{sid}",
        json={"items": [{"permission": "review.view", "scope": "team"}]},
        headers=admin,
    )
    assert again.status_code == 200
    assert len(_trail(db_session, "permission_set_item.update", item)) == 1
    # A permission dropped: deleted, with what it was.
    dropped = client.patch(
        f"{base}/{sid}",
        json={"items": [{"permission": "insights.view", "scope": "flag"}]},
        headers=admin,
    )
    assert dropped.status_code == 200, dropped.text
    gone = _trail(db_session, "permission_set_item.delete", item)
    assert len(gone) == 1 and _changes(gone[0])["scope"] == ("team", None)

    # A token: its hash never shows.
    token = client.post(f"{API}/me/tokens", json={"name": "laptop"}, headers=as_user(world.an))
    assert token.status_code == 201, token.text
    tid = token.json()["id"]
    rows = _trail(db_session, "api_token.create", f"api_token:{tid}")
    assert len(rows) == 1 and rows[0].actor_user_id == world.an.id
    got = _changes(rows[0])
    assert got["token_hash"] == (None, "***") and got["name"] == (None, "laptop")
    raw = token.json()["token"]
    assert raw not in str(rows[0].details)
    assert rows[0].business_project_id is None


def _seed_rows(db: Session, world: World) -> tuple[int, int, TestCase]:
    """Trail rows in the world's dự án and in another one, plus a global row."""
    bp = _bp(world)
    other = BusinessProject(name="Khác")
    db.add(other)
    db.flush()
    suite = TestSuite(business_project_id=bp, name="S")
    hidden = TestSuite(business_project_id=other.id, name="Ẩn")
    db.add_all([suite, hidden])
    db.flush()
    case = TestCase(suite_id=suite.id, title="Một")
    db.add(case)
    db.flush()
    case.title = '=HYPERLINK("x")'
    db.flush()
    db.add(AuditLog(actor_user_id=world.admin.id, action="project.update", target="project:1"))
    db.flush()
    return bp, other.id, case


def test_list_filters_pages_and_permissions(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp, other, case = _seed_rows(db_session, world)
    grant(db_session, world.manager, bp, "manage")
    url = f"{API}/testcases/audit"

    # A tester (view only) may not; a dự án one has no grant on does not exist.
    assert (
        client.get(url, params={"business_project_id": bp}, headers=as_user(world.an)).status_code
        == 403
    )
    assert client.get(url, headers=as_user(world.an)).status_code == 403
    mgr = as_user(world.manager)
    assert client.get(url, params={"business_project_id": other}, headers=mgr).status_code == 404

    page = client.get(url, params={"business_project_id": bp}, headers=mgr)
    assert page.status_code == 200, page.text
    items = page.json()["items"]
    assert {i["business_project_id"] for i in items} == {bp}
    assert [i["action"] for i in items] == ["case.update", "case.create", "suite.create"]
    first = items[0]
    assert first["entity"] == "case" and first["entity_id"] == case.id
    assert first["target"] == f"case:{case.id}"
    assert first["changes"] == [{"field": "title", "before": "Một", "after": '=HYPERLINK("x")'}]
    assert first["details"] == {"op": "update"} and "request_id" in first
    # Without a dự án, a manager sees the dự án they manage only.
    mine = client.get(url, headers=mgr).json()["items"]
    assert {i["business_project_id"] for i in mine} == {bp}

    # Filters.
    def actions(**params: Any) -> list[str]:
        got = client.get(url, params={"business_project_id": bp, **params}, headers=mgr)
        assert got.status_code == 200, got.text
        return [i["action"] for i in got.json()["items"]]

    assert actions(entity="suite") == ["suite.create"]
    assert actions(action="case.create") == ["case.create"]
    assert actions(target=f"case:{case.id}") == ["case.update", "case.create"]
    assert actions(q="hyperlink") == ["case.update"]
    assert actions(actor_id=world.binh.id) == []
    assert actions(**{"from": "2000-01-01", "to": "2000-01-02"}) == []
    assert (
        client.get(
            url, params={"business_project_id": bp, "entity": "x;drop"}, headers=mgr
        ).status_code
        == 422
    )
    assert (
        client.get(url, params={"business_project_id": bp, "limit": 201}, headers=mgr).status_code
        == 422
    )

    # Pages by before_id.
    one = client.get(url, params={"business_project_id": bp, "limit": 2}, headers=mgr).json()
    assert len(one["items"]) == 2 and one["next_before_id"] == one["items"][-1]["id"]
    two = client.get(
        url,
        params={"business_project_id": bp, "limit": 2, "before_id": one["next_before_id"]},
        headers=mgr,
    ).json()
    assert [i["action"] for i in two["items"]] == ["suite.create"]
    assert two["next_before_id"] is None

    # An admin without a dự án sees every row: other dự án and the global ones (old rows too).
    everything = client.get(url, params={"limit": 200}, headers=as_user(world.admin)).json()
    projects = {i["business_project_id"] for i in everything["items"]}
    assert {bp, other, None} <= projects
    old = next(i for i in everything["items"] if i["action"] == "project.update")
    assert old["changes"] == [] and old["entity"] == "project" and old["entity_id"] == "1"
    assert old["actor_name"] == world.admin.full_name

    facets = client.get(f"{url}/facets", params={"business_project_id": bp}, headers=mgr).json()
    assert facets["actions"] == ["case.create", "case.update", "suite.create"]
    assert facets["entities"] == ["case", "suite"]
    assert (
        client.get(
            f"{url}/facets", params={"business_project_id": bp}, headers=as_user(world.an)
        ).status_code
        == 403
    )


def test_export_xlsx_and_csv(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp, _other, case = _seed_rows(db_session, world)
    h = as_user(world.admin)
    url = f"{API}/testcases/audit/export"
    got = client.get(url, params={"business_project_id": bp, "format": "xlsx"}, headers=h)
    assert got.status_code == 200, got.text
    assert got.headers["content-disposition"].endswith('.xlsx"')
    ws = load_workbook(io.BytesIO(got.content)).active
    assert ws is not None
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "ID" and len(rows) == 4
    assert rows[1][3] == "case.update"
    change = rows[1][8]
    assert isinstance(change, str) and change.startswith("title: Một -> =HYPERLINK")
    # Text, never a formula.
    cell = ws.cell(2, 9)
    assert cell.data_type == "s"

    got = client.get(url, params={"business_project_id": bp, "format": "csv"}, headers=h)
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/csv")
    lines = list(csv.reader(io.StringIO(got.content.decode("utf-8-sig"))))
    assert lines[0][0] == "ID" and len(lines) == 4
    assert lines[1][4] == "case" and lines[1][5] == str(case.id)
    # A cell a spreadsheet would run is guarded in the CSV.
    db_session.add(AuditLog(action="=1+1", target=None, business_project_id=bp))
    db_session.flush()
    got = client.get(url, params={"business_project_id": bp, "format": "csv"}, headers=h)
    lines = list(csv.reader(io.StringIO(got.content.decode("utf-8-sig"))))
    assert lines[1][3] == "'=1+1"

    tester = as_user(world.an)
    assert client.get(url, params={"business_project_id": bp}, headers=tester).status_code == 403
    assert (
        client.get(url, params={"business_project_id": bp, "format": "pdf"}, headers=h).status_code
        == 422
    )


def test_export_streams_thousands_of_rows(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bp = _bp(world)
    n = 4_500  # more than two chunks
    db_session.execute(
        insert(AuditLog),
        [
            {"action": "case.update", "target": f"case:{i}", "business_project_id": bp}
            for i in range(n)
        ],
    )
    db_session.flush()
    h = as_user(world.admin)
    url = f"{API}/testcases/audit/export"
    got = client.get(url, params={"business_project_id": bp, "format": "xlsx"}, headers=h)
    assert got.status_code == 200, got.text
    assert "content-length" not in got.headers  # streamed, not built in memory first
    ws = load_workbook(io.BytesIO(got.content), read_only=True).active
    assert ws is not None
    rows = list(ws.iter_rows(values_only=True))
    ids = [int(str(r[0])) for r in rows[1:]]
    assert len(ids) == n and ids == sorted(ids, reverse=True) and len(set(ids)) == n
    assert rows[1][6] == f"case:{n - 1}"

    got = client.get(url, params={"business_project_id": bp, "format": "csv"}, headers=h)
    assert got.status_code == 200 and got.content.startswith(b"\xef\xbb\xbf")
    lines = list(csv.reader(io.StringIO(got.content.decode("utf-8-sig"))))
    assert lines[0][0] == "ID" and len(lines) == n + 1
    assert [int(x[0]) for x in lines[1:]] == ids

    # The cap holds across chunks.
    monkeypatch.setattr(test_audit, "EXPORT_MAX", 2_500)
    got = client.get(url, params={"business_project_id": bp, "format": "csv"}, headers=h)
    lines = list(csv.reader(io.StringIO(got.content.decode("utf-8-sig"))))
    assert len(lines) == 2_501 and [int(x[0]) for x in lines[1:]] == ids[:2_500]


def test_manual_audit_row_takes_its_project(db_session: Session, world: World) -> None:
    bp = _bp(world)
    audit(db_session, None, "testcase.manual", "x:1", business_project_id=bp, note="n")
    audit(db_session, None, "testcase.manual", "x:2", note="n")
    db_session.flush()
    rows = _trail(db_session, "testcase.manual")
    assert [r.business_project_id for r in rows] == [bp, None]
    assert rows[0].details == {"business_project_id": bp, "note": "n"}
    assert rows[1].details == {"note": "n"}
