"""docs/specs/31 §20.6 (round 2 stage 2.6): sprint review, scope change after the start, the
forecast range, 4-sprint trends of the overview and the review shown when no sprint runs."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuditLog, Sprint, Task, TaskGitLink
from app.tasks import sprint_review
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable, ungrant

pytestmark = pytest.mark.integration
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def status_of(space: dict[str, Any], category: str) -> int:
    return next(s["id"] for s in space["statuses"] if s["category"] == category)


def task_row(session: Session, key: str) -> Task:
    return session.scalars(select(Task).where(Task.key == key)).one()


def test_review_scope_change_and_retro(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    sid = client.post(f"{API}/tasks/spaces/{bp}/sprints", headers=admin, json={}).json()["id"]
    a = create(client, admin, world, title="A", story_points=3, sprint_id=sid)
    b = create(client, admin, world, title="B", story_points=5, sprint_id=sid)
    today = datetime.now(TZ).date()
    res = client.post(
        f"{API}/tasks/sprints/{sid}/start",
        headers=admin,
        json={
            "start_date": (today - timedelta(days=2)).isoformat(),
            "end_date": (today + timedelta(days=5)).isoformat(),
        },
    )
    assert res.status_code == 200, res.text
    # One test transaction has one now(): the start moves an hour back so later rows follow it.
    db_session.execute(
        update(AuditLog)
        .where(AuditLog.target == f"sprint:{sid}", AuditLog.action == "task.sprint.start")
        .values(at=datetime.now(UTC) - timedelta(hours=1))
    )
    for planned in (a, b):
        task_row(db_session, planned["key"]).created_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.flush()

    c = create(client, admin, world, title="C", story_points=2, sprint_id=sid)
    d = create(client, admin, world, title="D", story_points=1)
    assert (
        client.patch(f"{API}/tasks/{d['key']}", headers=admin, json={"sprint_id": sid}).status_code
        == 200
    )
    assert (
        client.patch(f"{API}/tasks/{b['key']}", headers=admin, json={"sprint_id": None}).status_code
        == 200
    )
    client.post(
        f"{API}/tasks/{a['key']}/transition",
        headers=admin,
        json={"status_id": status_of(space, "done")},
    )
    create(client, admin, world, type="bug", priority="highest", title="Sập")
    row = task_row(db_session, a["key"])
    assert row.resolved_at is not None
    row.started_at = row.resolved_at - timedelta(hours=10)
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

    got = client.get(f"{API}/tasks/sprints/{sid}/review", headers=admin)
    assert got.status_code == 200, got.text
    r = got.json()
    assert r["done"]["keys"] == [a["key"]] and r["done"]["points"] == 3.0
    assert r["committed_points"] == 8.0 and r["done_pct"] == 37.5
    assert r["carried_over"]["keys"] == [c["key"], d["key"]]
    assert r["carried_over"]["points"] == 3.0
    assert r["added_after_start"]["keys"] == [c["key"], d["key"]]
    assert r["added_after_start"]["points"] == 3.0
    assert r["removed_after_start"]["keys"] == [b["key"]]
    assert r["bugs_created"]["count"] == 1 and r["bugs_created"]["highest"] == 1
    assert r["findings"] == {"critical": 1, "high": 0}
    assert r["cycle_time_h"] == 10.0
    assert r["can_create_retro"] is True
    for heading in ("## Điều tốt", "## Cần cải thiện", "## Hành động", f"**{a['key']}** A"):
        assert heading in r["markdown"]

    # The retro page goes through the documents API.
    doc = client.post(
        f"{API}/docs",
        headers=admin,
        json={"business_project_id": bp, "title": "Retro", "body": r["markdown"]},
    )
    assert doc.status_code == 201, doc.text

    # Someone without docs.edit sees the review, not the button; outsiders get 404.
    seen = client.get(f"{API}/tasks/sprints/{sid}/review", headers=as_user(world.an)).json()
    assert seen["can_create_retro"] is False
    ungrant(db_session, world)
    path = f"{API}/tasks/sprints/{sid}/review"
    assert client.get(path, headers=as_user(world.outsider)).status_code == 404
    assert client.get(f"{API}/tasks/sprints/999999/review", headers=admin).status_code == 404

    # The same scope change in the health endpoint and in the Backlog's light endpoint.
    health = client.get(f"{API}/tasks/spaces/{bp}/health", headers=admin).json()
    assert health["scope_change"]["added_after_start"]["count"] == 2
    assert health["scope_change"]["removed_after_start"]["keys"] == [b["key"]]
    assert health["forecast"]["range"] is None and health["forecast"]["few_data"] is True
    scope = client.get(f"{API}/tasks/sprints/{sid}/scope-change", headers=admin).json()
    assert scope["added_after_start"]["keys"] == [c["key"], d["key"]]

    # Closed: the carry-over count comes from the close; the last closed review stands in for
    # the empty Sức khoẻ tab.
    done = client.post(f"{API}/tasks/sprints/{sid}/complete", headers=admin, json={"move_to": None})
    assert done.json()["moved"] == 2
    closed = client.get(path, headers=admin).json()
    assert closed["sprint"]["state"] == "closed"
    assert closed["carried_over"]["count"] == 2 and closed["carried_over"]["points"] is None
    # D's move in is in its history; C was created straight into the sprint and the close moved
    # it without history, so a closed sprint no longer knows C came in late (spec §20.6).
    assert closed["added_after_start"]["keys"] == [d["key"]]
    latest = client.get(f"{API}/tasks/spaces/{bp}/review/latest", headers=admin).json()
    assert latest["mode"] == "scrum" and latest["review"]["sprint"]["id"] == sid
    assert client.get(f"{API}/tasks/spaces/{bp}/health", headers=admin).json() == {"sprint": None}


def test_latest_without_sprints_and_kanban(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    latest = client.get(f"{API}/tasks/spaces/{bp}/review/latest", headers=admin).json()
    assert latest == {"mode": "scrum", "review": None, "flow": None}
    res = client.put(
        f"{API}/tasks/spaces/{bp}", headers=admin, json={"key": "CRM", "board_mode": "kanban"}
    )
    assert res.status_code == 200, res.text
    t = create(client, admin, world, story_points=2)
    client.post(
        f"{API}/tasks/{t['key']}/transition",
        headers=admin,
        json={"status_id": status_of(space, "done")},
    )
    flow = client.get(f"{API}/tasks/spaces/{bp}/review/latest", headers=admin).json()
    assert flow["mode"] == "kanban" and flow["review"] is None
    assert flow["flow"]["throughput"] == 1 and flow["flow"]["throughput_points"] == 2.0
    assert flow["flow"]["created"] == 1


def test_trends_and_velocities(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    now = datetime.now(UTC)
    today = datetime.now(TZ).date()
    done_id = status_of(space, "done")
    sprints = []
    # Three closed sprints, oldest first: lead times 10, 20, 40 hours.
    for i, (lead, pts) in enumerate(((10, 3), (20, 5), (40, 8))):
        sid = client.post(f"{API}/tasks/spaces/{bp}/sprints", headers=admin, json={}).json()["id"]
        t = create(client, admin, world, story_points=pts, sprint_id=sid)
        client.post(
            f"{API}/tasks/{t['key']}/transition", headers=admin, json={"status_id": done_id}
        )
        start = today - timedelta(days=30 - 10 * i)
        closed = now - timedelta(days=20 - 10 * i, hours=1)
        sprint = db_session.get(Sprint, sid)
        assert sprint is not None
        sprint.state, sprint.start_date, sprint.closed_at = "closed", start, closed
        row = task_row(db_session, t["key"])
        row.resolved_at = closed - timedelta(hours=2)
        row.created_at = row.resolved_at - timedelta(hours=lead)
        sprints.append(sprint)
    bug = create(client, admin, world, type="bug", priority="highest")
    task_row(db_session, bug["key"]).created_at = now - timedelta(days=5)
    # The critical finding opened during the oldest sprint, on code linked to a task.
    world.an_finding.created_at = now - timedelta(days=25)
    db_session.add(
        TaskGitLink(
            task_id=task_row(db_session, bug["key"]).id,
            kind="mr",
            project_id=world.project.id,
            mr_iid=world.mr.iid,
        )
    )
    db_session.flush()

    assert sprint_review.closed_velocities(db_session, bp) == [8.0, 5.0, 3.0]
    rows = client.get(f"{API}/tasks/overview", headers=admin).json()
    mine = next(r for r in rows if r["business_project_id"] == bp)
    tr = mine["trends"]
    assert tr["sprints"] == [s.name for s in sprints]
    assert tr["lead_time_h"] == {"values": [10.0, 20.0, 40.0], "direction": "up"}
    assert tr["highest_bugs"] == {"values": [0, 0, 1], "direction": "up"}
    assert tr["critical_findings"] == {"values": [1, 0, 0], "direction": "flat"}


def test_forecast_range_rules() -> None:
    # Too little: two days, two closed sprints.
    assert (
        sprint_review.forecast_range(
            done_points=4,
            scope=20,
            daily=[2, 2],
            remaining_days=8,
            velocities=[10, 12],
            min_days=4,
        )
        is None
    )
    # Daily pace 1..3 (mean 2, spread 1) over 6 more days from 8 done.
    band = sprint_review.forecast_range(
        done_points=8, scope=40, daily=[1, 3, 1, 3], remaining_days=6, velocities=[], min_days=4
    )
    assert band is not None and band["basis"] == ["daily"]
    assert band["low_points"] == 14.0 and band["high_points"] == 26.0
    assert band["low_pct"] == 35.0 and band["high_pct"] == 65.0
    # Velocity only (three closed sprints), capped at the scope.
    band = sprint_review.forecast_range(
        done_points=0, scope=12, daily=[], remaining_days=10, velocities=[10, 12, 14], min_days=4
    )
    assert band is not None and band["basis"] == ["velocity"]
    assert band["high_points"] == 12.0 and band["low_points"] == 10.4
