"""docs/specs/21 phase B3: review knowledge, notifications and Telegram on permissions; the
defaults keep the old behaviour (the existing tests), sets change it without code."""

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PermissionSet, PermissionSetItem, User, UserPermissionSet
from tests.api.conftest import World
from tests.api.test_knowledge import gitlab, knowledge_row

pytestmark = pytest.mark.integration


def give(session: Session, user: User, items: dict[str, str]) -> None:
    custom = PermissionSet(name=f"B3 {user.id} {sorted(items)}", builtin=False)
    session.add(custom)
    session.flush()
    session.add_all(
        [PermissionSetItem(set_id=custom.id, permission=k, scope=v) for k, v in items.items()]
        + [UserPermissionSet(user_id=user.id, set_id=custom.id)]
    )
    session.flush()


def test_knowledge_edit_at_all_lets_a_non_manager_approve(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    path = f"/api/v1/projects/{world.project.id}/knowledge/approve"
    row = knowledge_row(db_session, world)
    assert client.post(path, headers=as_user(world.an)).status_code == 403  # a viewer only
    give(db_session, world.an, {"knowledge.edit": "all"})
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        res = client.post(path, headers=as_user(world.an))
    assert res.status_code == 200, res.text
    db_session.refresh(row)
    assert row.approved_by_user_id == world.an.id
    # "all" is still within the dự án one sees: without a grant the repo does not exist.
    give(db_session, world.outsider, {"knowledge.edit": "all"})
    world.project.business_project_id = None
    db_session.flush()
    assert client.post(path, headers=as_user(world.outsider)).status_code == 404


def test_admin_notifications_lets_a_non_admin_manage_webhooks(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    body = {"name": "Ops", "url": "https://hooks.example.com/b3", "events": ["review.severe"]}
    an = as_user(world.an)
    assert client.get("/api/v1/webhooks", headers=an).status_code == 403
    assert client.post("/api/v1/webhooks", headers=an, json=body).status_code == 403
    assert client.get("/api/v1/telegram/bots", headers=an).status_code == 403
    give(db_session, world.an, {"admin.notifications": "flag"})
    created = client.post("/api/v1/webhooks", headers=an, json=body)
    assert created.status_code == 201, created.text
    hook_id = created.json()["id"]
    assert [h["id"] for h in client.get("/api/v1/webhooks", headers=an).json()] == [hook_id]
    assert client.delete(f"/api/v1/webhooks/{hook_id}", headers=an).status_code == 204
    assert client.get("/api/v1/telegram/bots", headers=an).status_code == 200
    # Other admin screens stay closed.
    assert client.get("/api/v1/admin/command-runs", headers=an).status_code in (403, 404)


def test_telegram_self_removed_from_the_member_set_is_refused(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    assert client.get("/api/v1/me/telegram", headers=as_user(world.an)).status_code == 200
    member = db_session.scalars(
        select(PermissionSet).where(PermissionSet.auto_role == "member")
    ).one()
    db_session.execute(
        delete(PermissionSetItem).where(
            PermissionSetItem.set_id == member.id, PermissionSetItem.permission == "telegram.self"
        )
    )
    db_session.flush()
    assert client.get("/api/v1/me/telegram", headers=as_user(world.an)).status_code == 403
    assert (
        client.post("/api/v1/me/telegram/link-code", headers=as_user(world.an)).status_code == 403
    )
    assert client.get("/api/v1/me/telegram", headers=as_user(world.admin)).status_code == 200
