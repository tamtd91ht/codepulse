"""docs/specs/22 §11 (docs/specs/21 §13): a dự án's own criteria sets, made by its Lead /
Manager dự án, seen and used by that dự án only."""

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import CriteriaSet, ProjectCriteriaSet
from app.scoring.criteria import TEMPLATES
from tests.api.conftest import World
from tests.api.test_project_admin import empty_project, name_admins

pytestmark = pytest.mark.integration

API = "/api/v1"


def parts() -> list[dict[str, Any]]:
    return copy.deepcopy(TEMPLATES["chuan_chung"]["components"])


def make(client: TestClient, headers: dict[str, str], name: str, bp_id: int | None = None) -> Any:
    body: dict[str, Any] = {"name": name, "components": parts()}
    if bp_id is not None:
        body["business_project_id"] = bp_id
    return client.post(f"{API}/criteria-sets", json=body, headers=headers)


def test_lead_du_an_makes_own_sets_that_other_du_an_never_see_or_use(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    mine = empty_project(client, world, as_user, "Mine")
    other = empty_project(client, world, as_user, "Other")
    name_admins(client, world, as_user, mine, an="lead")
    name_admins(client, world, as_user, other, binh="manager")
    an, binh, admin = as_user(world.an), as_user(world.binh), as_user(world.admin)

    shared = make(client, admin, "Chung")
    assert shared.status_code == 201
    # Not a shared set: criteria.manage is the admins'.
    assert make(client, an, "Chung 2").status_code == 403
    # Not another dự án's.
    assert make(client, an, "X", other).status_code == 404
    own = make(client, an, "Riêng", mine)
    assert own.status_code == 201, own.text
    own_id = own.json()["id"]
    assert own.json()["business_project_id"] == mine and own.json()["can_edit"] is True
    # Same name in another owner is fine; within the owner it is not.
    assert make(client, binh, "Riêng", other).status_code == 201
    assert make(client, an, "riêng", mine).status_code == 409

    listed = client.get(f"{API}/criteria-sets?business_project_id={mine}", headers=an).json()
    assert {s["name"] for s in listed} == {"Chung", "Riêng"}
    shared_row = next(s for s in listed if s["name"] == "Chung")
    assert shared_row["can_edit"] is False
    # Binh (other dự án) never sees An's set.
    assert client.get(f"{API}/criteria-sets/{own_id}", headers=binh).status_code == 404
    names = {s["name"] for s in client.get(f"{API}/criteria-sets", headers=binh).json()}
    assert "Riêng" in names and own_id not in {
        s["id"] for s in client.get(f"{API}/criteria-sets", headers=binh).json()
    }

    # The shared set is read-only to the Lead dự án.
    body = {"name": "Chung", "components": parts(), "apply_caps": True}
    put = client.put(f"{API}/criteria-sets/{shared.json()['id']}", json=body, headers=an)
    assert put.status_code == 403
    # Own set: edited, archived, unarchived.
    edited = client.put(
        f"{API}/criteria-sets/{own_id}", json={**body, "name": "Riêng 2"}, headers=an
    )
    assert edited.status_code == 200 and edited.json()["name"] == "Riêng 2"
    assert client.post(f"{API}/criteria-sets/{own_id}/archive", headers=an).status_code == 200
    assert client.post(f"{API}/criteria-sets/{own_id}/unarchive", headers=an).status_code == 200
    assert client.put(f"{API}/criteria-sets/{own_id}", json=body, headers=binh).status_code == 404

    # Assign: shared + own to Mine; An's set to Other is refused even for an admin.
    ok = client.put(
        f"{API}/business-projects/{mine}/criteria-sets",
        json={"sets": [{"set_id": own_id, "is_primary": True}, {"set_id": shared.json()["id"]}]},
        headers=an,
    )
    assert ok.status_code == 200, ok.text
    refused = client.put(
        f"{API}/business-projects/{other}/criteria-sets",
        json={"sets": [{"set_id": own_id, "is_primary": True}]},
        headers=admin,
    )
    assert refused.status_code == 422


def test_clone_shared_into_du_an_but_never_out_of_it(
    client: TestClient, world: World, as_user: Any
) -> None:
    mine = empty_project(client, world, as_user, "Mine")
    other = empty_project(client, world, as_user, "Other")
    name_admins(client, world, as_user, mine, an="manager")
    name_admins(client, world, as_user, other, an="manager")
    an, admin = as_user(world.an), as_user(world.admin)
    shared = make(client, admin, "Chung").json()

    copy_in = client.post(
        f"{API}/criteria-sets/{shared['id']}/clone",
        json={"name": "Chung của Mine", "business_project_id": mine},
        headers=an,
    )
    assert copy_in.status_code == 201, copy_in.text
    assert copy_in.json()["business_project_id"] == mine
    # Shared copy: criteria.manage only.
    shared_copy = client.post(
        f"{API}/criteria-sets/{shared['id']}/clone", json={"name": "Z"}, headers=an
    )
    assert shared_copy.status_code == 403
    # Out of the dự án (to another one An also runs, or to shared): no.
    out = client.post(
        f"{API}/criteria-sets/{copy_in.json()['id']}/clone",
        json={"name": "Y", "business_project_id": other},
        headers=an,
    )
    assert out.status_code == 422


def test_trigger_blocks_another_du_an_set_and_delete_takes_own_sets(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    mine = empty_project(client, world, as_user, "Mine")
    other = empty_project(client, world, as_user, "Other")
    admin = as_user(world.admin)
    own = make(client, admin, "Riêng", mine).json()
    assert (
        client.put(
            f"{API}/business-projects/{mine}/criteria-sets",
            json={"sets": [{"set_id": own["id"], "is_primary": True}]},
            headers=admin,
        ).status_code
        == 200
    )
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(ProjectCriteriaSet(business_project_id=other, set_id=own["id"]))
        db_session.flush()
    assert client.delete(f"{API}/business-projects/{mine}", headers=admin).status_code == 204
    db_session.expire_all()
    # Its scores are review history: the set stays, archived and shared, named after the dự án.
    kept = db_session.get(CriteriaSet, own["id"])
    assert kept is not None and kept.archived and kept.business_project_id is None
    assert "Mine, đã xoá" in kept.name


def test_plain_viewer_and_preview_need_project_criteria(
    client: TestClient, world: World, as_user: Any
) -> None:
    mine = empty_project(client, world, as_user, "Mine")
    name_admins(client, world, as_user, mine, an="lead")
    body = {"components": parts(), "business_project_id": mine}
    assert (
        client.post(f"{API}/criteria/preview", json=body, headers=as_user(world.an)).status_code
        == 200
    )
    assert client.post(
        f"{API}/criteria/preview", json=body, headers=as_user(world.binh)
    ).status_code in (403, 404)
