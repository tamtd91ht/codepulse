"""docs/specs/21 phase C: the permission sets API (Admin > Phân quyền)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, PermissionSet, PermissionSetItem, UserPermissionSet
from app.permissions.catalog import BUILTIN_SETS, BY_KEY, CATALOG
from tests.api.conftest import World

pytestmark = pytest.mark.integration

BASE = "/api/v1/admin/permission-sets"


def sets(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get(BASE, headers=headers)
    assert response.status_code == 200, response.text
    return list(response.json())


def by_role(rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
    return next(r for r in rows if r["auto_role"] == role)


def actions(db: Session) -> list[str]:
    return [
        a
        for a in db.scalars(select(AuditLog.action).order_by(AuditLog.id))
        if a.startswith("permission_set.")
    ]


def give(db: Session, world: World, user: Any, items: dict[str, str]) -> PermissionSet:
    row = PermissionSet(name=f"Bộ của {user.full_name}", builtin=False)
    db.add(row)
    db.flush()
    db.add_all(PermissionSetItem(set_id=row.id, permission=k, scope=v) for k, v in items.items())
    db.add(UserPermissionSet(user_id=user.id, set_id=row.id))
    db.flush()
    return row


def test_only_admins_by_default(client: TestClient, world: World, as_user: Any) -> None:
    for who in (world.an, world.lead, world.manager):
        assert client.get(BASE, headers=as_user(who)).status_code == 403
        assert (
            client.get("/api/v1/admin/permissions/catalog", headers=as_user(who)).status_code == 403
        )
        assert client.post(BASE, json={"name": "x"}, headers=as_user(who)).status_code == 403
    assert client.get(BASE).status_code == 401


def test_catalog_groups_every_permission(client: TestClient, world: World, as_user: Any) -> None:
    body = client.get("/api/v1/admin/permissions/catalog", headers=as_user(world.admin)).json()
    keys = [p["key"] for g in body["groups"] for p in g["permissions"]]
    assert keys == [p.key for p in CATALOG]
    flat = {p["key"]: p for g in body["groups"] for p in g["permissions"]}
    assert flat["overview.view"]["flag"] is True and flat["overview.view"]["scopes"] == []
    assert flat["evaluation.view"]["sensitive"] is True
    assert flat["review.request"]["scopes"] == ["own", "project", "all"]
    assert [s["label"] for s in body["scopes"]] == ["Của mình", "Team", "Dự án", "Tất cả"]


def test_builtin_sets_count_the_people_of_their_role(
    client: TestClient, world: World, as_user: Any
) -> None:
    rows = sets(client, as_user(world.admin))
    assert [r["auto_role"] for r in rows[: len(BUILTIN_SETS)]] == [
        b.auto_role for b in BUILTIN_SETS
    ]
    lead = by_role(rows, "lead")
    assert lead["builtin"] is True and lead["member_count"] == 1
    assert {i["permission"]: i["scope"] for i in lead["items"]} == next(
        b.items for b in BUILTIN_SETS if b.auto_role == "lead"
    )
    assert by_role(rows, "manager")["member_count"] == 1
    assert by_role(rows, "member")["member_count"] == 5  # everyone but the admin
    assert by_role(rows, "project_manager")["member_count"] == 0


def test_create_assign_preview_and_delete(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    created = client.post(
        BASE,
        json={
            "name": "Kiểm toán review",
            "description": "Xem mọi review",
            "items": [
                {"permission": "review.view", "scope": "all"},
                {"permission": "insights.view", "scope": "flag"},
            ],
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert created.json()["builtin"] is False and created.json()["member_count"] == 0

    assigned = client.put(f"{BASE}/{sid}/users", json={"user_ids": [world.an.id]}, headers=admin)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["member_count"] == 1
    assert [m["id"] for m in assigned.json()["members"]] == [world.an.id]

    preview = client.get(f"/api/v1/admin/users/{world.an.id}/permissions", headers=admin).json()
    held = {p["key"]: p for p in preview["permissions"]}
    assert held["review.view"]["scope"] == "all"
    assert [s["name"] for s in held["review.view"]["sources"]] == ["Kiểm toán review", "Thành viên"]
    assert held["dispute.raise"]["sources"][0]["name"] == "Thành viên"
    assert {s["via"] for s in preview["sets"]} == {"role", "given"}
    me = client.get("/api/v1/auth/me", headers=as_user(world.an)).json()["permissions"]
    assert me["review.view"] == "all"

    patched = client.patch(
        f"{BASE}/{sid}",
        json={"name": "Kiểm toán", "items": [{"permission": "review.view", "scope": "team"}]},
        headers=admin,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Kiểm toán"
    assert patched.json()["items"] == [{"permission": "review.view", "scope": "team"}]

    assert client.delete(f"{BASE}/{sid}", headers=admin).status_code == 204
    assert db_session.get(PermissionSet, sid) is None
    assert actions(db_session) == [
        "permission_set.create",
        "permission_set.assign",
        "permission_set.update",
        "permission_set.delete",
    ]
    me = client.get("/api/v1/auth/me", headers=as_user(world.an)).json()["permissions"]
    assert me["review.view"] == "own"


def test_clone_copies_the_items(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    lead = by_role(sets(client, admin), "lead")
    clone = client.post(
        BASE, json={"name": "Lead mở rộng", "from_set_id": lead["id"]}, headers=admin
    )
    # The lead set holds sensitive permissions, but a copy grants nobody anything new.
    assert clone.status_code == 201, clone.text
    assert clone.json()["items"] == lead["items"]
    assert clone.json()["auto_role"] is None and clone.json()["builtin"] is False
    assert clone.json()["description"] == lead["description"]
    again = client.post(BASE, json={"name": "lead mở rộng"}, headers=admin)
    assert again.status_code == 409  # names are unique, whatever the case


def test_items_are_validated(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    for items in (
        [{"permission": "no.such", "scope": "all"}],
        [{"permission": "overview.view", "scope": "all"}],  # a flag
        [{"permission": "review.request", "scope": "team"}],  # not one of its scopes
        [
            {"permission": "review.view", "scope": "own"},
            {"permission": "review.view", "scope": "all"},
        ],
    ):
        response = client.post(BASE, json={"name": "x", "items": items}, headers=admin)
        assert response.status_code == 422, items


def test_sensitive_permissions_need_confirmation(
    client: TestClient, world: World, as_user: Any
) -> None:
    admin = as_user(world.admin)
    body = {"name": "Đánh giá", "items": [{"permission": "evaluation.view", "scope": "all"}]}
    refused = client.post(BASE, json=body, headers=admin)
    assert refused.status_code == 422
    assert refused.json()["detail"]["sensitive"] == ["evaluation.view"]
    created = client.post(BASE, json={**body, "confirm_sensitive": True}, headers=admin)
    assert created.status_code == 201
    sid = created.json()["id"]
    # Giving it to someone grants it too.
    users = {"user_ids": [world.an.id]}
    assert client.put(f"{BASE}/{sid}/users", json=users, headers=admin).status_code == 422
    ok = client.put(f"{BASE}/{sid}/users", json={**users, "confirm_sensitive": True}, headers=admin)
    assert ok.status_code == 200
    # Narrowing or keeping it does not ask again; widening an existing one in a built-in does.
    lead = by_role(sets(client, admin), "lead")
    wider = [
        {**i, "scope": "all"} if i["permission"] == "evidence.view" else i for i in lead["items"]
    ]
    assert (
        client.patch(f"{BASE}/{lead['id']}", json={"items": wider}, headers=admin).status_code
        == 422
    )
    same = client.patch(f"{BASE}/{lead['id']}", json={"items": lead["items"]}, headers=admin)
    assert same.status_code == 200


def test_builtin_sets_follow_their_role(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    lead = by_role(sets(client, admin), "lead")
    url = f"{BASE}/{lead['id']}"
    assert client.delete(url, headers=admin).status_code == 409
    assert (
        client.put(f"{url}/users", json={"user_ids": [world.an.id]}, headers=admin).status_code
        == 409
    )
    assert client.patch(url, json={"name": "Trưởng nhóm"}, headers=admin).status_code == 409
    assert client.patch(url, json={"auto_role": "member"}, headers=admin).status_code == 422
    items = [*lead["items"], {"permission": "knowledge.edit", "scope": "all"}]
    edited = client.patch(url, json={"items": items, "description": "Mới"}, headers=admin)
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "Mới" and edited.json()["auto_role"] == "lead"
    me = client.get("/api/v1/auth/me", headers=as_user(world.lead)).json()["permissions"]
    assert me["knowledge.edit"] == "all"


def test_nothing_makes_an_admin(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    for extra in ({"auto_role": "member"}, {"builtin": True}, {"is_admin": True}):
        response = client.post(BASE, json={"name": "Siêu", **extra}, headers=admin)
        assert response.status_code == 422, extra


def test_a_non_admin_permission_manager_cannot_raise_themselves(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    give(db_session, world, world.lead, {"admin.permissions": "flag"})
    lead = as_user(world.lead)
    rows = sets(client, lead)  # holds admin.permissions now
    member_set = by_role(rows, "member")
    # A set they hold (member) cannot gain anything.
    items = [*member_set["items"], {"permission": "insights.view", "scope": "flag"}]
    assert (
        client.patch(f"{BASE}/{member_set['id']}", json={"items": items}, headers=lead).status_code
        == 403
    )
    # Nothing they do not hold, nor the right to hand out rights.
    beyond = {"name": "Rộng", "items": [{"permission": "review.view", "scope": "all"}]}
    assert client.post(BASE, json=beyond, headers=lead).status_code == 403
    meta = {"name": "Meta", "items": [{"permission": "admin.permissions", "scope": "flag"}]}
    assert client.post(BASE, json=meta, headers=lead).status_code == 403
    # Within what they hold: allowed, for others, never for themselves.
    fine = client.post(
        BASE,
        json={"name": "Team", "items": [{"permission": "review.view", "scope": "team"}]},
        headers=lead,
    )
    assert fine.status_code == 201, fine.text
    sid = fine.json()["id"]
    assert (
        client.put(
            f"{BASE}/{sid}/users", json={"user_ids": [world.lead.id]}, headers=lead
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"{BASE}/{sid}/users", json={"user_ids": [world.an.id]}, headers=lead
        ).status_code
        == 200
    )


def test_preview_of_an_admin_and_unknown_user(
    client: TestClient, world: World, as_user: Any
) -> None:
    admin = as_user(world.admin)
    body = client.get(f"/api/v1/admin/users/{world.admin.id}/permissions", headers=admin).json()
    assert body["is_admin"] is True and {p["key"] for p in body["permissions"]} == set(BY_KEY)
    assert client.get("/api/v1/admin/users/999999/permissions", headers=admin).status_code == 404
    assert client.get(f"{BASE}/999999", headers=admin).status_code in (404, 405)
    assert client.delete(f"{BASE}/999999", headers=admin).status_code == 404
