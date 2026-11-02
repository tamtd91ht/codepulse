"""users.manage_all: below admin, above everyone else — manages every dự án without a grant
(user request 2026-11-02). Set by an admin; no Admin screens."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, BusinessProject, Project
from tests.api.conftest import World

pytestmark = pytest.mark.integration


def test_an_admin_turns_it_on_and_it_is_audited(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    res = client.patch(
        f"/api/v1/users/{world.outsider.id}",
        json={"manage_all": True},
        headers=as_user(world.admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["manage_all"] is True
    listed = {u["id"]: u for u in client.get("/api/v1/users", headers=as_user(world.admin)).json()}
    assert listed[world.outsider.id]["manage_all"] is True
    assert (
        db_session.scalars(select(AuditLog).where(AuditLog.action == "user.update")).first()
        is not None
    )
    # Nobody else may.
    refused = client.patch(
        f"/api/v1/users/{world.an.id}", json={"manage_all": True}, headers=as_user(world.lead)
    )
    assert refused.status_code == 403


def test_it_manages_every_dự_án_without_a_grant_but_is_no_admin(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    other = BusinessProject(name="Ungranted")
    db_session.add(other)
    db_session.flush()
    repo = Project(
        connection_id=world.project.connection_id,
        external_id=3003,
        path_with_namespace="u/repo",
        business_project_id=other.id,
    )
    loose = Project(  # in no dự án: still the admins'
        connection_id=world.project.connection_id, external_id=3004, path_with_namespace="x/y"
    )
    db_session.add_all([repo, loose])
    world.outsider.manage_all = True
    db_session.flush()
    me = as_user(world.outsider)

    who = client.get("/api/v1/auth/me", headers=me).json()
    assert who["manage_all"] is True and who["is_admin"] is False
    assert who["projects"] == {
        str(world.project.business_project_id): "manage",
        str(other.id): "manage",
    }
    repos = {r["path_with_namespace"] for r in client.get("/api/v1/repos", headers=me).json()}
    assert "u/repo" in repos and "x/y" not in repos
    # A manager's view of everyone: another person's page and review.
    assert client.get(f"/api/v1/people/{world.an.id}", headers=me).status_code == 200
    assert client.get(f"/api/v1/reviews/{world.an_review.id}", headers=me).status_code == 200
    # No Admin screens.
    assert client.get("/api/v1/users", headers=me).status_code == 403
    assert client.get("/api/v1/connections", headers=me).status_code == 403


def test_off_by_default(client: TestClient, world: World, as_user: Any) -> None:
    who = client.get("/api/v1/auth/me", headers=as_user(world.an)).json()
    assert who["manage_all"] is False


def test_nobody_turns_it_on_for_themselves_or_through_create(
    client: TestClient, world: World, as_user: Any
) -> None:
    mine = client.patch(
        f"/api/v1/users/{world.an.id}", json={"manage_all": True}, headers=as_user(world.an)
    )
    assert mine.status_code == 403
    created = client.post(
        "/api/v1/users",
        json={"full_name": "New", "primary_email": "new@x.vn", "manage_all": True},
        headers=as_user(world.admin),
    )
    assert created.status_code == 201 and created.json()["manage_all"] is False
    listed = {u["id"]: u for u in client.get("/api/v1/users", headers=as_user(world.admin)).json()}
    assert listed[world.admin.id]["is_admin"] is True and listed[world.an.id]["is_admin"] is False


def test_only_an_admin_grants_or_removes_it_not_a_quan_ly_tong(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    world.outsider.manage_all = True
    db_session.flush()
    me = as_user(world.outsider)
    give = client.patch(f"/api/v1/users/{world.an.id}", json={"manage_all": True}, headers=me)
    drop = client.patch(
        f"/api/v1/users/{world.outsider.id}", json={"manage_all": False}, headers=me
    )
    assert (give.status_code, drop.status_code) == (403, 403)
    # Nor through a dự án's grants (admin screens).
    grants = client.put(
        f"/api/v1/business-projects/{world.project.business_project_id}/grants",
        json={"grants": []},
        headers=me,
    )
    assert grants.status_code == 403
    db_session.refresh(world.an)
    assert world.an.manage_all is False
