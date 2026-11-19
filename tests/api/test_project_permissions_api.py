"""docs/specs/21 §13 (P3): the Manager dự án hands out rights in their dự án, within what they
hold there, to the people of the dự án only."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, User
from tests.api.conftest import World, grant
from tests.api.test_project_admin import empty_project, name_admins

pytestmark = pytest.mark.integration

API = "/api/v1"


def demo(world: World) -> int:
    assert world.project.business_project_id is not None
    return world.project.business_project_id


def staff_demo(client: TestClient, world: World, as_user: Any, db_session: Session) -> int:
    """Demo with An as its Manager dự án, Lead as Lead dự án, Binh and Outsider seeing it."""
    bp = demo(world)
    name_admins(client, world, as_user, bp, an="manager", lead="lead")
    grant(db_session, world.binh, bp)
    grant(db_session, world.outsider, bp)
    return bp


def test_manager_du_an_makes_a_set_within_what_they_hold_and_gives_it(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = staff_demo(client, world, as_user, db_session)
    an = as_user(world.an)
    me = client.get(f"{API}/auth/me", headers=an).json()
    assert me["permit_projects"] == [bp]

    catalog = client.get(f"{API}/business-projects/{bp}/permissions/catalog", headers=an).json()
    flat = {p["key"]: p for g in catalog["groups"] for p in g["permissions"]}
    assert "admin.people" not in flat and "project.permissions" not in flat
    assert flat["review.view"]["grantable"] is True  # Quản lý dự án: review.view at project
    assert flat["dispute.decide"]["grantable"] is False  # nobody gave An that

    url = f"{API}/business-projects/{bp}/permission-sets"
    beyond = client.post(url, json={"name": "QA", "permissions": ["dispute.decide"]}, headers=an)
    assert beyond.status_code == 403
    off = client.post(url, json={"name": "QA", "permissions": ["admin.people"]}, headers=an)
    assert off.status_code == 422
    made = client.post(url, json={"name": "QA", "permissions": ["review.view"]}, headers=an)
    assert made.status_code == 201, made.text
    set_id = made.json()["id"]

    review = f"{API}/reviews/{world.an_review.id}"
    assert client.get(review, headers=as_user(world.binh)).status_code in (403, 404)
    users = f"{url}/{set_id}/users"
    assert client.put(users, json={"user_ids": [world.binh.id]}, headers=an).status_code == 200
    assert client.get(review, headers=as_user(world.binh)).status_code == 200
    binh_me = client.get(f"{API}/auth/me", headers=as_user(world.binh)).json()
    assert binh_me["project_permissions"] == {str(bp): ["review.view"]}

    # Never oneself, never a person without a grant, never an admin.
    assert client.put(users, json={"user_ids": [world.an.id]}, headers=an).status_code == 403
    stranger = User(full_name="Stranger", primary_email="stranger@x.vn")
    db_session.add(stranger)
    db_session.flush()
    assert client.put(users, json={"user_ids": [stranger.id]}, headers=an).status_code == 403
    assert client.put(users, json={"user_ids": [world.admin.id]}, headers=an).status_code == 409

    # Not listed among the system's sets; audited with the dự án.
    listed = client.get(f"{API}/admin/permission-sets", headers=as_user(world.admin)).json()
    assert set_id not in {s["id"] for s in listed}
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "permission_set.assign")
    ).all()
    assert any(r.business_project_id == bp for r in rows)


def test_lead_du_an_and_other_du_an_cannot_hand_out_rights(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = staff_demo(client, world, as_user, db_session)
    other = empty_project(client, world, as_user, "Other")
    name_admins(client, world, as_user, other, binh="manager")
    lead = as_user(world.lead)
    assert (
        client.get(f"{API}/business-projects/{bp}/permission-sets", headers=lead).status_code == 403
    )
    made = client.post(
        f"{API}/business-projects/{bp}/permission-sets",
        json={"name": "S", "permissions": ["review.view"]},
        headers=as_user(world.an),
    ).json()
    # Binh runs Other: Demo's set does not exist there.
    binh = as_user(world.binh)
    assert (
        client.patch(
            f"{API}/business-projects/{other}/permission-sets/{made['id']}",
            json={"name": "X"},
            headers=binh,
        ).status_code
        == 404
    )


def test_ticking_rights_for_one_person_and_the_manager_is_locked(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = staff_demo(client, world, as_user, db_session)
    an = as_user(world.an)
    url = f"{API}/business-projects/{bp}/users/{world.binh.id}/permissions"
    ok = client.put(
        url, json={"set_ids": [], "permissions": ["review.rerun", "tasks.edit"]}, headers=an
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["effective"] == ["review.rerun", "tasks.edit"]
    people = client.get(f"{API}/business-projects/{bp}/permissions/people", headers=an).json()
    rows = {p["user"]["id"]: p for p in people}
    assert rows[world.binh.id]["permissions"] == ["review.rerun", "tasks.edit"]
    assert rows[world.an.id]["locked"] is not None  # oneself
    # Emptied: the person's own set of the dự án goes.
    cleared = client.put(url, json={"set_ids": [], "permissions": []}, headers=an)
    assert cleared.json()["effective"] == []
    # An admin may change the Manager dự án; the Manager dự án may not change another one.
    name_admins(client, world, as_user, bp, an="manager", binh="manager")
    assert (
        client.put(
            url, json={"set_ids": [], "permissions": ["review.view"]}, headers=an
        ).status_code
        == 403
    )
    admin = client.put(
        url, json={"set_ids": [], "permissions": ["review.view"]}, headers=as_user(world.admin)
    )
    assert admin.status_code == 200


def test_a_set_held_by_someone_locked_cannot_be_changed_by_the_manager(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """Review finding: editing or deleting a set changes its holders' rights, so a set the admin
    gave to another Manager dự án stays the admin's to change."""
    bp = staff_demo(client, world, as_user, db_session)
    an, admin = as_user(world.an), as_user(world.admin)
    url = f"{API}/business-projects/{bp}/permission-sets"
    made = client.post(url, json={"name": "S", "permissions": ["review.view"]}, headers=an).json()
    name_admins(client, world, as_user, bp, an="manager", binh="manager")
    body = {"user_ids": [world.binh.id]}
    given = client.put(f"{url}/{made['id']}/users", json=body, headers=admin)
    assert given.status_code == 200, given.text
    edit = client.patch(
        f"{url}/{made['id']}", json={"permissions": ["review.view", "review.rerun"]}, headers=an
    )
    assert edit.status_code == 403
    assert client.delete(f"{url}/{made['id']}", headers=an).status_code == 403
    # Renaming changes nobody's rights.
    assert client.patch(f"{url}/{made['id']}", json={"name": "S2"}, headers=an).status_code == 200
