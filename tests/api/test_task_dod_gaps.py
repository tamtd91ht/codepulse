"""docs/specs/31 §21.1: GET /tasks/spaces/{bp}/dod-gaps, the Board's "DoD thiếu" badge."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable

pytestmark = pytest.mark.integration


def _status(space: dict[str, Any], category: str) -> int:
    return next(s["id"] for s in space["statuses"] if s["category"] == category)


def test_dod_gaps_only_when_enforced(client: TestClient, world: World, as_user: Any) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    path = f"{API}/tasks/spaces/{bp_of(world)}/dod-gaps"
    doing = create(client, admin, world)
    res = client.post(
        f"{API}/tasks/{doing['key']}/transition",
        headers=admin,
        json={"status_id": _status(space, "doing")},
    )
    assert res.status_code == 200, res.text
    waiting = create(client, admin, world)  # still To Do: not listed

    # No DoD (or not enforced): nothing to flag.
    assert client.get(path, headers=admin).json() == {"enforced": False, "gaps": {}}
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}",
        headers=admin,
        json={"key": "CRM", "rules": {"dod": {"checks": ["has_mr", "no_critical"]}}},
    )
    assert res.status_code == 200, res.text
    assert client.get(path, headers=admin).json()["enforced"] is False

    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}",
        headers=admin,
        json={
            "key": "CRM",
            "rules": {"dod": {"checks": ["has_mr", "no_critical"], "enforce": True}},
        },
    )
    assert res.status_code == 200, res.text
    got = client.get(path, headers=admin)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["enforced"] is True
    assert body["gaps"] == {doing["key"]: ["Có MR"]}
    assert waiting["key"] not in body["gaps"]


def test_dod_gaps_needs_a_visible_space(client: TestClient, world: World, as_user: Any) -> None:
    enable(client, world, as_user)
    res = client.get(f"{API}/tasks/spaces/999999/dod-gaps", headers=as_user(world.admin))
    assert res.status_code == 404
