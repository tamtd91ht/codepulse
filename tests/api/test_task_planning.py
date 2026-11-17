"""docs/specs/31 §21.3 (round 3 stage 3.3): timeline by epic, the multi-project timeline, sprint
capacity (auto / manual), load per person and the preflight before starting a sprint."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Sprint
from app.tasks import roadmap
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable, ungrant

pytestmark = pytest.mark.integration
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def status_of(space: dict[str, Any], category: str) -> int:
    return next(s["id"] for s in space["statuses"] if s["category"] == category)


def iso(d: date) -> str:
    return d.isoformat()


def test_window_defaults_and_limits() -> None:
    today = date(2026, 11, 18)  # a Wednesday
    lo, hi = roadmap.window(None, None, today)
    assert lo == date(2026, 11, 2) and lo.weekday() == 0
    assert (hi - lo).days == 83
    assert roadmap.window(date(2026, 1, 1), None, today)[1] == date(2026, 3, 25)
    with pytest.raises(ValueError):
        roadmap.window(date(2026, 2, 1), date(2026, 1, 1), today)
    with pytest.raises(ValueError):
        roadmap.window(date(2026, 1, 1), date(2027, 6, 1), today)


def test_space_timeline(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    today = datetime.now(TZ).date()
    sid = client.post(f"{API}/tasks/spaces/{bp}/sprints", headers=admin, json={}).json()["id"]
    res = client.post(
        f"{API}/tasks/sprints/{sid}/start",
        headers=admin,
        json={
            "start_date": iso(today - timedelta(days=3)),
            "end_date": iso(today + timedelta(days=4)),
        },
    )
    assert res.status_code == 200, res.text
    epic = create(client, admin, world, type="epic", title="Thanh toán")
    kw = {"parent_key": epic["key"], "type": "story"}
    in_sprint = create(
        client, admin, world, title="Trong sprint", story_points=3, sprint_id=sid, **kw
    )
    late = create(
        client,
        admin,
        world,
        title="Trễ",
        story_points=2,
        due_date=iso(today - timedelta(days=2)),
        **kw,
    )
    done = create(
        client,
        admin,
        world,
        title="Xong",
        story_points=5,
        due_date=iso(today + timedelta(days=1)),
        **kw,
    )
    blocker = create(
        client,
        admin,
        world,
        title="Chặn",
        story_points=1,
        due_date=iso(today + timedelta(days=9)),
        **kw,
    )
    loose = create(client, admin, world, title="Lẻ", due_date=iso(today + timedelta(days=6)))
    create(client, admin, world, title="Không hạn")  # no sprint, no due date: not on the timeline
    assert (
        client.post(
            f"{API}/tasks/{done['key']}/transition",
            headers=admin,
            json={"status_id": status_of(space, "done")},
        ).status_code
        == 200
    )
    link = client.post(
        f"{API}/tasks/{blocker['key']}/links",
        headers=admin,
        json={"key": in_sprint["key"], "kind": "blocks"},
    )
    assert link.status_code == 201, link.text
    version = client.post(
        f"{API}/tasks/spaces/{bp}/versions",
        headers=admin,
        json={"name": "v1.0", "release_date": iso(today + timedelta(days=20))},
    )
    assert version.status_code == 201, version.text

    got = client.get(f"{API}/tasks/spaces/{bp}/timeline", headers=admin)
    assert got.status_code == 200, got.text
    t = got.json()
    assert t["today"] == iso(today)
    assert [e["key"] for e in t["epics"]] == [epic["key"]]
    e = t["epics"][0]
    assert e["children_count"] == 4 and e["done_count"] == 1
    assert e["progress"] == round(5 / 11, 3)
    assert e["end"] == iso(today + timedelta(days=9)) and e["end_source"] == "children"
    bars = {c["key"]: c for c in e["children"]}
    s = bars[in_sprint["key"]]
    assert s["start"] == iso(today - timedelta(days=3)) and s["end"] == iso(
        today + timedelta(days=4)
    )
    assert s["end_source"] == "sprint" and s["can_edit"] is True
    assert s["blocked_late"] is True and s["blockers"] == [blocker["key"]]
    assert bars[late["key"]]["overdue"] is True
    assert bars[late["key"]]["overdue_from"] == iso(today - timedelta(days=2))
    assert bars[done["key"]]["done"] is True and bars[done["key"]]["progress"] == 1.0
    assert bars[done["key"]]["overdue"] is False
    assert [x["key"] for x in t["loose"]] == [loose["key"]]
    assert t["links"] == [{"from": blocker["key"], "to": in_sprint["key"], "late": True}]
    assert [w["key"] for w in t["warnings"]] == [in_sprint["key"]]
    assert "bị chặn quá hạn" in t["warnings"][0]["text"]
    assert [x["id"] for x in t["sprints"]] == [sid]
    assert [v["name"] for v in t["versions"]] == ["v1.0"]
    assert t["can_sprint"] is True

    # A window that leaves the version out; a bad window is 422.
    far = client.get(
        f"{API}/tasks/spaces/{bp}/timeline",
        headers=admin,
        params={"from": iso(today - timedelta(days=10)), "to": iso(today + timedelta(days=10))},
    ).json()
    assert far["versions"] == []
    bad = client.get(
        f"{API}/tasks/spaces/{bp}/timeline",
        headers=admin,
        params={"from": iso(today), "to": iso(today - timedelta(days=1))},
    )
    assert bad.status_code == 422

    # Another member sees the bars but may not edit the admin's tasks.
    an_view = client.get(f"{API}/tasks/spaces/{bp}/timeline", headers=as_user(world.an)).json()
    assert an_view["epics"][0]["children"][0]["can_edit"] is False

    # The Manager view: epics only, of dự án one sees.
    many = client.get(f"{API}/tasks/timeline", headers=admin, params={"space": [bp, 999999]})
    assert many.status_code == 200, many.text
    m = many.json()
    assert [x["business_project_id"] for x in m["spaces"]] == [bp]
    assert m["spaces"][0]["key"] == "CRM"
    assert m["spaces"][0]["epics"][0]["children"] == []
    assert m["spaces"][0]["epics"][0]["children_count"] == 4
    assert client.get(f"{API}/tasks/timeline", headers=admin).json()["spaces"][0]["key"] == "CRM"

    ungrant(db_session, world)
    outsider = as_user(world.outsider)
    assert client.get(f"{API}/tasks/spaces/{bp}/timeline", headers=outsider).status_code == 404
    assert client.get(f"{API}/tasks/timeline", headers=outsider).json()["spaces"] == []


def test_capacity_load_and_preflight(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    sid = client.post(f"{API}/tasks/spaces/{bp}/sprints", headers=admin, json={}).json()["id"]
    create(client, admin, world, title="A", story_points=6, sprint_id=sid, assignee_id=world.an.id)
    create(
        client, admin, world, title="B", story_points=2, sprint_id=sid, assignee_id=world.binh.id
    )
    create(
        client, admin, world, title="C", story_points=1, sprint_id=sid, assignee_id=world.binh.id
    )
    no_points = create(client, admin, world, title="D", sprint_id=sid, assignee_id=world.an.id)
    nobody = create(client, admin, world, title="E", story_points=1, sprint_id=sid)
    blocked = create(
        client, admin, world, title="F", story_points=1, sprint_id=sid, assignee_id=world.binh.id
    )
    outside = create(client, admin, world, title="Ngoài sprint")
    assert (
        client.post(
            f"{API}/tasks/{outside['key']}/links",
            headers=admin,
            json={"key": blocked["key"], "kind": "blocks"},
        ).status_code
        == 201
    )

    plan = client.get(f"{API}/tasks/sprints/{sid}/plan", headers=admin).json()
    assert plan["points"] == 11.0
    assert plan["capacity"] is None and plan["source"] == "auto" and plan["auto"] is None
    by_name = {r["name"]: r for r in plan["load"]}
    assert by_name["An"]["points"] == 6.0 and by_name["An"]["pct"] == 54.5
    assert by_name["An"]["over"] is True and by_name["Binh"]["over"] is False
    assert by_name["Chưa giao"]["over"] is False
    assert plan["overloaded"] == ["An"] and plan["can_sprint"] is True

    # Manual capacity when there is no history; only who plans sprints sets it.
    path = f"{API}/tasks/sprints/{sid}/capacity"
    assert (
        client.patch(path, headers=as_user(world.an), json={"capacity_points": 9}).status_code
        == 403
    )
    res = client.patch(path, headers=admin, json={"capacity_points": 10})
    assert res.status_code == 200, res.text
    assert res.json()["capacity"] == 10.0 and res.json()["source"] == "manual"
    assert res.json()["over_capacity"] is True
    sprint = db_session.get(Sprint, sid)
    assert sprint is not None and sprint.capacity_source == "manual"
    assert client.patch(path, headers=admin, json={"capacity_points": -1}).status_code == 422

    pre = client.get(f"{API}/tasks/sprints/{sid}/preflight", headers=admin).json()
    assert pre["ok"] is False
    kinds = {w["kind"]: w for w in pre["warnings"]}
    assert [t["key"] for t in kinds["unestimated"]["tasks"]] == [no_points["key"]]
    assert [t["key"] for t in kinds["unassigned"]["tasks"]] == [nobody["key"]]
    assert kinds["blocked_outside"]["tasks"][0]["key"] == blocked["key"]
    assert kinds["blocked_outside"]["tasks"][0]["blocker"]["key"] == outside["key"]
    assert "over_capacity" in kinds and "overloaded" in kinds

    # Starting anyway still works (warnings only); back to auto.
    today = datetime.now(TZ).date()
    started = client.post(
        f"{API}/tasks/sprints/{sid}/start",
        headers=admin,
        json={"start_date": iso(today), "end_date": iso(today + timedelta(days=13))},
    )
    assert started.status_code == 200, started.text
    back = client.patch(path, headers=admin, json={"capacity_points": None}).json()
    assert back["source"] == "auto" and back["capacity"] is None

    # A done blocker no longer warns; outsiders get 404.
    assert (
        client.post(
            f"{API}/tasks/{outside['key']}/transition",
            headers=admin,
            json={"status_id": status_of(space, "done")},
        ).status_code
        == 200
    )
    pre = client.get(f"{API}/tasks/sprints/{sid}/preflight", headers=admin).json()
    assert "blocked_outside" not in {w["kind"] for w in pre["warnings"]}
    ungrant(db_session, world)
    outsider = as_user(world.outsider)
    assert client.get(f"{API}/tasks/sprints/{sid}/plan", headers=outsider).status_code == 404
    assert client.get(f"{API}/tasks/sprints/{sid}/preflight", headers=outsider).status_code == 404
