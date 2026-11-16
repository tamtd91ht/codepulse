"""docs/specs/31 §19.6: project templates when Task is turned on, and the Kanban board mode."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable, status_id

pytestmark = pytest.mark.integration


def put_space(client: TestClient, world: World, as_user: Any, **body: Any) -> Any:
    return client.put(f"{API}/tasks/spaces/{bp_of(world)}", headers=as_user(world.admin), json=body)


def test_no_template_is_scrum(client: TestClient, world: World, as_user: Any) -> None:
    space = enable(client, world, as_user)
    assert space["board_mode"] == "scrum"
    assert [s["name"] for s in space["statuses"]] == ["Cần làm", "Đang làm", "Đang review", "Xong"]
    assert space["types"] == ["epic", "story", "task", "bug", "subtask"]
    rules = space["rules"]
    assert rules["start"] == status_id(space, "Đang làm")
    assert rules["review"] == status_id(space, "Đang review")
    assert rules["done"] == status_id(space, "Xong")
    assert rules["review_on_mr"] and rules["done_on_merge"]
    assert len(space["priorities"]) == 5


def test_kanban_template(client: TestClient, world: World, as_user: Any) -> None:
    res = put_space(client, world, as_user, key="OPS", template="kanban")
    assert res.status_code == 200, res.text
    space = res.json()
    assert space["board_mode"] == "kanban"
    assert [(s["name"], s["category"]) for s in space["statuses"]] == [
        ("Cần làm", "todo"),
        ("Đang làm", "doing"),
        ("Xong", "done"),
    ]
    assert space["types"] == ["task", "bug", "subtask"]
    assert space["rules"]["review"] is None
    assert space["rules"]["review_on_mr"] is False
    assert space["rules"]["start"] == status_id(space, "Đang làm")
    # Sprint endpoints keep working in Kanban mode.
    admin = as_user(world.admin)
    sprint = client.post(f"{API}/tasks/spaces/{bp_of(world)}/sprints", headers=admin, json={})
    assert sprint.status_code == 201, sprint.text
    task = create(client, admin, world, type="task")
    assert task["key"] == "OPS-1"


def test_qa_template_and_explicit_types(client: TestClient, world: World, as_user: Any) -> None:
    res = put_space(client, world, as_user, key="QA", template="qa", types=["bug", "task"])
    assert res.status_code == 200, res.text
    space = res.json()
    assert [(s["name"], s["category"]) for s in space["statuses"]] == [
        ("Mới", "todo"),
        ("Đang xác minh", "doing"),
        ("Đã sửa", "doing"),
        ("Đã đóng", "done"),
    ]
    # Types picked in the dialog win over the template's.
    assert space["types"] == ["bug", "task"]
    assert space["board_mode"] == "scrum"
    rules = space["rules"]
    assert rules["review_on_mr"] is True
    assert rules["done_on_merge"] is False
    assert rules["review"] == status_id(space, "Đã sửa")
    assert rules["done"] == status_id(space, "Đã đóng")
    assert len(space["priorities"]) == 5


def test_template_only_on_first_enable_and_board_mode_later(
    client: TestClient, world: World, as_user: Any
) -> None:
    space = enable(client, world, as_user)
    again = put_space(client, world, as_user, key="CRM", template="kanban")
    assert again.status_code == 422
    unknown = put_space(client, world, as_user, key="CRM", board_mode="list")
    assert unknown.status_code == 422
    switched = put_space(client, world, as_user, key="CRM", board_mode="kanban")
    assert switched.status_code == 200, switched.text
    assert switched.json()["board_mode"] == "kanban"
    # The workflow is unchanged by switching mode.
    assert switched.json()["statuses"] == space["statuses"]
    listed = client.get(f"{API}/tasks/spaces", headers=as_user(world.admin)).json()
    assert next(s for s in listed if s["business_project_id"] == bp_of(world))["board_mode"] == (
        "kanban"
    )
    # Only one who manages Task changes the mode.
    member = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}",
        headers=as_user(world.an),
        json={"key": "CRM", "board_mode": "scrum"},
    )
    assert member.status_code == 403


def test_board_mode_given_on_enable(client: TestClient, world: World, as_user: Any) -> None:
    res = put_space(client, world, as_user, key="BUG", template="qa", board_mode="kanban")
    assert res.status_code == 200, res.text
    assert res.json()["board_mode"] == "kanban"
    bad = put_space(client, world, as_user, key="BUG2", template="nope")
    assert bad.status_code == 422
