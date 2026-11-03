"""docs/specs/21 §3: a delegated admin right (a non-admin holding admin.*) never reaches above
itself. Regression tests for the security review of the permission sets."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import (
    Account,
    PermissionSet,
    PermissionSetItem,
    Project,
    ProjectGrant,
    User,
    UserPermissionSet,
)
from tests.api.conftest import World

pytestmark = pytest.mark.integration


def give(session: Session, user: User, items: dict[str, str]) -> PermissionSet:
    custom = PermissionSet(name=f"Bộ {sorted(items)} {user.id}", builtin=False)
    session.add(custom)
    session.flush()
    session.add_all(
        [PermissionSetItem(set_id=custom.id, permission=k, scope=v) for k, v in items.items()]
        + [UserPermissionSet(user_id=user.id, set_id=custom.id)]
    )
    session.flush()
    return custom


def test_the_gitlab_sign_in_account_is_moved_by_admins_only(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    give(db_session, world.binh, {"admin.people": "flag"})
    login = Account(kind="gitlab_user", external_key="555", user_id=world.binh.id)
    email = Account(kind="git_email", external_key="binh@home", user_id=world.binh.id)
    db_session.add_all([login, email])
    db_session.flush()
    binh = as_user(world.binh)
    # Moving one's own sign-in onto someone else would log in as that person.
    moved = client.put(
        f"/api/v1/accounts/{login.id}/user", json={"user_id": world.manager.id}, headers=binh
    )
    assert moved.status_code == 403
    # Nor any account of one's own, nor onto someone holding power.
    world.outsider.manage_all = True
    db_session.flush()
    for target in (world.outsider.id,):
        res = client.put(
            f"/api/v1/accounts/{email.id}/user", json={"user_id": target}, headers=binh
        )
        assert res.status_code == 403
    assert (
        client.put(
            f"/api/v1/accounts/{login.id}/user",
            json={"user_id": world.manager.id},
            headers=as_user(world.admin),
        ).status_code
        == 200
    )


def test_a_delegated_people_admin_never_widens_their_own_team(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    give(db_session, world.lead, {"admin.people": "flag"})
    lead = as_user(world.lead)
    body = {"user_id": world.admin.id, "role": "member", "joined_at": "2025-01-01"}
    # Adding an admin (or anyone) to the team one leads would open their per-person data.
    res = client.put(f"/api/v1/teams/{world.backend.id}/members", json=body, headers=lead)
    assert res.status_code == 403
    body["user_id"] = world.outsider.id
    res = client.put(f"/api/v1/teams/{world.backend.id}/members", json=body, headers=lead)
    assert res.status_code == 403
    # A sub-team of one's own team widens it too.
    sub = client.post(
        "/api/v1/teams", json={"name": "Backend con", "parent_id": world.backend.id}, headers=lead
    )
    assert sub.status_code == 403
    # Another team, with an ordinary person: allowed.
    body["user_id"] = world.binh.id
    res = client.put(f"/api/v1/teams/{world.mobile.id}/members", json=body, headers=lead)
    assert res.status_code == 204


def test_a_delegated_projects_admin_moves_no_repo_into_their_own_du_an(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    give(db_session, world.binh, {"admin.projects": "flag"})
    loose = Project(
        connection_id=world.project.connection_id, external_id=9191, path_with_namespace="x/hidden"
    )
    db_session.add(loose)
    db_session.flush()
    res = client.put(
        "/api/v1/projects/business-project",
        json={"project_ids": [loose.id], "business_project_id": world.project.business_project_id},
        headers=as_user(world.binh),
    )
    assert res.status_code == 403  # binh holds a grant on that dự án


def test_a_delegated_admin_hands_out_neither_manage_all_nor_manage(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    give(db_session, world.binh, {"admin.people": "flag", "admin.projects": "flag"})
    binh = as_user(world.binh)
    res = client.patch(f"/api/v1/users/{world.an.id}", json={"manage_all": True}, headers=binh)
    assert res.status_code == 403
    bp = world.project.business_project_id
    grants = client.get(f"/api/v1/business-projects/{bp}/grants", headers=binh).json()
    wanted = [{"user_id": g["user_id"], "role": g["role"]} for g in grants]
    for g in wanted:
        if g["user_id"] == world.an.id:
            g["role"] = "manage"  # binh only views this dự án
    res = client.put(
        f"/api/v1/business-projects/{bp}/grants", json={"grants": wanted}, headers=binh
    )
    assert res.status_code == 403


def test_a_project_manager_reruns_only_in_a_du_an_they_manage(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.api.access import may_rerun
    from app.api.security import load_principal
    from app.config import get_settings

    db_session.execute(
        update(ProjectGrant).where(ProjectGrant.user_id == world.an.id).values(role="manage")
    )
    db_session.flush()
    an = load_principal(db_session, world.an.id, get_settings())
    assert may_rerun(an, world.an.id, world.project.business_project_id)
    assert not may_rerun(an, world.an.id, None)  # an admin-only repo: not theirs to spend
    assert not may_rerun(an, world.an.id, 999_999)


def test_a_delegated_permissions_admin_takes_away_only_what_they_hold(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    give(db_session, world.binh, {"admin.permissions": "flag"})
    strong = give(db_session, world.an, {"admin.purge": "flag"})
    binh = as_user(world.binh)
    assert (
        client.delete(f"/api/v1/admin/permission-sets/{strong.id}", headers=binh).status_code == 403
    )
    res = client.put(
        f"/api/v1/admin/permission-sets/{strong.id}/users", json={"user_ids": []}, headers=binh
    )
    assert res.status_code == 403
