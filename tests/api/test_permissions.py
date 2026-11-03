"""docs/specs/21 phase A: who holds what, from their roles and the sets given to them."""

from typing import Annotated, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.security import Principal, current_principal, load_principal, require
from app.config import Settings
from app.models import PermissionSet, PermissionSetItem, ProjectGrant, UserPermissionSet
from app.permissions.catalog import BUILTIN_SETS, BY_KEY
from tests.api.conftest import World

pytestmark = pytest.mark.integration


def perms(client: TestClient, as_user: Any, who: Any) -> dict[str, str]:
    return dict(client.get("/api/v1/auth/me", headers=as_user(who)).json()["permissions"])


def builtin(role: str) -> dict[str, str]:
    return next(dict(b.items) for b in BUILTIN_SETS if b.auto_role == role)


def test_roles_give_their_builtin_sets(client: TestClient, world: World, as_user: Any) -> None:
    assert perms(client, as_user, world.an) == builtin("member")
    lead = perms(client, as_user, world.lead)
    assert lead["review.view"] == "team" and lead["dispute.decide"] == "team"
    assert lead["finding.close_request"] == "own"  # a lead is a member too
    manager = perms(client, as_user, world.manager)
    assert manager["assistant.use"] == "flag" and "dispute.decide" not in manager
    assert set(perms(client, as_user, world.admin)) == set(BY_KEY)


def test_a_project_manager_and_manage_all(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    db_session.execute(
        update(ProjectGrant).where(ProjectGrant.user_id == world.binh.id).values(role="manage")
    )
    db_session.flush()
    held = perms(client, as_user, world.binh)
    assert held["review.view"] == "project" and held["knowledge.edit"] == "project"
    world.outsider.manage_all = True
    db_session.flush()
    held = perms(client, as_user, world.outsider)
    assert held["review.view"] == "all" and "admin.projects" not in held


def test_a_set_given_by_hand_adds_to_the_roles(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    custom = PermissionSet(name="Kiểm toán review", builtin=False)
    db_session.add(custom)
    db_session.flush()
    db_session.add_all(
        [
            PermissionSetItem(set_id=custom.id, permission="review.view", scope="all"),
            PermissionSetItem(set_id=custom.id, permission="insights.view", scope="flag"),
            PermissionSetItem(set_id=custom.id, permission="gone.key", scope="all"),
            UserPermissionSet(user_id=world.an.id, set_id=custom.id),
        ]
    )
    db_session.flush()
    held = perms(client, as_user, world.an)
    assert held["review.view"] == "all" and held["insights.view"] == "flag"
    assert "gone.key" not in held  # a key the catalog no longer has is ignored
    assert held["dispute.raise"] == "own"  # still a member


def test_editing_a_builtin_set_changes_everyone_of_that_role(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    lead_set = db_session.scalars(
        select(PermissionSet).where(PermissionSet.auto_role == "lead")
    ).one()
    db_session.add(PermissionSetItem(set_id=lead_set.id, permission="knowledge.edit", scope="all"))
    db_session.flush()
    assert perms(client, as_user, world.lead)["knowledge.edit"] == "all"
    assert "knowledge.edit" not in perms(client, as_user, world.an)


def test_covers_follows_the_scope(db_session: Session, world: World, settings: Settings) -> None:
    bp = world.project.business_project_id
    lead = load_principal(db_session, world.lead.id, settings)
    an = load_principal(db_session, world.an.id, settings)
    admin = load_principal(db_session, world.admin.id, settings)
    assert lead.covers("review.view", world.an.id, bp)  # team: their member
    assert not lead.covers("review.view", world.outsider.id, bp)  # not their team
    assert an.covers("review.view", world.an.id, bp)
    assert not an.covers("review.view", world.binh.id, bp)
    assert not an.covers("dispute.decide", world.an.id, bp)  # not held at all
    assert admin.covers("dispute.decide", world.outsider.id, None)
    db_session.execute(
        update(ProjectGrant).where(ProjectGrant.user_id == world.binh.id).values(role="manage")
    )
    db_session.flush()
    pm = load_principal(db_session, world.binh.id, settings)
    assert pm.covers("review.view", world.outsider.id, bp)  # project: everyone in the dự án
    assert not pm.covers("review.view", world.outsider.id, 999_999)  # not a dự án they manage


def test_require_refuses_without_the_permission(
    db_session: Session, world: World, settings: Settings
) -> None:
    app = FastAPI()

    @app.get("/x")
    def x(principal: Annotated[Principal, require("insights.view")]) -> dict[str, int]:
        return {"id": principal.user_id}

    for who, status in ((world.an, 403), (world.lead, 200), (world.admin, 200)):
        principal = load_principal(db_session, who.id, settings)
        app.dependency_overrides[current_principal] = lambda p=principal: p
        assert TestClient(app).get("/x").status_code == status
    with pytest.raises(ValueError, match="unknown permission"):
        require("no.such.permission")
