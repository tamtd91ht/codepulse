"""docs/specs/31 §19.5 (roadmap P4): sprint health of a dự án and the multi-project overview."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Task, TaskGitLink
from app.tasks import health
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable, ungrant

pytestmark = pytest.mark.integration


def status_of(space: dict[str, Any], category: str) -> int:
    return next(s["id"] for s in space["statuses"] if s["category"] == category)


def test_no_active_sprint_and_access(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    path = f"{API}/tasks/spaces/{bp_of(world)}/health"
    got = client.get(path, headers=as_user(world.admin))
    assert got.status_code == 200 and got.json() == {"sprint": None}
    ungrant(db_session, world)
    assert client.get(path, headers=as_user(world.outsider)).status_code == 404
    assert (
        client.get(f"{API}/tasks/spaces/999999/health", headers=as_user(world.admin)).status_code
        == 404
    )
    # The overview leaves out what one cannot see.
    rows = client.get(f"{API}/tasks/overview", headers=as_user(world.outsider)).json()
    assert all(r["business_project_id"] != bp_of(world) for r in rows)


def test_sprint_health_and_overview(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    sprint = client.post(f"{API}/tasks/spaces/{bp}/sprints", headers=admin, json={}).json()
    sid = sprint["id"]
    done_task = create(
        client, admin, world, title="Xong", story_points=4, sprint_id=sid, assignee_id=world.an.id
    )
    slow = create(
        client, admin, world, title="Chậm", story_points=6, sprint_id=sid, assignee_id=world.binh.id
    )
    blocked = create(client, admin, world, title="Bị chặn", story_points=2, sprint_id=sid)
    blocker = create(client, admin, world, title="Chặn")
    create(client, admin, world, type="bug", priority="highest", sprint_id=sid)
    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    res = client.post(
        f"{API}/tasks/sprints/{sid}/start",
        headers=admin,
        json={
            "start_date": (today - timedelta(days=4)).isoformat(),
            "end_date": (today + timedelta(days=4)).isoformat(),
        },
    )
    assert res.status_code == 200, res.text
    done, doing, todo = (status_of(space, c) for c in ("done", "doing", "todo"))
    path = f"{API}/tasks/{done_task['key']}/transition"
    client.post(path, headers=admin, json={"status_id": done})
    # Reopened, then done again: counted once.
    client.post(path, headers=admin, json={"status_id": todo})
    client.post(path, headers=admin, json={"status_id": done})
    client.post(f"{API}/tasks/{slow['key']}/transition", headers=admin, json={"status_id": doing})
    client.post(
        f"{API}/tasks/{blocker['key']}/links",
        headers=admin,
        json={"key": blocked["key"], "kind": "blocks"},
    )
    # The slow one entered "doing" four days ago; it is linked to the world's MR, open for two
    # days, never reviewed, whose review has an open critical finding.
    row = db_session.scalars(select(Task).where(Task.key == slow["key"])).one()
    row.status_changed_at = datetime.now(UTC) - timedelta(days=4)
    world.mr.state = "opened"
    world.mr.created_at_src = datetime.now(UTC) - timedelta(days=2)
    db_session.add(
        TaskGitLink(
            task_id=row.id,
            kind="mr",
            project_id=world.project.id,
            mr_iid=world.mr.iid,
            state="opened",
        )
    )
    db_session.flush()

    got = client.get(f"{API}/tasks/spaces/{bp}/health", headers=as_user(world.an))
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["sprint"]["id"] == sid
    fc = body["forecast"]
    assert fc["done_points"] == 4.0 and fc["scope_points"] == 12.0
    # 4 points in 4 days, 8 days long: 8 of 12 points by the end.
    assert fc["projected_done_points"] == 8.0 and fc["projected_pct"] == 66.7
    assert len(fc["days"]) == 9
    stuck = body["stuck"]
    assert [a["key"] for a in stuck["aged"]] == [slow["key"]]
    assert stuck["aged"][0]["limit_hours"] == health.DOING_WARNING_H
    assert [m["iid"] for m in stuck["mrs_waiting"]] == [world.mr.iid]
    assert stuck["blocked"] == [
        {
            "key": blocked["key"],
            "title": "Bị chặn",
            "assignee_id": None,
            "blocked_by": [blocker["key"]],
        }
    ]
    load = body["load"]
    assert [r["name"] for r in load] == ["Binh", "Chưa giao"]
    assert load[0]["open"] == 1 and load[0]["open_points"] == 6.0
    q = body["quality"]
    assert q["open_critical"] >= 1 and q["reopened"] == 1
    assert q["bugs_created"] == 1 and q["highest_bugs"] == 1
    assert body["health"] == "red"

    rows = client.get(f"{API}/tasks/overview", headers=admin).json()
    mine = next(r for r in rows if r["business_project_id"] == bp)
    assert mine["sprint"]["name"] == sprint["name"]
    assert mine["projected_pct"] == 66.7 and mine["highest_bugs"] == 1
    assert mine["open_critical"] >= 1 and mine["health"] == "red"
    assert mine["lead_time_h"] is not None and mine["next_version"] is None


def test_health_colour_thresholds() -> None:
    assert health.health_color(None, 0, 0) == "green"
    assert health.health_color(95, 0, 0) == "green"
    assert health.health_color(85, 0, 0) == "amber"
    assert health.health_color(65, 0, 0) == "red"
    assert health.health_color(100, 1, 0) == "red"
    assert health.health_color(100, 0, 2) == "red"


def test_next_version_progress(client: TestClient, world: World, as_user: Any) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    later = client.post(
        f"{API}/tasks/spaces/{bp}/versions",
        headers=admin,
        json={"name": "2.0", "release_date": "2099-01-01"},
    ).json()
    soon = client.post(
        f"{API}/tasks/spaces/{bp}/versions",
        headers=admin,
        json={"name": "1.0", "release_date": "2098-01-01"},
    ).json()
    a = create(client, admin, world, version_id=soon["id"])
    create(client, admin, world, version_id=soon["id"])
    create(client, admin, world, version_id=later["id"])
    client.post(
        f"{API}/tasks/{a['key']}/transition",
        headers=admin,
        json={"status_id": status_of(space, "done")},
    )
    rows = client.get(f"{API}/tasks/overview", headers=admin).json()
    mine = next(r for r in rows if r["business_project_id"] == bp)
    assert mine["next_version"]["name"] == "1.0"
    assert mine["next_version"]["done_pct"] == 50.0
    assert mine["sprint"] is None and mine["health"] == "green"
