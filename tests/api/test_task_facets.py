"""docs/specs/31 §22.2: the FilterBar's counts per value (`POST /tasks/search/facets`)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable

pytestmark = pytest.mark.integration


def test_facets_count_per_value(client: TestClient, world: World, as_user: Any) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    create(client, admin, world, title="A", type="bug", labels=["api"])
    create(client, admin, world, title="B", type="bug", labels=["api", "ui"])
    create(client, admin, world, title="C", type="story", assignee_id=world.binh.id)
    bp = bp_of(world)

    res = client.post(
        f"{API}/tasks/search/facets",
        headers=admin,
        json={"space": [bp], "fields": ["type", "label", "assignee", "sprint"]},
    )
    assert res.status_code == 200, res.text
    counts = res.json()["counts"]
    assert counts["type"] == {"bug": 2, "story": 1}
    assert counts["label"] == {"api": 2, "ui": 1}
    assert counts["assignee"] == {"none": 2, str(world.binh.id): 1}
    assert counts["sprint"] == {"backlog": 3}

    # The tree narrows what is counted.
    tree = {"all": [{"field": "label", "op": "is", "value": ["ui"]}]}
    res = client.post(
        f"{API}/tasks/search/facets",
        headers=admin,
        json={"space": [bp], "tree": tree, "fields": ["type"]},
    )
    assert res.json()["counts"] == {"type": {"bug": 1}}

    bad = client.post(
        f"{API}/tasks/search/facets", headers=admin, json={"space": [bp], "fields": ["due"]}
    )
    assert bad.status_code == 422
