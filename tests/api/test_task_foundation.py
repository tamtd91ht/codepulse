"""docs/specs/31 §19.1 (roadmap P0): resolutions instead of a "Won't Do" status, cycle time from
the first start, sub-tasks following their parent, points from Story / Task / Bug only, epics,
components, permission sets given to a team."""

import importlib.util
import pathlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PermissionSet, PermissionSetItem, Task, TaskStatus
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable

pytestmark = pytest.mark.integration


def status_of(space: dict[str, Any], category: str) -> int:
    return next(s["id"] for s in space["statuses"] if s["category"] == category)


def sprint(client: TestClient, headers: dict[str, str], world: World, name: str) -> int:
    res = client.post(
        f"{API}/tasks/spaces/{bp_of(world)}/sprints", headers=headers, json={"name": name}
    )
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


def test_resolutions_close_tasks_with_a_reason(
    client: TestClient, world: World, as_user: Any
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    assert all(s["name"] != "Won't Do" for s in space["statuses"])  # a resolution now
    task = create(client, admin, world)
    path = f"{API}/tasks/{task['key']}"
    assert task["resolution"] is None
    # No reason while open.
    assert client.patch(path, headers=admin, json={"resolution": "duplicate"}).status_code == 409
    done = status_of(space, "done")
    got = client.post(f"{path}/transition", headers=admin, json={"status_id": done}).json()
    assert got["resolution"] == "done"
    got = client.patch(path, headers=admin, json={"resolution": "duplicate"}).json()
    assert got["resolution"] == "duplicate"
    assert any(h["field"] == "resolution" and h["new"] == "Trùng" for h in got["history"])
    # Reopening clears it.
    todo = status_of(space, "todo")
    got = client.post(f"{path}/transition", headers=admin, json={"status_id": todo}).json()
    assert got["resolution"] is None
    bad = client.post(
        f"{path}/transition", headers=admin, json={"status_id": done, "resolution": "nope"}
    )
    assert bad.status_code == 422


def test_cycle_time_counts_only_started_work(
    client: TestClient, world: World, as_user: Any
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    worked, skipped, dropped = (create(client, admin, world)["key"] for _ in range(3))
    doing, done = status_of(space, "doing"), status_of(space, "done")
    client.post(f"{API}/tasks/{worked}/transition", headers=admin, json={"status_id": doing})
    started = client.get(f"{API}/tasks/{worked}", headers=admin).json()["started_at"]
    assert started is not None
    for key in (worked, skipped):
        client.post(f"{API}/tasks/{key}/transition", headers=admin, json={"status_id": done})
    client.post(
        f"{API}/tasks/{dropped}/transition",
        headers=admin,
        json={"status_id": done, "resolution": "wont_do"},
    )
    report = client.get(f"{API}/tasks/spaces/{bp_of(world)}/reports", headers=admin).json()
    # Only the started one is timed; straight-to-Done is counted apart; won't-do is neither.
    assert report["cycle_time"]["count"] == 1 and report["cycle_time"]["skipped"] == 1


def test_sub_tasks_follow_their_parent(client: TestClient, world: World, as_user: Any) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    s1, s2 = sprint(client, admin, world, "S1"), sprint(client, admin, world, "S2")
    parent = create(client, admin, world, type="story", sprint_id=s1, story_points=5)
    child = create(client, admin, world, type="subtask", parent_key=parent["key"], story_points=2)
    assert child["sprint_id"] == s1  # inherited on create
    # Moving the parent moves its sub-tasks.
    client.patch(f"{API}/tasks/{parent['key']}", headers=admin, json={"sprint_id": s2})
    assert client.get(f"{API}/tasks/{child['key']}", headers=admin).json()["sprint_id"] == s2
    # A sub-task alone cannot leave its parent's sprint.
    res = client.patch(f"{API}/tasks/{child['key']}", headers=admin, json={"sprint_id": s1})
    assert res.status_code == 422
    # Bulk edits skip it rather than fail.
    res = client.post(
        f"{API}/tasks/bulk",
        headers=admin,
        json={"keys": [child["key"]], "set": {"sprint_id": s1, "priority": "high"}},
    )
    assert res.status_code == 200, res.text
    got = client.get(f"{API}/tasks/{child['key']}", headers=admin).json()
    assert got["sprint_id"] == s2 and got["priority"] == "high"
    # Sprint points: the story's 5, not the sub-task's 2 nor an epic's.
    create(client, admin, world, type="epic", sprint_id=s2, story_points=40)
    sprints = client.get(f"{API}/tasks/spaces/{bp_of(world)}/sprints", headers=admin).json()
    assert next(s for s in sprints if s["id"] == s2)["points"] == 5


def test_epics_and_components(client: TestClient, world: World, as_user: Any) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    epic = create(client, admin, world, type="epic", title="Thanh toán")
    create(client, admin, world, type="story", parent_key=epic["key"], story_points=3)
    done_one = create(
        client,
        admin,
        world,
        type="task",
        parent_key=epic["key"],
        story_points=2,
        components=["api"],
    )
    client.post(
        f"{API}/tasks/{done_one['key']}/transition",
        headers=admin,
        json={"status_id": status_of(space, "done")},
    )
    epics = client.get(f"{API}/tasks/spaces/{bp_of(world)}/epics", headers=admin).json()
    assert epics == [
        {
            "key": epic["key"],
            "title": "Thanh toán",
            "closed": False,
            "total": 2,
            "done": 1,
            "points": 5.0,
            "points_open": 3.0,
        }
    ]
    found = client.get(
        f"{API}/tasks", headers=admin, params={"space": bp_of(world), "component": "api"}
    ).json()
    assert [t["key"] for t in found["items"]] == [done_one["key"]]


def test_smart_commit_aliases_reach_the_vietnamese_statuses(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.models import TaskSpace
    from app.tasks import service as ts

    enable(client, world, as_user)
    space = db_session.get(TaskSpace, bp_of(world))
    assert space is not None
    for word, name in (
        ("in-progress", "Đang làm"),
        ("dang-lam", "Đang làm"),
        ("review", "Đang review"),
        ("done", "Xong"),
        ("xong", "Xong"),
        ("todo", "Cần làm"),
    ):
        found = ts.status_by_word(db_session, space, word)
        assert found is not None and found.name == name, word
    assert ts.status_by_word(db_session, space, "nothing") is None


def test_the_migration_turns_wont_do_into_a_resolution(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    # A space from before: a "Won't Do" status holding a task, a transition naming it.
    statuses = [
        {"id": s["id"], "name": s["name"], "category": s["category"], "transitions": None}
        for s in space["statuses"]
    ]
    statuses.append({"name": "Won't Do", "category": "done", "transitions": None})
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}/statuses", headers=admin, json={"statuses": statuses}
    )
    wont = next(s["id"] for s in res.json() if s["name"] == "Won't Do")
    todo = status_of(space, "todo")
    client.put(
        f"{API}/tasks/spaces/{bp_of(world)}/statuses",
        headers=admin,
        json={
            "statuses": [
                {**s, "transitions": [wont] if s["id"] == todo else None}
                for s in [*statuses[:-1], {**statuses[-1], "id": wont}]
            ]
        },
    )
    # The git rule "done" pointed at it too (review H4): it must follow the tasks.
    old_done = space["rules"]["done"]
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}",
        headers=admin,
        json={"key": "CRM", "rules": {"done": wont}},
    )
    assert res.status_code == 200, res.text
    key = create(client, admin, world)["key"]
    client.post(f"{API}/tasks/{key}/transition", headers=admin, json={"status_id": wont})
    path = sorted(pathlib.Path("migrations/versions").glob("*_a7c9e1b3d5f8_*.py"))[-1]
    spec = importlib.util.spec_from_file_location("task_foundation", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db_session.flush()
    module.migrate_data(db_session.connection())
    db_session.expire_all()
    task = db_session.scalars(select(Task).where(Task.key == key)).one()
    assert task.resolution == "wont_do"
    assert task.status_id == old_done
    assert db_session.get(TaskStatus, wont) is None
    # A move to Won't Do became a move to Done; the rule points at Done again.
    assert db_session.get(TaskStatus, todo).transitions == [old_done]  # type: ignore[union-attr]
    from app.models import TaskSpace

    rules = db_session.get(TaskSpace, bp_of(world)).rules  # type: ignore[union-attr]
    assert rules["done"] == old_done
    assert db_session.scalars(select(PermissionSet).where(PermissionSet.name == "Tester")).one()


def test_a_set_given_to_a_team(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    # A custom set ("Tester" itself became the built-in tester role, docs/specs/31 §21.0).
    tester = PermissionSet(name="Bộ QA", builtin=False)
    db_session.add(tester)
    db_session.flush()
    db_session.add_all(
        [
            PermissionSetItem(set_id=tester.id, permission="tests.edit", scope="all"),
            PermissionSetItem(set_id=tester.id, permission="tests.run", scope="all"),
        ]
    )
    db_session.flush()

    def held(user: Any) -> dict[str, str]:
        res = client.get(f"{API}/admin/users/{user.id}/permissions", headers=admin).json()
        return {p["key"]: p["scope"] for p in res["permissions"]}

    assert "tests.edit" not in held(world.an)
    res = client.put(
        f"{API}/admin/permission-sets/{tester.id}/teams",
        headers=admin,
        json={"team_ids": [world.backend.id]},
    )
    assert res.status_code == 200, res.text
    assert [t["name"] for t in res.json()["teams"]] == ["Backend"]
    # Everyone in Backend holds it now, nobody else.
    assert held(world.an)["tests.edit"] == "all"
    assert held(world.binh)["tests.run"] == "all"
    assert "tests.edit" not in held(world.outsider)
    sets = client.get(f"{API}/admin/users/{world.an.id}/permissions", headers=admin).json()["sets"]
    assert any(s["name"] == "Bộ QA" and s["via"] == "team" for s in sets)
    # Only admins hand it out here; a member cannot.
    res = client.put(
        f"{API}/admin/permission-sets/{tester.id}/teams",
        headers=as_user(world.an),
        json={"team_ids": []},
    )
    assert res.status_code == 403
    res = client.put(
        f"{API}/admin/permission-sets/{tester.id}/teams", headers=admin, json={"team_ids": []}
    )
    assert res.json()["teams"] == [] and "tests.edit" not in held(world.an)


def test_completing_a_sprint_keeps_sub_tasks_with_their_parent(
    client: TestClient, world: World, as_user: Any
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    s1, s2 = sprint(client, admin, world, "S1"), sprint(client, admin, world, "S2")
    open_parent = create(client, admin, world, type="story", sprint_id=s1)
    done_child = create(client, admin, world, type="subtask", parent_key=open_parent["key"])
    done_parent = create(client, admin, world, type="story", sprint_id=s1)
    open_child = create(client, admin, world, type="subtask", parent_key=done_parent["key"])
    done = status_of(space, "done")
    for key in (done_child["key"], done_parent["key"]):
        client.post(f"{API}/tasks/{key}/transition", headers=admin, json={"status_id": done})
    from datetime import date, timedelta

    window = {
        "start_date": date.today().isoformat(),
        "end_date": (date.today() + timedelta(days=7)).isoformat(),
    }
    res = client.post(f"{API}/tasks/sprints/{s1}/start", headers=admin, json=window)
    assert res.status_code == 200, res.text
    res = client.post(f"{API}/tasks/sprints/{s1}/complete", headers=admin, json={"move_to": s2})
    assert res.status_code == 200, res.text
    assert res.json()["moved"] == 1

    def sprint_of(key: str) -> int | None:
        return client.get(f"{API}/tasks/{key}", headers=admin).json()["sprint_id"]

    assert sprint_of(open_parent["key"]) == s2 and sprint_of(done_child["key"]) == s2
    assert sprint_of(done_parent["key"]) == s1 and sprint_of(open_child["key"]) == s1


def test_round2_trust_move_resolution_velocity_and_team_scope(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """docs/specs/31 §20.0: the Board's move validates the resolution, "obsolete" closes without
    output, velocity counts delivered work only, the Lead's "team" scope reaches their people."""
    from datetime import date, timedelta

    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    done = status_of(space, "done")
    task = create(client, admin, world)
    bad = client.post(
        f"{API}/tasks/{task['key']}/move",
        headers=admin,
        json={"status_id": done, "resolution": "xyz"},
    )
    assert bad.status_code == 422
    res = client.post(
        f"{API}/tasks/{task['key']}/move",
        headers=admin,
        json={"status_id": done, "resolution": "obsolete"},
    )
    assert res.status_code == 200, res.text
    assert (
        client.get(f"{API}/tasks/{task['key']}", headers=admin).json()["resolution"] == "obsolete"
    )
    listed = client.get(
        f"{API}/tasks", headers=admin, params={"space": bp_of(world), "resolution": "obsolete"}
    ).json()
    assert [t["key"] for t in listed["items"]] == [task["key"]]
    report = client.get(f"{API}/tasks/spaces/{bp_of(world)}/reports", headers=admin).json()
    assert report["by_resolution"]["obsolete"] == 1
    # Velocity: a sprint with one delivered (3 SP) and one won't-do (5 SP) task completes 3.
    sid = sprint(client, admin, world, "S1")
    kept = create(client, admin, world, sprint_id=sid, story_points=3)
    dropped = create(client, admin, world, sprint_id=sid, story_points=5)
    client.post(f"{API}/tasks/{kept['key']}/transition", headers=admin, json={"status_id": done})
    client.post(
        f"{API}/tasks/{dropped['key']}/transition",
        headers=admin,
        json={"status_id": done, "resolution": "wont_do"},
    )
    window = {
        "start_date": date.today().isoformat(),
        "end_date": (date.today() + timedelta(days=7)).isoformat(),
    }
    client.post(f"{API}/tasks/sprints/{sid}/start", headers=admin, json=window)
    closed = client.post(f"{API}/tasks/sprints/{sid}/complete", headers=admin, json={}).json()
    assert closed["done_points"] == 3
    report = client.get(f"{API}/tasks/spaces/{bp_of(world)}/reports", headers=admin).json()
    assert report["velocity"][-1]["completed"] == 3
    # Team scope: the Lead edits a task assigned to a member of the team they lead (an), not
    # one of the outsider's.
    lead = as_user(world.lead)
    theirs = create(client, admin, world, assignee_id=world.an.id)
    other = create(client, admin, world, assignee_id=world.outsider.id)
    assert client.get(f"{API}/tasks/{theirs['key']}", headers=lead).json()["can"]["edit"] is True
    got = client.get(f"{API}/tasks/{other['key']}", headers=lead).json()
    assert got["can"]["edit"] is False
    # Team moderation never covers the lead's own task (others' comments on it stay theirs).
    own = create(client, admin, world, assignee_id=world.lead.id)
    note = client.post(
        f"{API}/tasks/{own['key']}/comments", headers=admin, json={"body": "của PM"}
    ).json()
    res = client.delete(f"{API}/tasks/{own['key']}/comments/{note['id']}", headers=lead)
    assert res.status_code == 403
    # ...but covers a team member's task.
    note = client.post(
        f"{API}/tasks/{theirs['key']}/comments", headers=admin, json={"body": "x"}
    ).json()
    assert (
        client.delete(
            f"{API}/tasks/{theirs['key']}/comments/{note['id']}", headers=lead
        ).status_code
        == 204
    )
