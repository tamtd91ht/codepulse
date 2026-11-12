"""Case approval (docs/specs/30 §15.9, §15.11, 30a G7): submit, approve, reject, the review
history, and who may do each with the sample permission sets."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BusinessProject,
    PermissionSet,
    TestCase,
    TestProjectSettings,
    TestSuite,
    User,
    UserPermissionSet,
)
from tests.api.conftest import World

API = "/api/v1"


def _sample(db: Session, user: User, role: str) -> None:
    """Assign the migration's sample set "Testcase - <role>" to `user`."""
    set_id = db.scalar(select(PermissionSet.id).where(PermissionSet.name == f"Testcase - {role}"))
    assert set_id is not None, role
    db.add(UserPermissionSet(user_id=user.id, set_id=set_id))
    db.flush()


def _case(db: Session, bp: int, *, status: str = "draft") -> TestCase:
    suite = TestSuite(business_project_id=bp, name=f"Duyệt {bp}", position=1)
    db.add(suite)
    db.flush()
    case = TestCase(suite_id=suite.id, title="Đăng nhập", position=1, status=status)
    db.add(case)
    db.flush()
    return case


def _approval(db: Session, bp: int, on: bool = True) -> None:
    db.add(TestProjectSettings(business_project_id=bp, require_approval=on))
    db.flush()


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def test_tester_submits_lead_approves(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    _approval(db_session, bp)
    case = _case(db_session, bp)
    _sample(db_session, world.an, "Tester")
    _sample(db_session, world.lead, "Lead")
    tester, lead = as_user(world.an), as_user(world.lead)

    # A Tester may not approve, before or after sending it for review.
    refused = client.post(f"{API}/testcases/{case.id}/approve", json={}, headers=tester)
    assert refused.status_code == 403, refused.text
    sent = client.post(f"{API}/testcases/{case.id}/submit", headers=tester)
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "review"
    assert client.post(f"{API}/testcases/{case.id}/submit", headers=tester).status_code == 409
    assert (
        client.post(f"{API}/testcases/{case.id}/approve", json={}, headers=tester).status_code
        == 403
    )
    assert (
        client.post(
            f"{API}/testcases/{case.id}/reject", json={"comment": "thiếu bước"}, headers=tester
        ).status_code
        == 403
    )

    # The Lead approves; the change is in the case's history.
    ok = client.post(f"{API}/testcases/{case.id}/approve", json={"comment": "ổn"}, headers=lead)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "approved"
    assert [r["snapshot"]["status"] for r in body["revisions"][:2]] == ["review", "draft"]
    assert (
        client.post(f"{API}/testcases/{case.id}/approve", json={}, headers=lead).status_code == 409
    )

    rows = client.get(f"{API}/testcases/{case.id}/reviews", headers=tester)
    assert rows.status_code == 200, rows.text
    got = [(r["decision"], r["comment"], r["user_name"], r["revision"]) for r in rows.json()]
    assert got == [
        ("approve", "ổn", world.lead.full_name, 2),
        ("submit", None, world.an.full_name, 1),
    ]


def test_reject_needs_a_comment_and_returns_to_draft(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    _approval(db_session, bp)
    case = _case(db_session, bp)
    _sample(db_session, world.lead, "Lead")
    lead = as_user(world.lead)
    url = f"{API}/testcases/{case.id}/reject"

    assert client.post(url, json={"comment": "x"}, headers=lead).status_code == 409  # a draft
    assert client.post(f"{API}/testcases/{case.id}/submit", headers=lead).status_code == 200
    assert client.post(url, json={}, headers=lead).status_code == 422
    assert client.post(url, json={"comment": "   "}, headers=lead).status_code == 422
    assert client.post(url, json={"comment": "x" * 2001}, headers=lead).status_code == 422
    done = client.post(url, json={"comment": " thiếu kết quả mong đợi "}, headers=lead)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "draft"
    rows = client.get(f"{API}/testcases/{case.id}/reviews", headers=lead).json()
    assert [(r["decision"], r["comment"]) for r in rows] == [
        ("reject", "thiếu kết quả mong đợi"),
        ("submit", None),
    ]


def test_approval_off_still_works_for_approvers(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    case = _case(db_session, bp)  # no settings row: approval off
    _sample(db_session, world.an, "Tester")
    _sample(db_session, world.lead, "Lead")
    assert (
        client.post(
            f"{API}/testcases/{case.id}/approve", json={}, headers=as_user(world.an)
        ).status_code
        == 403
    )
    ok = client.post(f"{API}/testcases/{case.id}/approve", json={}, headers=as_user(world.lead))
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"

    other = _case(db_session, bp)
    sent = client.post(f"{API}/testcases/{other.id}/submit", headers=as_user(world.an))
    assert sent.status_code == 200 and sent.json()["status"] == "review"


def test_guest_and_developer_may_not_submit(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    case = _case(db_session, bp)
    _sample(db_session, world.binh, "Developer")
    _sample(db_session, world.outsider, "Guest")
    for user in (world.binh, world.outsider):
        headers = as_user(user)
        assert client.post(f"{API}/testcases/{case.id}/submit", headers=headers).status_code == 403
        assert client.get(f"{API}/testcases/{case.id}/reviews", headers=headers).status_code == 200


def test_another_projects_case_is_not_found(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    other = BusinessProject(name="Khác")
    db_session.add(other)
    db_session.flush()
    _approval(db_session, other.id)
    case = _case(db_session, other.id, status="review")
    _sample(db_session, world.lead, "Lead")
    lead = as_user(world.lead)  # sees only Demo
    for method, path, body in (
        ("post", "submit", None),
        ("post", "approve", {}),
        ("post", "reject", {"comment": "x"}),
        ("get", "reviews", None),
    ):
        got = client.request(
            method.upper(), f"{API}/testcases/{case.id}/{path}", json=body, headers=lead
        )
        assert got.status_code == 404, (path, got.text)
    assert client.post(f"{API}/testcases/999999999/submit", headers=lead).status_code == 404


def test_developer_may_not_delete(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    case = _case(db_session, bp)
    _sample(db_session, world.binh, "Developer")
    _sample(db_session, world.lead, "Lead")
    got = client.delete(f"{API}/testcases/{case.id}", headers=as_user(world.binh))
    assert got.status_code == 403, got.text
    assert (
        client.delete(f"{API}/testcases/{case.id}", headers=as_user(world.lead)).status_code == 204
    )


def test_tester_may_edit_but_not_delete(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """tests.edit no longer includes deleting (§15.9): needs the DELETE route's tests.delete."""
    bp = _bp(world)
    case = _case(db_session, bp)
    _sample(db_session, world.an, "Tester")
    tester = as_user(world.an)
    edited = client.patch(
        f"{API}/testcases/{case.id}", json={"revision": 1, "title": "Đăng nhập lại"}, headers=tester
    )
    assert edited.status_code == 200, edited.text
    assert client.delete(f"{API}/testcases/{case.id}", headers=tester).status_code == 403
