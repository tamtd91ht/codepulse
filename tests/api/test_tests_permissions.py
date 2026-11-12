"""The Testcase module's own permissions (docs/specs/30 §2, user 2026-11-12): tests.view reads,
tests.edit designs cases, tests.run records results of any test, tests.manage runs the runs;
each one alone gives exactly its part."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    PermissionSet,
    PermissionSetItem,
    TestCase,
    TestSuite,
    UserPermissionSet,
)
from app.permissions.catalog import BUILTIN_SETS, BY_KEY
from tests.api.conftest import World

API = "/api/v1"


def _give(db: Session, user_id: int, items: dict[str, str]) -> None:
    row = PermissionSet(name=f"tests {user_id} {'-'.join(sorted(items))}", builtin=False)
    db.add(row)
    db.flush()
    db.add_all(
        [PermissionSetItem(set_id=row.id, permission=k, scope=v) for k, v in items.items()]
        + [UserPermissionSet(user_id=user_id, set_id=row.id)]
    )
    db.flush()


def _suite(db: Session, bp: int) -> tuple[TestSuite, TestCase]:
    suite = TestSuite(business_project_id=bp, name="Quyền", position=1)
    db.add(suite)
    db.flush()
    case = TestCase(suite_id=suite.id, title="Đăng nhập", position=1)
    db.add(case)
    db.flush()
    return suite, case


def test_catalog_and_defaults() -> None:
    assert BY_KEY["tests.view"].is_flag
    for key in ("tests.edit", "tests.delete", "tests.approve", "tests.run", "tests.manage"):
        assert BY_KEY[key].scopes == ("project", "all")
    by_role = {s.auto_role: s.items for s in BUILTIN_SETS}
    assert by_role["member"]["tests.view"] == "flag" and "tests.edit" not in by_role["member"]
    assert by_role["project_manager"]["tests.manage"] == "project"
    assert by_role["project_manager"]["tests.delete"] == "project"
    assert by_role["project_manager"]["tests.approve"] == "project"
    assert "tests.delete" not in by_role["member"] and "tests.approve" not in by_role["member"]
    assert by_role["manage_all"]["tests.run"] == "all"


def test_each_permission_gives_its_part(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    suite, case = _suite(db_session, bp)
    admin = as_user(world.admin)
    run = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"suite_id": suite.id, "all": True}},
        headers=admin,
    )
    assert run.status_code == 201, run.text
    test_id = client.get(f"{API}/testcases/runs/{run.json()['id']}/tests", headers=admin).json()[
        "items"
    ][0]["id"]

    # A member (tests.view only): reads, nothing more.
    an = as_user(world.an)
    rows = {p["id"]: p for p in client.get(f"{API}/testcases/projects", headers=an).json()}
    assert rows[bp]["can_edit"] is False and rows[bp]["can_manage"] is False
    assert client.get(f"{API}/testcases/runs/{run.json()['id']}", headers=an).status_code == 200
    assert (
        client.post(
            f"{API}/testcases", json={"suite_id": suite.id, "title": "x"}, headers=an
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{API}/testcases/tests/{test_id}/results", json={"status": "passed"}, headers=an
        ).status_code
        == 403
    )

    # tests.run: records results of any test, may not design or manage.
    _give(db_session, world.binh.id, {"tests.run": "all"})
    binh = as_user(world.binh)
    assert (
        client.post(
            f"{API}/testcases/tests/{test_id}/results", json={"status": "failed"}, headers=binh
        ).status_code
        == 201
    )
    assert (
        client.post(f"{API}/testcases/runs/{run.json()['id']}/close", headers=binh).status_code
        == 403
    )
    assert (
        client.patch(
            f"{API}/testcases/{case.id}", json={"revision": 1, "title": "y"}, headers=binh
        ).status_code
        == 403
    )

    # tests.edit: designs cases and shared steps, but runs are not theirs.
    _give(db_session, world.an.id, {"tests.edit": "all"})
    assert (
        client.post(
            f"{API}/testcases", json={"suite_id": suite.id, "title": "Mới"}, headers=an
        ).status_code
        == 201
    )
    shared = client.post(
        f"{API}/testcases/shared-steps",
        json={
            "business_project_id": bp,
            "title": "Đăng nhập",
            "steps": [{"content": "a", "expected": "b"}],
        },
        headers=an,
    )
    assert shared.status_code == 201, shared.text
    assert (
        client.post(
            f"{API}/testcases/runs",
            json={"business_project_id": bp, "selection": {"suite_id": suite.id, "all": True}},
            headers=an,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{API}/testcases/milestones",
            json={"business_project_id": bp, "name": "R1"},
            headers=an,
        ).status_code
        == 403
    )

    # tests.manage: runs, milestones, configurations.
    _give(db_session, world.outsider.id, {"tests.manage": "all"})
    outsider = as_user(world.outsider)
    assert (
        client.post(
            f"{API}/testcases/milestones",
            json={"business_project_id": bp, "name": "R2"},
            headers=outsider,
        ).status_code
        == 201
    )
    assert (
        client.post(f"{API}/testcases/runs/{run.json()['id']}/close", headers=outsider).status_code
        == 200
    )
