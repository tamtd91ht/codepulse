"""Bulk changes of cases (docs/specs/30 §15.10, §15.11, G10): the new actions, copy, find and
replace, undo, the AI duplicates, permissions, and 200 cases at once."""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PermissionSet,
    PermissionSetItem,
    TestAiRun,
    TestBulkOp,
    TestCase,
    TestField,
    TestProjectSettings,
    TestResult,
    TestRun,
    TestRunTest,
    TestSuite,
    User,
    UserPermissionSet,
)
from app.review.providers.fake import FakeLLMProvider
from app.testcases import ai
from app.testcases import bulk as bulk_ops
from app.testcases.bulk import regex_problem
from tests.api.conftest import World

API = "/api/v1"


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _suite(client: TestClient, headers: dict[str, str], bp: int, name: str = "Đăng nhập") -> Any:
    got = client.post(
        f"{API}/testcases/suites", json={"business_project_id": bp, "name": name}, headers=headers
    )
    assert got.status_code == 201, got.text
    return got.json()


def _case(client: TestClient, headers: dict[str, str], suite: int, **values: Any) -> Any:
    got = client.post(
        f"{API}/testcases",
        json={"suite_id": suite, "title": "Đăng nhập đúng", **values},
        headers=headers,
    )
    assert got.status_code == 201, got.text
    return got.json()


def _get(client: TestClient, headers: dict[str, str], case_id: int) -> Any:
    got = client.get(f"{API}/testcases/{case_id}", headers=headers)
    assert got.status_code == 200, got.text
    return got.json()


def _bulk(client: TestClient, headers: dict[str, str], ids: list[int], **body: Any) -> Any:
    got = client.post(f"{API}/testcases/bulk", json={"ids": ids, **body}, headers=headers)
    assert got.status_code == 200, got.text
    return got.json()


def _undo(client: TestClient, headers: dict[str, str], op_id: int) -> Any:
    got = client.post(f"{API}/testcases/bulk-ops/{op_id}/undo", headers=headers)
    assert got.status_code == 200, got.text
    return got.json()


def _sample(db: Session, user: User, role: str) -> None:
    """Assign the migration's sample set "Testcase - <role>" to `user`."""
    set_id = db.scalar(select(PermissionSet.id).where(PermissionSet.name == f"Testcase - {role}"))
    assert set_id is not None, role
    db.add(UserPermissionSet(user_id=user.id, set_id=set_id))
    db.flush()


def test_new_actions_record_an_op_and_undo(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    one = _case(client, admin, suite["id"], tags=["smoke", "ui"], jira_keys=["OMX-1"])
    two = _case(client, admin, suite["id"], title="Sai mật khẩu", tags=["ui"])
    ids = [one["id"], two["id"]]

    tagged = _bulk(client, admin, ids, action="tags", tags_add=["regression"], tags_remove=["UI"])
    assert tagged["changed"] == 2 and tagged["batch_id"] and tagged["skipped"] == []
    assert _get(client, admin, one["id"])["tags"] == ["smoke", "regression"]
    got = _get(client, admin, two["id"])
    assert got["tags"] == ["regression"] and got["revision"] == 2
    assert got["revisions"][0]["via"] == "bulk"

    kind = _bulk(client, admin, ids, action="kind", kind="automated")
    assert kind["changed"] == 2
    estimate = _bulk(client, admin, ids, action="estimate", estimate="1h 30m")
    assert estimate["changed"] == 2 and _get(client, admin, one["id"])["estimate_s"] == 5400
    jira = _bulk(client, admin, ids, action="jira", jira_add=["omx-7", "rác"])
    assert jira["changed"] == 2
    assert _get(client, admin, one["id"])["jira_keys"] == ["OMX-1", "OMX-7"]
    again = _bulk(client, admin, ids, action="jira", jira_add=["OMX-7"])
    assert again == {"changed": 0, "batch_id": None, "skipped": []}  # nothing to undo
    bad = client.post(
        f"{API}/testcases/bulk",
        json={"ids": ids, "action": "estimate", "estimate": "mãi mãi"},
        headers=admin,
    )
    assert bad.status_code == 422

    ops = client.get(
        f"{API}/testcases/bulk-ops", params={"business_project_id": bp}, headers=admin
    ).json()
    assert [o["id"] for o in ops][:4] == [
        jira["batch_id"],
        estimate["batch_id"],
        kind["batch_id"],
        tagged["batch_id"],
    ]
    assert ops[0]["kind"] == "edit" and ops[0]["count"] == 2 and ops[0]["can_undo"] is True

    # Undo the last one: the Jira keys go back; the one before is still applied.
    undone = _undo(client, admin, jira["batch_id"])
    assert undone == {"restored": 2, "deleted": 0, "skipped": []}
    after = _get(client, admin, one["id"])
    assert after["jira_keys"] == ["OMX-1"] and after["estimate_s"] == 5400
    assert after["revisions"][0]["via"] == "restore"
    twice = client.post(f"{API}/testcases/bulk-ops/{jira['batch_id']}/undo", headers=admin)
    assert twice.status_code == 409
    listed = client.get(
        f"{API}/testcases/bulk-ops", params={"business_project_id": bp}, headers=admin
    ).json()
    assert listed[0]["undone_at"] and listed[0]["can_undo"] is False

    # An older op: the cases changed since (by the later ops) are skipped and reported.
    stale = _undo(client, admin, tagged["batch_id"])
    assert stale["restored"] == 0 and len(stale["skipped"]) == 2
    assert stale["skipped"][0]["key"] == f"C{one['id']}"
    assert "sửa sau" in stale["skipped"][0]["reason"]


def test_undo_skips_a_case_edited_since_and_old_status_ops(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    one = _case(client, admin, suite["id"])
    two = _case(client, admin, suite["id"], title="Hai")
    done = _bulk(client, admin, [one["id"], two["id"]], action="priority", priority="critical")
    edited = client.patch(
        f"{API}/testcases/{two['id']}", json={"revision": 2, "title": "Hai (sửa)"}, headers=admin
    )
    assert edited.status_code == 200, edited.text
    undone = _undo(client, admin, done["batch_id"])
    assert undone["restored"] == 1
    assert [s["case_id"] for s in undone["skipped"]] == [two["id"]]
    assert _get(client, admin, one["id"])["priority"] == "medium"
    kept = _get(client, admin, two["id"])
    assert kept["priority"] == "critical" and kept["title"] == "Hai (sửa)"

    # Status: an undo puts the previous status back.
    status = _bulk(client, admin, [one["id"]], action="status", status="obsolete")
    assert _undo(client, admin, status["batch_id"])["restored"] == 1
    assert _get(client, admin, one["id"])["status"] == "draft"


def test_move_and_undo_restores_suite_and_section(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    other = _suite(client, admin, bp, "Khác")
    folder = client.post(
        f"{API}/testcases/sections", json={"suite_id": suite["id"], "name": "Web"}, headers=admin
    ).json()
    case = _case(client, admin, suite["id"], section_id=folder["id"], automation_id="a.b")
    _case(client, admin, other["id"], title="Có sẵn", automation_id="a.b")
    moved = _bulk(client, admin, [case["id"]], action="move", suite_id=other["id"])
    assert moved["changed"] == 1 and moved["batch_id"]
    got = _get(client, admin, case["id"])
    assert got["suite_id"] == other["id"] and got["section_id"] is None
    assert got["automation_id"] is None  # taken in the target
    noop = _bulk(client, admin, [case["id"]], action="move", suite_id=other["id"])
    assert noop["changed"] == 0 and noop["skipped"][0]["case_id"] == case["id"]

    undone = _undo(client, admin, moved["batch_id"])
    assert undone["restored"] == 1, undone
    back = _get(client, admin, case["id"])
    assert back["suite_id"] == suite["id"] and back["section_id"] == folder["id"]
    assert back["automation_id"] == "a.b"  # free again in its old suite
    ops = client.get(
        f"{API}/testcases/bulk-ops", params={"business_project_id": bp}, headers=admin
    ).json()
    assert ops[0]["kind"] == "move"


def test_copy_then_undo_deletes_copies_without_results(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    other = _suite(client, admin, bp, "Khác")
    folder = client.post(
        f"{API}/testcases/sections", json={"suite_id": other["id"], "name": "Đích"}, headers=admin
    ).json()
    one = _case(
        client,
        admin,
        suite["id"],
        status="approved",
        tags=["smoke"],
        automation_id="x.y",
        steps="1. Mở",
        expected="Thấy",
    )
    two = _case(client, admin, suite["id"], title="Hai")
    _case(client, admin, other["id"], title="Có sẵn", automation_id="x.y")
    copied = _bulk(
        client,
        admin,
        [one["id"], two["id"]],
        action="copy",
        suite_id=other["id"],
        section_id=folder["id"],
    )
    assert copied["changed"] == 2 and copied["batch_id"]
    op = db_session.get(TestBulkOp, copied["batch_id"])
    assert op is not None and op.kind == "copy" and len(op.created_ids) == 2
    first, second = (_get(client, admin, i) for i in op.created_ids)
    assert first["id"] != one["id"] and first["key"] == f"C{first['id']}"
    assert first["suite_id"] == other["id"] and first["section_id"] == folder["id"]
    assert first["status"] == "draft" and first["tags"] == ["smoke"]
    assert first["steps"] == "1. Mở" and first["automation_id"] is None  # taken there
    assert first["source"] == "manual" and first["revision"] == 1
    assert _get(client, admin, one["id"])["status"] == "approved"  # the original is untouched
    wrong = client.post(
        f"{API}/testcases/bulk",
        json={"ids": [one["id"]], "action": "copy", "suite_id": suite["id"], "section_id": 999999},
        headers=admin,
    )
    assert wrong.status_code == 422

    # A copy someone already recorded a result on is kept.
    run = TestRun(business_project_id=bp, name="Run 1")
    db_session.add(run)
    db_session.flush()
    test = TestRunTest(
        run_id=run.id, case_id=second["id"], title="Hai", priority="medium", type="functional"
    )
    db_session.add(test)
    db_session.flush()
    db_session.add(TestResult(test_id=test.id, status="passed"))
    db_session.flush()
    undone = _undo(client, admin, copied["batch_id"])
    assert undone["deleted"] == 1 and undone["restored"] == 0
    assert [s["case_id"] for s in undone["skipped"]] == [second["id"]]
    assert client.get(f"{API}/testcases/{first['id']}", headers=admin).status_code == 404
    assert client.get(f"{API}/testcases/{second['id']}", headers=admin).status_code == 200


def test_find_and_replace(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    one = _case(client, admin, suite["id"], title="Đăng nhập bằng Email", expected="Email hợp lệ")
    two = _case(
        client,
        admin,
        suite["id"],
        title="Quên mật khẩu",
        template="steps",
        steps_list=[{"content": "Nhập email", "expected": "Gửi email"}],
    )
    _case(client, admin, suite["id"], title="Không liên quan", notes="email")
    body = {
        "suite_id": suite["id"],
        "find": "email",
        "replace": "thư điện tử",
        "fields": ["title", "steps", "expected"],
        "preview": True,
    }
    preview = client.post(f"{API}/testcases/replace", json=body, headers=admin)
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["total"] == 2
    by_case = {m["case_id"]: m for m in shown["matches"]}
    assert [c["field"] for c in by_case[one["id"]]["changes"]] == ["title", "expected"]
    assert by_case[one["id"]]["changes"][0]["after"] == "Đăng nhập bằng thư điện tử"
    assert [c["field"] for c in by_case[two["id"]]["changes"]] == [
        "steps_list[1].content",
        "steps_list[1].expected",
    ]
    assert _get(client, admin, one["id"])["revision"] == 1  # a preview changes nothing

    sensitive = client.post(
        f"{API}/testcases/replace", json={**body, "case_sensitive": True}, headers=admin
    ).json()
    assert [m["case_id"] for m in sensitive["matches"]] == [two["id"]]  # not "Email"

    applied = client.post(
        f"{API}/testcases/replace", json={**body, "preview": False}, headers=admin
    )
    assert applied.status_code == 200, applied.text
    done = applied.json()
    assert done["changed"] == 2 and done["batch_id"]
    got = _get(client, admin, two["id"])
    assert got["steps_list"] == [{"content": "Nhập thư điện tử", "expected": "Gửi thư điện tử"}]
    assert got["revisions"][0]["via"] == "bulk"
    assert db_session.get(TestBulkOp, done["batch_id"]).kind == "replace"  # type: ignore[union-attr]

    regex = client.post(
        f"{API}/testcases/replace",
        json={**body, "find": r"(Đăng) (nhập)", "replace": r"\2 \1", "regex": True},
        headers=admin,
    ).json()
    assert regex["matches"][0]["changes"][0]["after"].startswith("nhập Đăng")

    assert _undo(client, admin, done["batch_id"])["restored"] == 2
    assert _get(client, admin, one["id"])["title"] == "Đăng nhập bằng Email"

    for find in [r"(a+)+$", r"(a|aa)*", r"(\w)\1", "a" * 201, r"x{5000}", "[", "a*b*c*d*"]:
        refused = client.post(
            f"{API}/testcases/replace", json={**body, "find": find, "regex": True}, headers=admin
        )
        assert refused.status_code == 422, find
    empty_title = client.post(
        f"{API}/testcases/replace",
        json={
            **body,
            "find": ".*",
            "replace": "",
            "regex": True,
            "fields": ["title"],
            "preview": False,
        },
        headers=admin,
    )
    assert empty_title.status_code == 422  # a title cannot become empty
    as_an = client.post(f"{API}/testcases/replace", json=body, headers=as_user(world.an))
    assert as_an.status_code == 403


def test_regex_guard() -> None:
    for ok in [r"foo", r"\bfoo\b", r"(?i)abc", r"(?:ab)+", r"[a-z]+\d{2,4}", r"(a)(b)?", r"x{3}"]:
        assert regex_problem(ok) is None, ok
    for bad in [
        r"(a+)+",
        r"((ab)*)*",
        r"(a|b)+",
        r"(?P<x>a)(?P=x)",
        r"(a)\1",
        r"a{1,5000}",
        r".*.*.*.*",
        r"(abc",
        r"abc)",
    ]:
        assert regex_problem(bad) is not None, bad


def test_undo_permissions_and_expiry(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    _sample(db_session, world.an, "Tester")
    tester = as_user(world.an)
    suite = _suite(client, admin, bp)
    case = _case(client, admin, suite["id"])
    done = _bulk(client, admin, [case["id"]], action="type", type="security")
    other = client.post(f"{API}/testcases/bulk-ops/{done['batch_id']}/undo", headers=tester)
    assert other.status_code == 403  # neither the owner nor tests.manage
    mine = client.get(
        f"{API}/testcases/bulk-ops", params={"business_project_id": bp}, headers=tester
    ).json()
    assert mine == []  # only one's own

    op = db_session.get(TestBulkOp, done["batch_id"])
    assert op is not None
    op.created_at = datetime.now(UTC) - timedelta(hours=25)
    db_session.flush()
    late = client.post(f"{API}/testcases/bulk-ops/{done['batch_id']}/undo", headers=admin)
    assert late.status_code == 409
    assert client.post(f"{API}/testcases/bulk-ops/999999/undo", headers=admin).status_code == 404

    # A Tester edits in bulk but does not delete (tests.delete), nor approve while it is on.
    assert _bulk(client, tester, [case["id"]], action="priority", priority="low")["changed"] == 1
    refused = client.post(
        f"{API}/testcases/bulk", json={"ids": [case["id"]], "action": "delete"}, headers=tester
    )
    assert refused.status_code == 403
    db_session.add(TestProjectSettings(business_project_id=bp, require_approval=True))
    db_session.flush()
    approve = client.post(
        f"{API}/testcases/bulk",
        json={"ids": [case["id"]], "action": "status", "status": "approved"},
        headers=tester,
    )
    assert approve.status_code == 403
    assert _bulk(client, admin, [case["id"]], action="status", status="approved")["changed"] == 1
    # Content changed in bulk by a non-approver: an approved case goes back to review.
    _bulk(client, tester, [case["id"]], action="kind", kind="automated")
    assert _get(client, admin, case["id"])["status"] == "review"
    deleted = _bulk(client, admin, [case["id"]], action="delete")
    assert deleted["changed"] == 1 and deleted["batch_id"] is None


def test_custom_field_action(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    other = _suite(client, admin, bp, "Khác")
    one = _case(client, admin, suite["id"])
    two = _case(client, admin, other["id"], title="Ngoài")
    db_session.add(
        TestField(
            business_project_id=bp,
            entity="case",
            key="browser",
            name="Browser",
            type="dropdown",
            options=["Chrome", "Firefox"],
            suite_ids=[suite["id"]],
        )
    )
    db_session.flush()
    done = _bulk(
        client,
        admin,
        [one["id"], two["id"]],
        action="custom",
        field_key="browser",
        value="Chrome",
    )
    assert done["changed"] == 1 and [s["case_id"] for s in done["skipped"]] == [two["id"]]
    row = db_session.get(TestCase, one["id"])
    assert row is not None
    db_session.refresh(row)
    assert row.custom == {"browser": "Chrome"}
    bad = client.post(
        f"{API}/testcases/bulk",
        json={"ids": [one["id"]], "action": "custom", "field_key": "browser", "value": "IE"},
        headers=admin,
    )
    assert bad.status_code == 422
    unknown = client.post(
        f"{API}/testcases/bulk",
        json={"ids": [one["id"]], "action": "custom", "field_key": "nope", "value": "x"},
        headers=admin,
    )
    assert unknown.status_code == 422
    assert _undo(client, admin, done["batch_id"])["restored"] == 1
    db_session.refresh(row)
    assert row.custom == {}


def test_ai_duplicates_apply_and_undo(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    keep = _case(client, admin, suite["id"], title="Đăng nhập đúng mật khẩu")
    dup = _case(client, admin, suite["id"], title="Đăng nhập với mật khẩu đúng")
    other = _case(client, admin, suite["id"], title="Quên mật khẩu")
    asked = client.post(
        f"{API}/testcases/ai",
        json={"kind": "duplicates", "business_project_id": bp, "suite_id": suite["id"]},
        headers=admin,
    )
    assert asked.status_code == 201, asked.text
    run = asked.json()
    row = db_session.get(TestAiRun, run["id"])
    assert row is not None
    answer = {
        "summary": "Một nhóm trùng",
        "groups": [
            {"keep": keep["key"], "duplicates": [dup["key"], "C999999"], "reason": "cùng ý"},
            {"keep": dup["key"], "duplicates": [other["key"]], "reason": "đã dùng"},
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
    assert f'<case code="{dup["key"]}"' in provider.prompts[0].user
    db_session.expire_all()
    got = client.get(f"{API}/testcases/ai/{run['id']}", headers=admin).json()
    assert got["result"]["groups"] == [
        {"keep": keep["id"], "duplicates": [dup["id"]], "reason": "cùng ý"}
    ]
    outside = client.post(
        f"{API}/testcases/ai/{run['id']}/apply-duplicates",
        json={"groups": [{"keep": keep["id"], "duplicates": [other["id"]]}]},
        headers=admin,
    )
    assert outside.status_code == 422
    applied = client.post(
        f"{API}/testcases/ai/{run['id']}/apply-duplicates",
        json={"groups": [{"keep": keep["id"], "duplicates": [dup["id"]]}]},
        headers=admin,
    )
    assert applied.status_code == 200, applied.text
    done = applied.json()
    assert done["changed"] == 1 and done["batch_id"]
    marked = _get(client, admin, dup["id"])
    assert marked["status"] == "obsolete" and marked["revisions"][0]["via"] == "ai"
    assert db_session.get(TestBulkOp, done["batch_id"]).kind == "duplicates"  # type: ignore[union-attr]
    assert _undo(client, admin, done["batch_id"])["restored"] == 1
    assert _get(client, admin, dup["id"])["status"] == "draft"


def test_two_hundred_cases_in_one_go(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = TestSuite(business_project_id=bp, name="Lớn", position=1)
    db_session.add(suite)
    db_session.flush()
    cases = [
        TestCase(suite_id=suite.id, title=f"Testcase {n}", position=n, tags=["a"])
        for n in range(200)
    ]
    db_session.add_all(cases)
    db_session.flush()
    ids = [c.id for c in cases]
    for body in (
        {"action": "tags", "tags_add": ["b"]},
        {"action": "status", "status": "obsolete"},
        {"action": "priority", "priority": "high"},
    ):
        started = time.monotonic()
        done = _bulk(client, admin, ids, **body)
        assert time.monotonic() - started < 5.0, body
        assert done["changed"] == 200
    started = time.monotonic()
    undone = _undo(client, admin, done["batch_id"])
    assert time.monotonic() - started < 5.0 and undone["restored"] == 200


def test_replace_redos_is_refused_quickly(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    _case(client, admin, suite["id"], notes="a" * 5000)
    for find in ["a*a*a*b", ".*.*.*x"]:
        started = time.monotonic()
        got = client.post(
            f"{API}/testcases/replace",
            json={
                "suite_id": suite["id"],
                "find": find,
                "replace": "",
                "fields": ["notes"],
                "regex": True,
            },
            headers=admin,
        )
        assert got.status_code == 422, find
        assert time.monotonic() - started < 4.0, find


def test_replacer_stops_a_slow_match(monkeypatch: Any) -> None:
    # Whatever the guard lets through runs under the time budget (the `regex` package's timeout).
    monkeypatch.setattr(bulk_ops, "REGEX_BUDGET_S", 1e-6)
    sub = bulk_ops.replacer(r"\w+\s?", "x", regex=True, case_sensitive=False)
    started = time.monotonic()
    with pytest.raises(HTTPException) as caught:
        sub("an apple a day " * 50_000)
    assert caught.value.status_code == 422 and "quá chậm" in str(caught.value.detail)
    assert time.monotonic() - started < 4.0
    plain = bulk_ops.replacer("a.b", "-", regex=False, case_sensitive=True)
    assert plain("a.b axb") == ("- axb", 1)  # plain text is a literal


def test_replace_preview_is_bounded(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    long = _case(client, admin, suite["id"], notes="x" * 3000 + "email" + "y" * 3000)
    many = _case(
        client,
        admin,
        suite["id"],
        title="Nhiều bước",
        template="steps",
        steps_list=[{"content": f"Nhập email {n}", "expected": ""} for n in range(12)],
    )
    body = {
        "suite_id": suite["id"],
        "find": "email",
        "replace": "thư",
        "fields": ["notes", "steps"],
    }
    shown = client.post(f"{API}/testcases/replace", json=body, headers=admin).json()
    assert shown["total"] == 2 and shown["truncated"] is True
    by_case = {m["case_id"]: m for m in shown["matches"]}
    [change] = by_case[long["id"]]["changes"]
    assert change["before"] == "..." + "x" * 80 + "email" + "y" * 80 + "..."
    assert change["after"] == "..." + "x" * 80 + "thư" + "y" * 80 + "..."
    assert len(by_case[many["id"]]["changes"]) == bulk_ops.PREVIEW_CHANGES_MAX

    small = client.post(
        f"{API}/testcases/replace", json={**body, "fields": ["notes"]}, headers=admin
    ).json()
    assert small["truncated"] is False

    # Applying still replaces everything.
    done = client.post(f"{API}/testcases/replace", json={**body, "preview": False}, headers=admin)
    assert done.status_code == 200, done.text
    got = _get(client, admin, many["id"])
    assert all("thư" in s["content"] for s in got["steps_list"])
    assert _get(client, admin, long["id"])["notes"] == "x" * 3000 + "thư" + "y" * 3000


def test_copy_keeps_custom_values_the_target_accepts(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    other = _suite(client, admin, bp, "Khác")
    db_session.add_all(
        [
            TestField(
                business_project_id=bp,
                entity="case",
                key="browser",
                name="Browser",
                type="dropdown",
                options=["Chrome", "Firefox"],
                suite_ids=[suite["id"]],
            ),
            TestField(business_project_id=bp, entity="case", key="area", name="Area", type="text"),
        ]
    )
    db_session.flush()
    case = _case(client, admin, suite["id"], custom={"browser": "Chrome", "area": "Web"})
    row = db_session.get(TestCase, case["id"])
    assert row is not None
    row.custom = {**row.custom, "gone": "x"}  # a key no field has (any more)
    db_session.flush()
    copied = _bulk(client, admin, [case["id"]], action="copy", suite_id=other["id"])
    op = db_session.get(TestBulkOp, copied["batch_id"])
    assert op is not None
    copy = db_session.get(TestCase, op.created_ids[0])
    assert copy is not None and copy.custom == {"area": "Web"}  # browser is not used there
    same = _bulk(client, admin, [case["id"]], action="copy", suite_id=suite["id"])
    again = db_session.get(TestBulkOp, same["batch_id"])
    assert again is not None
    twin = db_session.get(TestCase, again.created_ids[0])
    assert twin is not None and twin.custom == {"browser": "Chrome", "area": "Web"}


def test_undo_copy_skips_edited_copies_and_needs_delete_for_others(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    suite = _suite(client, admin, bp)
    one = _case(client, admin, suite["id"])
    two = _case(client, admin, suite["id"], title="Hai")

    # The maker's own undo: a copy edited since is kept and reported.
    copied = _bulk(client, admin, [one["id"], two["id"]], action="copy", suite_id=suite["id"])
    op = db_session.get(TestBulkOp, copied["batch_id"])
    assert op is not None
    first, second = op.created_ids
    edited = client.patch(
        f"{API}/testcases/{second}", json={"revision": 1, "title": "Hai (sửa)"}, headers=admin
    )
    assert edited.status_code == 200, edited.text
    undone = _undo(client, admin, copied["batch_id"])
    assert undone["deleted"] == 1
    assert [(s["case_id"], s["reason"]) for s in undone["skipped"]] == [
        (second, "đã sửa sau khi sao chép")
    ]
    assert client.get(f"{API}/testcases/{first}", headers=admin).status_code == 404

    # A manager (tests.manage) without tests.delete undoing someone else's copy: kept.
    manager = PermissionSet(name="Quản lý test không xoá", builtin=False)
    db_session.add(manager)
    db_session.flush()
    db_session.add_all(
        [
            PermissionSetItem(set_id=manager.id, permission=p, scope="all")
            for p in ("tests.view", "tests.edit", "tests.manage")
        ]
    )
    db_session.add(UserPermissionSet(user_id=world.an.id, set_id=manager.id))
    db_session.flush()
    by_admin = _bulk(client, admin, [one["id"]], action="copy", suite_id=suite["id"])
    other = _undo(client, as_user(world.an), by_admin["batch_id"])
    assert other["deleted"] == 0 and "tests.delete" in other["skipped"][0]["reason"]
