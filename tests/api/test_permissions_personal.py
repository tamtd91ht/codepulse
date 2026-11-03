"""docs/specs/21 §9: permissions ticked for one person (their own set) and custom sets given
from their profile."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PermissionSet, PermissionSetItem, UserPermissionSet
from tests.api.conftest import World

pytestmark = pytest.mark.integration


def me(client: TestClient, as_user: Any, who: Any) -> dict[str, str]:
    return dict(client.get("/api/v1/auth/me", headers=as_user(who)).json()["permissions"])


def test_an_admin_ticks_permissions_for_one_person(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    url = f"/api/v1/admin/users/{world.an.id}/own-permissions"
    assert client.get(url, headers=admin).json() == {}
    assert "insights.view" not in me(client, as_user, world.an)
    # A sensitive one needs confirming.
    res = client.put(url, json={"items": {"evaluation.view": "all"}}, headers=admin)
    assert res.status_code == 422
    res = client.put(
        url,
        json={
            "items": {"insights.view": "flag", "evaluation.view": "all"},
            "confirm_sensitive": True,
        },
        headers=admin,
    )
    assert res.status_code == 200, res.text
    assert any(s["via"] == "personal" for s in res.json()["sets"])
    held = me(client, as_user, world.an)
    assert held["insights.view"] == "flag" and held["evaluation.view"] == "all"
    assert client.get(url, headers=admin).json() == {
        "insights.view": "flag",
        "evaluation.view": "all",
    }
    # The own set is not one of the sets to manage.
    listed = client.get("/api/v1/admin/permission-sets", headers=admin).json()
    assert not any(s["name"].startswith("Quyền riêng") for s in listed)
    personal = db_session.scalars(
        select(PermissionSet).where(PermissionSet.personal_user_id == world.an.id)
    ).one()
    assert (
        client.patch(
            f"/api/v1/admin/permission-sets/{personal.id}", json={"name": "x"}, headers=admin
        ).status_code
        == 404
    )
    # Untick everything: the own set goes.
    assert client.put(url, json={"items": {}}, headers=admin).status_code == 200
    assert (
        db_session.scalars(
            select(PermissionSet).where(PermissionSet.personal_user_id == world.an.id)
        ).first()
        is None
    )
    assert "insights.view" not in me(client, as_user, world.an)


def test_invalid_items_and_admin_targets(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    bad = client.put(
        f"/api/v1/admin/users/{world.an.id}/own-permissions",
        json={"items": {"dispute.raise": "all"}},  # not a scope it has
        headers=admin,
    )
    assert bad.status_code == 422
    assert (
        client.put(
            f"/api/v1/admin/users/{world.admin.id}/own-permissions",
            json={"items": {"insights.view": "flag"}},
            headers=admin,
        ).status_code
        == 409
    )


def test_a_delegated_permissions_admin_ticks_only_what_they_hold(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    delegate = PermissionSet(name="Phân quyền viên", builtin=False)
    db_session.add(delegate)
    db_session.flush()
    db_session.add_all(
        [
            PermissionSetItem(set_id=delegate.id, permission="admin.permissions", scope="flag"),
            UserPermissionSet(user_id=world.lead.id, set_id=delegate.id),
        ]
    )
    db_session.flush()
    lead = as_user(world.lead)
    mine = f"/api/v1/admin/users/{world.lead.id}/own-permissions"
    assert (
        client.put(mine, json={"items": {"insights.view": "flag"}}, headers=lead).status_code == 403
    )
    theirs = f"/api/v1/admin/users/{world.an.id}/own-permissions"
    # The lead holds insights.view (Lead set): may tick it; not admin.purge they lack.
    assert (
        client.put(theirs, json={"items": {"insights.view": "flag"}}, headers=lead).status_code
        == 200
    )
    assert (
        client.put(theirs, json={"items": {"admin.purge": "flag"}}, headers=lead).status_code == 403
    )
    assert (
        client.put(theirs, json={"items": {"admin.permissions": "flag"}}, headers=lead).status_code
        == 403
    )
    # A member has no access at all.
    assert client.get(theirs, headers=as_user(world.binh)).status_code == 403


def test_custom_sets_given_from_the_profile(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    custom = PermissionSet(name="Xem Luồng làm việc", builtin=False)
    db_session.add(custom)
    db_session.flush()
    db_session.add(PermissionSetItem(set_id=custom.id, permission="insights.view", scope="flag"))
    db_session.flush()
    admin = as_user(world.admin)
    url = f"/api/v1/admin/users/{world.an.id}/permission-sets"
    res = client.put(url, json={"set_ids": [custom.id]}, headers=admin)
    assert res.status_code == 200, res.text
    assert me(client, as_user, world.an)["insights.view"] == "flag"
    lead_set = db_session.scalars(
        select(PermissionSet).where(PermissionSet.auto_role == "lead")
    ).one()
    assert client.put(url, json={"set_ids": [lead_set.id]}, headers=admin).status_code == 409
    assert client.put(url, json={"set_ids": []}, headers=admin).status_code == 200
    assert "insights.view" not in me(client, as_user, world.an)
