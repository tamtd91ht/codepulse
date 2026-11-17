"""docs/specs/31 §21.0 (R2 "vai trò tester"): the built-in "tester" auto role, held by a person
flagged tester (users.is_tester) or by the current members of a QA team (teams.is_qa)."""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.security import load_principal
from app.config import Settings
from app.models import PermissionSet, PermissionSetItem, TeamMember
from app.permissions.catalog import BUILTIN_SETS
from tests.api.conftest import World

pytestmark = pytest.mark.integration

API = "/api/v1"
TESTER = next(b for b in BUILTIN_SETS if b.auto_role == "tester")


def held(client: TestClient, admin: dict[str, str], user: Any) -> dict[str, str]:
    res = client.get(f"{API}/admin/users/{user.id}/permissions", headers=admin)
    assert res.status_code == 200, res.text
    return {p["key"]: p["scope"] for p in res.json()["permissions"]}


def the_tester_set(db: Session) -> PermissionSet:
    return db.scalars(select(PermissionSet).where(PermissionSet.auto_role == "tester")).one()


def test_the_migration_made_the_tester_set_builtin(db_session: Session) -> None:
    """The check constraint takes 'tester'; the custom "Tester" set became the built-in one."""
    row = the_tester_set(db_session)
    assert row.builtin is True and row.name == "Tester"
    rows = db_session.execute(
        select(PermissionSetItem.permission, PermissionSetItem.scope).where(
            PermissionSetItem.set_id == row.id
        )
    ).all()
    items = {k: v for k, v in rows}
    assert items == TESTER.items


def test_a_flagged_tester_holds_the_role(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    admin = as_user(world.admin)
    assert "tests.edit" not in held(client, admin, world.outsider)
    res = client.patch(f"{API}/users/{world.outsider.id}", headers=admin, json={"is_tester": True})
    assert res.status_code == 200, res.text
    assert res.json()["is_tester"] is True
    assert held(client, admin, world.outsider)["tests.edit"] == "all"
    principal = load_principal(db_session, world.outsider.id, settings)
    assert principal.is_tester and principal.can("tests.run")
    # The built-in set counts them among its holders.
    sets = client.get(f"{API}/admin/permission-sets", headers=admin).json()
    row = next(s for s in sets if s["auto_role"] == "tester")
    assert row["member_count"] >= 1
    res = client.patch(f"{API}/users/{world.outsider.id}", headers=admin, json={"is_tester": False})
    assert res.json()["is_tester"] is False
    assert "tests.edit" not in held(client, admin, world.outsider)


def test_the_members_of_a_qa_team_hold_the_role(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    admin = as_user(world.admin)
    # A member who has left the team is not in it any more.
    db_session.add(
        TeamMember(
            team_id=world.backend.id,
            user_id=world.manager.id,
            joined_at=date(2024, 1, 1),
            left_at=date(2024, 6, 1),
        )
    )
    db_session.flush()
    res = client.patch(f"{API}/teams/{world.backend.id}", headers=admin, json={"is_qa": True})
    assert res.status_code == 200, res.text
    assert res.json()["is_qa"] is True
    assert held(client, admin, world.an)["tests.edit"] == "all"
    assert held(client, admin, world.binh)["tests.run"] == "all"
    assert "tests.edit" not in held(client, admin, world.outsider)
    assert not load_principal(db_session, world.outsider.id, settings).is_tester
    assert not load_principal(db_session, world.manager.id, settings).is_tester
    assert load_principal(db_session, world.an.id, settings).is_tester
    teams = {t["id"]: t for t in client.get(f"{API}/teams", headers=admin).json()}
    assert teams[world.backend.id]["is_qa"] is True
    res = client.patch(f"{API}/teams/{world.backend.id}", headers=admin, json={"is_qa": False})
    assert res.json()["is_qa"] is False
    assert "tests.edit" not in held(client, admin, world.an)


def test_a_built_in_tester_set_is_not_given_by_hand(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    row = the_tester_set(db_session)
    res = client.put(
        f"{API}/admin/permission-sets/{row.id}/teams",
        headers=admin,
        json={"team_ids": [world.backend.id]},
    )
    assert res.status_code == 409
