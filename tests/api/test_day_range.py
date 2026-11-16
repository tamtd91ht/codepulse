"""docs/specs/10: the dự án and Team pages take whole days (period=range), "Hôm nay" being
today..today: from 00:00 (REPORT_TZ) to now. An's review finished 2026-10-06 16:00 there."""

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import World

pytestmark = pytest.mark.integration

API = "/api/v1"
DAY = "2026-10-06"
NEXT = "2026-10-07"


def test_a_project_over_one_day(client: TestClient, world: World, as_user: Any) -> None:
    bp = world.project.business_project_id
    admin = as_user(world.admin)
    res = client.get(
        f"{API}/metrics/projects/{bp}?period=range&start={DAY}&end={DAY}", headers=admin
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["period_start"], body["period_end"], body["next_period_start"]) == (DAY, DAY, NEXT)
    assert body["series_period"] == "week"
    assert (body["totals"]["reviews"], body["totals"]["critical"]) == (1, 1)
    assert body["period_range"][0].startswith(f"{DAY}T00:00:00")
    assert [c["category"] for c in body["categories"]] == ["security"]
    # The next day has none; the day before it (An's) is what it is compared with.
    body = client.get(
        f"{API}/metrics/projects/{bp}?period=range&start={NEXT}&end={NEXT}", headers=admin
    ).json()
    assert (body["totals"]["reviews"], body["totals"]["delta"]["reviews"]) == (0, -1)
    # The series still shows the week An's review is in.
    week = next(s for s in body["series"] if s["period_start"] == "2026-10-05")
    assert week["reviews"] == 1
    # Weeks and months as before.
    assert (
        client.get(f"{API}/metrics/projects/{bp}?period=week&start={DAY}", headers=admin).json()[
            "series_period"
        ]
        == "week"
    )


def test_today_is_from_midnight_to_now(client: TestClient, world: World, as_user: Any) -> None:
    bp = world.project.business_project_id
    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
    res = client.get(
        f"{API}/metrics/projects/{bp}?period=range&start={today}&end={today}",
        headers=as_user(world.admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["totals"]["reviews"] == 0
    res = client.get(
        f"{API}/metrics/teams/{world.backend.id}?period=range&start={today}&end={today}",
        headers=as_user(world.lead),
    )
    assert res.status_code == 200, res.text
    assert res.json()["current"] is None


def test_a_team_over_one_day(client: TestClient, world: World, as_user: Any) -> None:
    team = world.backend.id
    lead = as_user(world.lead)
    res = client.get(f"{API}/metrics/teams/{team}?period=range&start={DAY}&end={DAY}", headers=lead)
    assert res.status_code == 200, res.text
    body = res.json()
    current = body["current"]
    assert (current["period_type"], current["period_start"]) == ("range", DAY)
    assert (current["reviews_count"], current["critical"]) == (1, 1)
    assert body["period_range"][0].startswith(f"{DAY}T00:00:00")
    # The chart keeps the stored weeks.
    assert [s["period_start"] for s in body["series"]] == ["2026-10-05"]
    # Against the day before: nothing then.
    assert body["delta"]["critical"] is None or body["delta"]["critical"] >= 0

    board = client.get(
        f"{API}/leaderboard?team_id={team}&period=range&start={DAY}&end={DAY}", headers=lead
    )
    assert board.status_code == 200, board.text
    rows = {m["user"]["id"]: m for m in board.json()["members"]}
    assert rows[world.an.id]["reviews_count"] == 1
    assert len(rows[world.an.id]["trend"]) == 8
    assert board.json()["team_metrics"]["reviews_count"] == 1

    hot = client.get(
        f"{API}/metrics/teams/{team}/hotspots?period=range&start={DAY}&end={DAY}", headers=lead
    ).json()
    assert hot["categories"] == [{"category": "security", "count": 1}]
    hot = client.get(
        f"{API}/metrics/teams/{team}/hotspots?period=range&start={NEXT}&end={NEXT}", headers=lead
    ).json()
    assert hot["categories"] == []


def test_a_manager_sees_the_team_range_without_people(
    client: TestClient, world: World, as_user: Any
) -> None:
    res = client.get(
        f"{API}/leaderboard?team_id={world.backend.id}&period=range&start={DAY}&end={DAY}",
        headers=as_user(world.manager),
    )
    assert res.status_code == 200, res.text
    assert res.json()["members"] is None
    assert res.json()["team_metrics"]["reviews_count"] == 1


def test_bad_ranges(client: TestClient, world: World, as_user: Any) -> None:
    lead = as_user(world.lead)
    team = f"{API}/metrics/teams/{world.backend.id}?period=range"
    assert client.get(f"{team}&start={DAY}", headers=lead).status_code == 422
    assert client.get(f"{team}&start={NEXT}&end={DAY}", headers=lead).status_code == 422
    future = date(2100, 1, 1).isoformat()
    assert client.get(f"{team}&start={future}&end={future}", headers=lead).status_code == 422
    assert client.get(f"{team}&start=2025-01-01&end=2026-10-06", headers=lead).status_code == 422
    bp = world.project.business_project_id
    res = client.get(
        f"{API}/metrics/projects/{bp}?period=range&start={DAY}", headers=as_user(world.admin)
    )
    assert res.status_code == 422
