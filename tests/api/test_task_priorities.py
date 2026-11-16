"""docs/specs/31 §18: each dự án's own priorities (Jira's priority scheme), the Board filter's
data and the report by priority."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable

pytestmark = pytest.mark.integration


def prio_path(world: World) -> str:
    return f"{API}/tasks/spaces/{bp_of(world)}/priorities"


def test_a_new_space_starts_with_the_five_priorities(
    client: TestClient, world: World, as_user: Any
) -> None:
    space = enable(client, world, as_user)
    assert [(p["key"], p["name"], p["level"], p["is_default"]) for p in space["priorities"]] == [
        ("highest", "Cao nhất", "highest", False),
        ("high", "Cao", "high", False),
        ("medium", "Trung bình", "medium", True),
        ("low", "Thấp", "low", False),
        ("lowest", "Thấp nhất", "lowest", False),
    ]
    an = as_user(world.an)
    task = create(client, an, world)
    assert task["priority"] == "medium"
    assert task["priority_name"] == "Trung bình" and task["priority_color"] == "amber"
    res = client.patch(f"{API}/tasks/{task['key']}", headers=an, json={"priority": "nope"})
    assert res.status_code == 422


def test_managers_shape_the_priorities(client: TestClient, world: World, as_user: Any) -> None:
    space = enable(client, world, as_user)
    admin, an = as_user(world.admin), as_user(world.an)
    by_key = {p["key"]: p for p in space["priorities"]}
    t_low = create(client, an, world, priority="low")
    t_top = create(client, an, world, priority="highest")
    wanted = [
        {"name": "Chặn", "level": "highest", "color": "purple"},
        {"id": by_key["highest"]["id"], "name": "Khẩn cấp", "level": "highest", "color": "red"},
        {"id": by_key["medium"]["id"], "name": "Bình thường", "level": "medium", "color": "blue"},
        {
            "id": by_key["high"]["id"],
            "name": "Cao",
            "level": "high",
            "color": "orange",
            "is_default": True,
        },
    ]
    # Only who manages Task on the dự án.
    assert client.put(prio_path(world), headers=an, json={"priorities": wanted}).status_code == 403
    # "low" still holds a task: removing it needs somewhere to move it.
    res = client.put(prio_path(world), headers=admin, json={"priorities": wanted})
    assert res.status_code == 409 and "Thấp" in res.json()["detail"]
    res = client.put(
        prio_path(world),
        headers=admin,
        json={"priorities": wanted, "move": {by_key["low"]["id"]: by_key["medium"]["id"]}},
    )
    assert res.status_code == 200, res.text
    got = res.json()
    assert [p["name"] for p in got] == ["Chặn", "Khẩn cấp", "Bình thường", "Cao"]
    assert got[0]["key"] == "chan"  # ASCII, from the name
    assert [p["key"] for p in got if p["is_default"]] == ["high"]
    moved = client.get(f"{API}/tasks/{t_low['key']}", headers=an).json()
    assert moved["priority"] == "medium" and moved["priority_name"] == "Bình thường"
    renamed = client.get(f"{API}/tasks/{t_top['key']}", headers=an).json()
    assert renamed["priority"] == "highest" and renamed["priority_name"] == "Khẩn cấp"
    # The new default and the new priority work on create.
    assert create(client, an, world)["priority"] == "high"
    blocker = create(client, an, world, priority="chan")
    assert blocker["priority_level"] == "highest" and blocker["priority_color"] == "purple"
    # Sorting follows the dự án's order: Chặn, Khẩn cấp, Bình thường, Cao.
    listed = client.get(
        f"{API}/tasks", headers=an, params={"space": bp_of(world), "sort": "priority"}
    ).json()
    assert [t["priority"] for t in listed["items"]] == ["chan", "highest", "medium", "high"]
    # Filter by priority key.
    only = client.get(
        f"{API}/tasks", headers=an, params={"space": bp_of(world), "priority": "chan"}
    ).json()
    assert [t["key"] for t in only["items"]] == [blocker["key"]]
    # Bad input.
    dup = [
        {"name": "A", "level": "high", "color": "red"},
        {"name": "a", "level": "low", "color": "red"},
    ]
    assert client.put(prio_path(world), headers=admin, json={"priorities": dup}).status_code == 422
    bad = [{"name": "A", "level": "high", "color": "teal"}]
    assert client.put(prio_path(world), headers=admin, json={"priorities": bad}).status_code == 422


def test_the_report_counts_by_priority(client: TestClient, world: World, as_user: Any) -> None:
    space = enable(client, world, as_user)
    an = as_user(world.an)
    create(client, an, world, priority="highest", due_date="2020-01-01", story_points=3)
    create(client, an, world, priority="highest")
    create(client, an, world, priority="low")
    report = client.get(f"{API}/tasks/spaces/{bp_of(world)}/reports", headers=an).json()
    rows = {r["priority"]["key"]: r for r in report["by_priority"]}
    assert [r["priority"]["key"] for r in report["by_priority"]] == [
        p["key"] for p in space["priorities"]
    ]
    assert rows["highest"]["open"] == 2 and rows["highest"]["overdue"] == 1
    assert rows["highest"]["open_points"] == 3 and rows["highest"]["created"] == 2
    assert rows["low"]["open"] == 1 and rows["medium"]["open"] == 0
