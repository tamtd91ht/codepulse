"""BDD and exploratory cases (docs/specs/30 §15.12, §15.16, 30a G8): the case fields, the live
check, .feature import / export (round trip), AI conversion, exploratory sessions, permissions and
isolation between dự án."""

import io
import time
import zipfile
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    BusinessProject,
    PermissionSet,
    TestAiRun,
    TestBulkOp,
    TestCase,
    TestProjectSettings,
    TestRun,
    TestSuite,
    User,
    UserPermissionSet,
)
from app.review.providers.fake import FakeLLMProvider
from app.testcases import ai, content
from app.testcases import service as tc
from tests.api.conftest import World

API = "/api/v1"

LOGIN = """Background:
  Given trang đăng nhập đang mở

Scenario: Đăng nhập đúng
  When nhập email và mật khẩu đúng
  Then vào trang chủ"""

FEATURE = """Feature: Đăng nhập
  Người dùng vào hệ thống.

  Background:
    Given trang đăng nhập đang mở

  @smoke
  Scenario: Đăng nhập đúng
    When nhập email và mật khẩu đúng
    Then vào trang chủ

  Scenario: Sai mật khẩu
    When nhập mật khẩu sai
    Then hiện "Sai mật khẩu"

  Scenario Outline: Khoá tài khoản
    When nhập sai <times> lần
    Then <result>

    Examples:
      | times | result          |
      | 3     | cảnh báo        |
      | 5     | khoá tài khoản  |
"""


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _suite(client: TestClient, h: dict[str, str], bp: int, name: str = "Đăng nhập") -> Any:
    got = client.post(
        f"{API}/testcases/suites", json={"business_project_id": bp, "name": name}, headers=h
    )
    assert got.status_code == 201, got.text
    return got.json()


def _case(client: TestClient, h: dict[str, str], suite: int, **values: Any) -> Any:
    got = client.post(
        f"{API}/testcases", json={"suite_id": suite, "title": "Đăng nhập", **values}, headers=h
    )
    assert got.status_code == 201, got.text
    return got.json()


def _sample(db: Session, user: User, role: str) -> None:
    set_id = db.scalar(select(PermissionSet.id).where(PermissionSet.name == f"Testcase - {role}"))
    assert set_id is not None, role
    db.add(UserPermissionSet(user_id=user.id, set_id=set_id))
    db.flush()


def _import(client: TestClient, h: dict[str, str], suite: int, text: str, **form: Any) -> Any:
    return client.post(
        f"{API}/testcases/import/feature",
        data={"suite_id": str(suite), **{k: str(v) for k, v in form.items()}},
        files={"file": ("login.feature", text.encode(), "text/plain")},
        headers=h,
    )


def test_bdd_case_fields_steps_and_errors(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    suite = _suite(client, admin, _bp(world))
    case = _case(client, admin, suite["id"], template="bdd", bdd="  " + LOGIN.replace("\n", "\n  "))
    assert case["template"] == "bdd" and case["bdd"] == LOGIN  # the common indent removed
    assert case["problems"] == [] and case["params"] == {} and case["charter"] is None
    assert case["steps_view"] == [
        {"content": "Given trang đăng nhập đang mở", "expected": ""},
        {"content": "When nhập email và mật khẩu đúng", "expected": "Then vào trang chủ"},
    ]
    bad = client.post(
        f"{API}/testcases",
        json={
            "suite_id": suite["id"],
            "title": "x",
            "template": "bdd",
            "bdd": "Scenario: a\n  Givn",
        },
        headers=admin,
    )
    assert bad.status_code == 422 and "dòng 2" in bad.json()["detail"]
    two = client.post(
        f"{API}/testcases",
        json={
            "suite_id": suite["id"],
            "title": "x",
            "template": "bdd",
            "bdd": "Scenario: a\n  Given x\nScenario: b\n  Given y",
        },
        headers=admin,
    )
    assert two.status_code == 422 and "đúng một Scenario" in two.json()["detail"]
    # An edit that breaks the scenario is refused; the case keeps its text.
    broken = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": case["revision"], "bdd": "Scenario: a\n  | x |"},
        headers=admin,
    )
    assert broken.status_code == 422
    # No Then: the problem filter and the case say so.
    lazy = _case(
        client, admin, suite["id"], title="Chưa xong", template="bdd", bdd="Scenario: a\n  Given x"
    )
    assert lazy["problems"] == ["thiếu kết quả mong đợi"]
    listed = client.get(
        f"{API}/testcases",
        params={"business_project_id": _bp(world), "suite_id": suite["id"], "problem": True},
        headers=admin,
    ).json()
    assert [c["id"] for c in listed["items"]] == [lazy["id"]]
    # Search reads the scenario.
    found = client.get(
        f"{API}/testcases",
        params={"business_project_id": _bp(world), "q": "trang chu"},
        headers=admin,
    ).json()
    assert case["id"] in [c["id"] for c in found["items"]]


def test_exploratory_case_and_revision_restore(
    client: TestClient, world: World, as_user: Any
) -> None:
    admin = as_user(world.admin)
    suite = _suite(client, admin, _bp(world))
    case = _case(client, admin, suite["id"], template="exploratory", estimate="30m")
    assert case["problems"] == ["thiếu charter"]
    got = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": case["revision"], "charter": "Khám phá luồng quên mật khẩu"},
        headers=admin,
    )
    assert got.status_code == 200 and got.json()["problems"] == []
    assert got.json()["revisions"][0]["snapshot"]["charter"] is None
    long = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": got.json()["revision"], "charter": "x" * 5001},
        headers=admin,
    )
    assert long.status_code == 422
    back = client.post(f"{API}/testcases/{case['id']}/restore/1", headers=admin)
    assert back.status_code == 200 and back.json()["charter"] is None


def test_check_endpoint(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    ok = client.post(
        f"{API}/testcases/bdd/check",
        json={"text": "Scenario Outline: a\n  When <x>\n  Then y\n  Examples:\n   | x |\n   | 1 |"},
        headers=admin,
    ).json()
    assert ok["ok"] and ok["errors"] == [] and ok["scenarios"] == 1
    assert ok["outline_columns"] == ["x"] and ok["steps"][0]["expected"] == "Then y"
    bad = client.post(
        f"{API}/testcases/bdd/check", json={"text": "Scenario: a\n  Given x\n  Thn"}, headers=admin
    ).json()
    assert bad["ok"] is False and bad["errors"][0]["line"] == 3
    whole = client.post(
        f"{API}/testcases/bdd/check", json={"text": FEATURE, "mode": "feature"}, headers=admin
    ).json()
    assert (
        whole["ok"] and whole["scenarios"] == 3 and whole["outline_columns"] == ["times", "result"]
    )


def test_import_three_scenarios_then_export_round_trip(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    got = _import(client, admin, suite["id"], FEATURE)
    assert got.status_code == 200, got.text
    out = got.json()
    assert (
        out["created"] == 3 and out["updated"] == 0 and out["sections"] == 1 and not out["errors"]
    )
    cases = client.get(
        f"{API}/testcases",
        params={"business_project_id": bp, "suite_id": suite["id"]},
        headers=admin,
    ).json()["items"]
    assert [c["title"] for c in cases] == ["Đăng nhập đúng", "Sai mật khẩu", "Khoá tài khoản"]
    assert all(c["template"] == "bdd" and c["section"] == "Đăng nhập" for c in cases)
    assert cases[0]["bdd"] == LOGIN and cases[0]["tags"] == ["smoke"]
    assert cases[2]["params"] == {
        "columns": ["times", "result"],
        "rows": [["3", "cảnh báo"], ["5", "khoá tài khoản"]],
    }
    sections = client.get(
        f"{API}/testcases/sections", params={"suite_id": suite["id"]}, headers=admin
    ).json()
    assert sections[0]["description"] == "Người dùng vào hệ thống."

    exported = client.get(
        f"{API}/testcases/export/feature", params={"suite_id": suite["id"]}, headers=admin
    )
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/plain")
    text = exported.text
    for c in cases:
        assert f"@{c['key']}" in text
    revisions = {c["id"]: c["revision"] for c in cases}
    again = _import(client, admin, suite["id"], text).json()
    assert again == {
        "created": 0,
        "updated": 0,
        "unchanged": 3,
        "sections": 0,
        "section_id": out["section_id"],
        "errors": [],
    }
    after = client.get(
        f"{API}/testcases",
        params={"business_project_id": bp, "suite_id": suite["id"]},
        headers=admin,
    ).json()["items"]
    assert {c["id"]: c["revision"] for c in after} == revisions
    assert [{k: c[k] for k in ("bdd", "params", "tags", "title")} for c in after] == [
        {k: c[k] for k in ("bdd", "params", "tags", "title")} for c in cases
    ]

    # @C<id> of a case of the suite updates it, even edited; a new tag joins its tags.
    changed = text.replace("Then vào trang chủ", "Then vào trang chủ ngay").replace(
        f"@{cases[0]['key']} @smoke", f"@{cases[0]['key']} @smoke @regression"
    )
    third = _import(client, admin, suite["id"], changed).json()
    assert third["updated"] == 1 and third["unchanged"] == 2 and third["created"] == 0
    one = client.get(f"{API}/testcases/{cases[0]['id']}", headers=admin).json()
    assert "vào trang chủ ngay" in one["bdd"] and one["tags"] == ["smoke", "regression"]
    assert one["revisions"][0]["via"] == "import"

    # Two sections: a zip with one .feature each.
    other = client.post(
        f"{API}/testcases/sections",
        json={"suite_id": suite["id"], "name": "Quên mật khẩu"},
        headers=admin,
    ).json()
    _case(
        client,
        admin,
        suite["id"],
        title="Gửi mail",
        section_id=other["id"],
        template="bdd",
        bdd="Scenario: Gửi mail\n  When bấm Quên\n  Then nhận mail",
    )
    zipped = client.get(
        f"{API}/testcases/export/feature", params={"suite_id": suite["id"]}, headers=admin
    )
    assert zipped.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()
    assert sorted(names) == ["Quên_mật_khẩu.feature", "Đăng_nhập.feature"]
    one_section = client.get(
        f"{API}/testcases/export/feature",
        params={"suite_id": suite["id"], "section_id": other["id"]},
        headers=admin,
    )
    assert "Feature: Quên mật khẩu" in one_section.text
    assert db_session.scalar(select(TestSuite).where(TestSuite.id == suite["id"])) is not None


def test_import_errors_and_limits(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    suite = _suite(client, admin, _bp(world))
    bad = _import(client, admin, suite["id"], "Feature: x\n  Scenario: a\n    Given x\n    Thn y\n")
    assert bad.status_code == 422 and "dòng 4" in bad.json()["detail"]
    no_feature = _import(client, admin, suite["id"], "Scenario: a\n  Given x\n")
    assert no_feature.status_code == 422
    big = _import(client, admin, suite["id"], "#" * (2 * 1024 * 1024 + 1))
    assert big.status_code == 413
    # A @C tag of a case of another suite: a new case, not an update.
    other = _suite(client, admin, _bp(world), name="Khác")
    stranger = _case(client, admin, other["id"])
    got = _import(
        client,
        admin,
        suite["id"],
        f"Feature: F\n  @{stranger['key']}\n  Scenario: a\n    Given x\n",
    ).json()
    assert got["created"] == 1 and got["updated"] == 0
    # A scenario whose variable has no column (not an outline: {{x}} with no table) is fine;
    # the section goes under the folder asked.
    folder = client.post(
        f"{API}/testcases/sections", json={"suite_id": suite["id"], "name": "Gốc"}, headers=admin
    ).json()
    under = _import(
        client,
        admin,
        suite["id"],
        "Feature: Con\n  Scenario: b\n    Given y\n",
        section_id=folder["id"],
    ).json()
    assert under["created"] == 1
    tree = client.get(
        f"{API}/testcases/sections", params={"suite_id": suite["id"]}, headers=admin
    ).json()
    assert "Gốc > Con" in [s["path"] for s in tree]


def test_import_caps_and_audit_project(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    before = db_session.scalar(select(func.count()).select_from(TestCase))

    # More scenarios than one import may hold: refused, nothing made.
    many = "Feature: F\n" + "".join(f"  Scenario: s{i}\n    Given x\n" for i in range(1001))
    got = _import(client, admin, suite["id"], many)
    assert got.status_code == 422 and "1000" in got.json()["detail"]

    # A Background longer than a case may keep: every case would be refused, so is the file,
    # fast (the Background is rendered once, not once per scenario).
    big_bg = (
        "Feature: F\n  Background:\n    Given "
        + "x" * (tc.BDD_MAX + 10)
        + "\n"
        + "".join(f"  Scenario: s{i}\n    Given y\n" for i in range(1000))
    )
    t0 = time.perf_counter()
    got = _import(client, admin, suite["id"], big_bg)
    assert time.perf_counter() - t0 < 5.0
    assert got.status_code == 422 and "Background" in got.json()["detail"]
    assert db_session.scalar(select(func.count()).select_from(TestCase)) == before

    # One scenario too long among good ones: that one is an error, the others are made.
    mixed = (
        "Feature: Trộn\n  Background:\n    Given mở\n"
        "  Scenario: ngắn\n    Given a\n"
        f"  Scenario: dài\n    Given {'y' * tc.BDD_MAX}\n"
    )
    out = _import(client, admin, suite["id"], mixed).json()
    assert out["created"] == 1 and [e["scenario"] for e in out["errors"]] == ["dài"]
    assert str(tc.BDD_MAX) in out["errors"][0]["message"]
    logged = db_session.scalars(
        select(AuditLog)
        .where(
            AuditLog.action == "testcase.import.feature",
            AuditLog.target == f"test_suite:{suite['id']}",
        )
        .order_by(AuditLog.id)
    ).all()
    assert logged and logged[-1].business_project_id == bp


def test_permissions_and_isolation(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    _import(client, admin, suite["id"], FEATURE)
    _sample(db_session, world.outsider, "Guest")
    guest = as_user(world.outsider)
    assert _import(client, guest, suite["id"], FEATURE).status_code == 403
    assert (
        client.get(
            f"{API}/testcases/export/feature", params={"suite_id": suite["id"]}, headers=guest
        ).status_code
        == 200
    )
    # Another dự án's suite: not found for someone who cannot see it.
    other = BusinessProject(name="Khác")
    db_session.add(other)
    db_session.flush()
    hidden = TestSuite(business_project_id=other.id, name="Ẩn", position=1)
    db_session.add(hidden)
    db_session.flush()
    _sample(db_session, world.lead, "Lead")
    lead = as_user(world.lead)
    assert _import(client, lead, hidden.id, FEATURE).status_code == 404
    assert (
        client.get(
            f"{API}/testcases/export/feature", params={"suite_id": hidden.id}, headers=lead
        ).status_code
        == 404
    )


def _run(client: TestClient, h: dict[str, str], bp: int, case_ids: list[int]) -> Any:
    got = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"case_ids": case_ids}},
        headers=h,
    )
    assert got.status_code == 201, got.text
    return got.json()


def test_exploratory_session(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    case = _case(client, admin, suite["id"], template="exploratory", charter="Khám phá giỏ hàng")
    run = _run(client, admin, bp, [case["id"]])
    [test] = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=admin).json()["items"]
    path = f"{API}/testcases/tests/{test['id']}/session"
    early = client.post(f"{path}/notes", json={"kind": "note", "text": "x"}, headers=admin)
    assert early.status_code == 409
    started = client.post(path, json={"action": "start"}, headers=admin)
    assert started.status_code == 200 and started.json()["running"] is True
    assert client.post(path, json={"action": "start"}, headers=admin).status_code == 409
    noted = client.post(
        f"{path}/notes", json={"kind": "bug", "text": "Lỗi tổng tiền"}, headers=admin
    )
    assert noted.status_code == 200
    [note] = noted.json()["notes"]
    assert note["kind"] == "bug" and note["user_id"] == world.admin.id and note["offset_s"] >= 0
    assert (
        client.post(f"{path}/notes", json={"kind": "rant", "text": "x"}, headers=admin).status_code
        == 422
    )
    too_long = client.post(
        f"{path}/notes", json={"kind": "note", "text": "x" * 2001}, headers=admin
    )
    assert too_long.status_code == 422
    paused = client.post(path, json={"action": "pause"}, headers=admin)
    assert paused.status_code == 200 and paused.json()["running"] is False
    assert client.post(path, json={"action": "pause"}, headers=admin).status_code == 409
    assert client.post(path, json={"action": "resume"}, headers=admin).json()["running"] is True

    # Guest may not record, so may not run the session; an unseen test is not found.
    _sample(db_session, world.outsider, "Guest")
    guest = as_user(world.outsider)
    assert client.post(path, json={"action": "pause"}, headers=guest).status_code == 403
    _sample(db_session, world.binh, "Developer")
    # The dự án records by assignees only: a developer may not use an unassigned test.
    db_session.add(TestProjectSettings(business_project_id=bp, assigned_only=True))
    db_session.flush()
    dev = as_user(world.binh)
    assert (
        client.post(f"{path}/notes", json={"kind": "note", "text": "x"}, headers=dev).status_code
        == 403
    )
    assert (
        client.post(
            f"{API}/testcases/tests/999999/session", json={"action": "start"}, headers=admin
        ).status_code
        == 404
    )

    # A closed run: 409.
    row = db_session.get(TestRun, run["id"])
    assert row is not None
    closed = client.post(f"{API}/testcases/runs/{run['id']}/close", headers=admin)
    assert closed.status_code == 200, closed.text
    assert client.post(path, json={"action": "pause"}, headers=admin).status_code == 409


def test_ai_convert_apply_and_undo(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    steps = _case(
        client,
        admin,
        suite["id"],
        template="steps",
        steps_list=[{"content": "Nhập đúng", "expected": "Vào trang chủ"}],
    )
    other = _case(client, admin, suite["id"], title="Khác")
    missing = client.post(
        f"{API}/testcases/ai",
        json={
            "kind": "convert",
            "business_project_id": bp,
            "suite_id": suite["id"],
            "case_ids": [steps["id"]],
        },
        headers=admin,
    )
    assert missing.status_code == 422  # no direction
    asked = client.post(
        f"{API}/testcases/ai",
        json={
            "kind": "convert",
            "business_project_id": bp,
            "suite_id": suite["id"],
            "case_ids": [steps["id"], other["id"]],
            "direction": "to_bdd",
        },
        headers=admin,
    )
    assert asked.status_code == 201, asked.text
    run = asked.json()
    row = db_session.get(TestAiRun, run["id"])
    assert row is not None
    answer = {
        "summary": "Đã chuyển",
        "proposals": [
            {
                "code": steps["key"],
                "bdd": "Scenario: Đăng nhập\n  When nhập đúng\n  Then vào trang chủ",
            },
            {"code": other["key"], "bdd": "Scenario: hỏng\n  Givn"},  # dropped: does not parse
            {"code": "C999999", "bdd": "Scenario: x\n  Given y"},  # not asked for
        ],
    }
    provider = FakeLLMProvider(responder=lambda prompt: answer, cost_usd=0.01)
    status = ai.run(
        db_session,
        run["id"],
        row.requested_at,
        web=lambda url: ("", ""),
        gitlab=lambda conn: None,  # type: ignore[arg-type,return-value]
        provider=lambda: provider,
        over_budget=lambda: False,
        audit_cost=lambda cost, model: None,
    )
    assert status == "ready", row.error
    assert f'<case code="{steps["key"]}"' in provider.prompts[0].user
    db_session.expire_all()
    got = client.get(f"{API}/testcases/ai/{run['id']}", headers=admin).json()
    assert got["result"]["proposals"] == [
        {
            "case_id": steps["id"],
            "template": "bdd",
            "bdd": "Scenario: Đăng nhập\n  When nhập đúng\n  Then vào trang chủ",
        }
    ]
    wrong = client.post(
        f"{API}/testcases/ai/{run['id']}/apply-convert",
        json={"case_ids": [other["id"]]},
        headers=admin,
    )
    assert wrong.status_code == 422
    applied = client.post(
        f"{API}/testcases/ai/{run['id']}/apply-convert",
        json={"case_ids": [steps["id"]]},
        headers=admin,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["changed"] == 1 and applied.json()["batch_id"]
    case = client.get(f"{API}/testcases/{steps['id']}", headers=admin).json()
    assert case["template"] == "bdd" and case["revisions"][0]["via"] == "ai"
    op = db_session.get(TestBulkOp, applied.json()["batch_id"])
    assert op is not None and op.kind == "edit" and op.summary.startswith("AI chuyển đổi")
    # The hand-written audit row carries the dự án (the testcase audit trail filters on it).
    logged = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "testcase.ai.convert", AuditLog.target == f"test_ai:{run['id']}"
        )
    )
    assert logged is not None and logged.business_project_id == bp
    undone = client.post(f"{API}/testcases/bulk-ops/{op.id}/undo", headers=admin)
    assert undone.status_code == 200, undone.text
    back = client.get(f"{API}/testcases/{steps['id']}", headers=admin).json()
    assert back["template"] == "steps"
    row2 = db_session.scalar(select(TestCase).where(TestCase.id == steps["id"]))
    assert row2 is not None and content.key("case", row2.id) == steps["key"]


def test_exploratory_result_takes_session_notes_once(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """§15.12 G8: a result with a status takes the session's notes under its comment (within
    the comment limit) and its time; the session pauses; a later result does not repeat them."""
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp, name="Khám phá")
    case = _case(client, admin, suite["id"], template="exploratory", charter="Giỏ hàng")
    run = _run(client, admin, bp, [case["id"]])
    [test] = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=admin).json()["items"]
    path = f"{API}/testcases/tests/{test['id']}"
    started = client.post(f"{path}/session", json={"action": "start"}, headers=admin)
    assert started.status_code == 200
    for kind, text in (("bug", "Lỗi tổng tiền"), ("note", "Mã giảm giá áp hai lần")):
        noted = client.post(
            f"{path}/session/notes", json={"kind": kind, "text": text}, headers=admin
        )
        assert noted.status_code == 200, noted.text

    first = client.post(
        f"{path}/results", json={"status": "passed", "comment": "Ổn"}, headers=admin
    )
    assert first.status_code == 201, first.text
    body = first.json()["results"][0]  # newest first
    assert body["comment"].startswith("Ổn\n\nPhiên exploratory")
    assert "Lỗi tổng tiền" in body["comment"] and "Mã giảm giá áp hai lần" in body["comment"]
    assert body["elapsed_s"] is not None
    state = first.json()["session"]
    assert state["running"] is False and state["taken"] == 2

    second = client.post(
        f"{path}/results", json={"status": "failed", "comment": "Lần hai"}, headers=admin
    )
    assert second.status_code == 201, second.text
    assert "Lỗi tổng tiền" not in second.json()["results"][0]["comment"]

    # A long comment: the comment stays whole, the summary is cut to the limit.
    resumed = client.post(f"{path}/session", json={"action": "resume"}, headers=admin)
    assert resumed.status_code == 200
    client.post(f"{path}/session/notes", json={"kind": "note", "text": "y" * 2000}, headers=admin)
    long = client.post(
        f"{path}/results", json={"status": "passed", "comment": "z" * 9_000}, headers=admin
    )
    assert long.status_code == 201, long.text
    kept = long.json()["results"][0]["comment"]
    assert len(kept) <= 10_000 and kept.startswith("z" * 9_000) and kept.endswith("...")
