"""docs/specs/21 phase B4: the Admin endpoints on admin.* permissions, and the hard rules of
§3 a delegated admin.* right cannot get around."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import PermissionSet, PermissionSetItem, User, UserPermissionSet
from tests.api.conftest import World

pytestmark = pytest.mark.integration

API = "/api/v1"


def give(session: Session, user: User, *keys: str) -> None:
    """A custom set holding these flag permissions, given to `user` by hand."""
    custom = PermissionSet(name=f"Bộ {'+'.join(keys)} {user.id}", builtin=False)
    session.add(custom)
    session.flush()
    session.add_all(
        [PermissionSetItem(set_id=custom.id, permission=k, scope="flag") for k in keys]
        + [UserPermissionSet(user_id=user.id, set_id=custom.id)]
    )
    session.flush()


def test_admin_projects_creates_a_du_an_but_not_users(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    give(db_session, world.an, "admin.projects")
    res = client.post(f"{API}/business-projects", json={"name": "Mới"}, headers=as_user(world.an))
    assert res.status_code == 201, res.text
    assert client.get(f"{API}/projects", headers=as_user(world.an)).status_code == 200
    res = client.post(
        f"{API}/users",
        json={"full_name": "X", "primary_email": "x@x.vn"},
        headers=as_user(world.an),
    )
    assert res.status_code == 403
    assert client.get(f"{API}/connections", headers=as_user(world.an)).status_code == 403


def test_admin_projects_cannot_change_its_own_grant(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    give(db_session, world.an, "admin.projects")
    bp_id = world.project.business_project_id
    res = client.put(
        f"{API}/business-projects/{bp_id}/grants",
        json={"grants": [{"user_id": world.an.id, "role": "manage"}]},
        headers=as_user(world.an),
    )
    assert res.status_code == 403
    # Others' grants are theirs to set, as long as their own stays as it was.
    res = client.put(
        f"{API}/business-projects/{bp_id}/grants",
        json={
            "grants": [
                {"user_id": world.an.id, "role": "view"},
                {"user_id": world.binh.id, "role": "manage"},
            ]
        },
        headers=as_user(world.an),
    )
    assert res.status_code == 200, res.text


def test_admin_people_cannot_touch_an_admin(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    give(db_session, world.an, "admin.people")
    who = as_user(world.an)
    assert client.get(f"{API}/users", headers=who).status_code == 200
    res = client.patch(f"{API}/users/{world.admin.id}", json={"active": False}, headers=who)
    assert res.status_code == 403
    assert client.delete(f"{API}/users/{world.admin.id}", headers=who).status_code == 403
    assert client.delete(f"{API}/users/{world.admin.id}/avatar", headers=who).status_code == 403
    db_session.refresh(world.admin)
    assert world.admin.active is not False
    # Nor mint one, nor grow their own rights.
    res = client.post(
        f"{API}/users", json={"full_name": "A2", "primary_email": "admin@x.vn"}, headers=who
    )
    assert res.status_code == 403
    res = client.patch(f"{API}/users/{world.an.id}", json={"manage_all": True}, headers=who)
    assert res.status_code == 403
    res = client.put(
        f"{API}/teams/{world.backend.id}/members",
        json={"user_id": world.an.id, "role": "lead", "joined_at": "2025-01-01"},
        headers=who,
    )
    assert res.status_code == 403
    # A non-admin is still theirs to manage.
    res = client.patch(f"{API}/users/{world.binh.id}", json={"active": False}, headers=who)
    assert res.status_code == 200, res.text


def test_the_last_active_admin_stays(client: TestClient, world: World, as_user: Any) -> None:
    res = client.patch(
        f"{API}/users/{world.admin.id}", json={"active": False}, headers=as_user(world.admin)
    )
    assert res.status_code == 409


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/connections"),
        ("get", "/business-projects"),
        ("get", "/projects"),
        ("get", "/users"),
        ("get", "/teams"),
        ("get", "/accounts"),
        ("get", "/identity/unmapped"),
        ("get", "/admin/llm-costs"),
        ("get", "/admin/llm-token"),
        ("get", "/admin/review-settings"),
        ("get", "/admin/insights/backfill"),
        ("get", "/admin/purge"),
    ],
)
def test_members_still_get_403(
    client: TestClient, world: World, as_user: Any, method: str, path: str
) -> None:
    for who in (world.an, world.lead, world.manager):
        res = getattr(client, method)(f"{API}{path}", headers=as_user(who))
        assert res.status_code == 403, (path, who.full_name)
    res = getattr(client, method)(f"{API}{path}", headers=as_user(world.admin))
    assert res.status_code != 403


def test_a_lead_still_syncs_a_repo_they_see(client: TestClient, world: World, as_user: Any) -> None:
    path = f"{API}/projects/{world.project.id}/sync"
    assert client.post(path, headers=as_user(world.lead)).status_code == 202
    assert client.post(path, headers=as_user(world.an)).status_code == 403
