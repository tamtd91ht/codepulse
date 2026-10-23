"""Per-dự-án dashboards: /metrics/projects and /metrics/projects/{id}."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import BusinessProject, Commit, Project, Review, ReviewFinding
from tests.api.conftest import World

pytestmark = pytest.mark.integration

WEEK = "2026-10-05"  # Monday of the week that holds the world's reviews (2026-10-06)


@pytest.fixture
def omicrm(db_session: Session, world: World) -> BusinessProject:
    bp = BusinessProject(name="OmiCRM", description="CRM")
    db_session.add(bp)
    db_session.flush()
    world.project.business_project_id = bp.id
    # A second repo, unassigned, with a review the week before (previous period).
    other = Project(
        connection_id=world.project.connection_id, external_id=1002, path_with_namespace="demo/q"
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(Commit(sha="c" * 40, project_id=other.id))
    db_session.flush()
    prev = Review(
        target_type="commit",
        commit_sha="c" * 40,
        reviewed_sha="c" * 40,
        status="done",
        author_user_id=world.binh.id,
        score=Decimal(90),
        finished_at=datetime(2026, 9, 30, 9, tzinfo=UTC),
        rubric_version="v1",
    )
    db_session.add(prev)
    db_session.flush()
    db_session.add(ReviewFinding(review_id=prev.id, severity="high", category="bug", title="y"))
    db_session.flush()
    return bp


def test_overview_groups_reviews_by_project(
    client: TestClient, world: World, omicrm: BusinessProject, as_user: Any
) -> None:
    # Anonymous aggregates: a developer may read them too.
    res = client.get(
        f"/api/v1/metrics/projects?period=week&start={WEEK}", headers=as_user(world.an)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["period_start"] == WEEK and len(body["trend_starts"]) == 8
    assert body["totals"]["reviews"] == 1 and body["totals"]["avg_score"] == 50.0
    assert body["totals"]["critical"] == 1 and body["totals"]["open_severe"] == 2
    assert body["totals"]["delta"]["reviews"] == 0  # one review last week too (demo/q)
    names = [p["name"] for p in body["projects"]]
    assert names == ["OmiCRM", "Chưa gán dự án"]
    omi, unassigned = body["projects"]
    assert (
        omi["repos"] == 1 and omi["reviews"] == 1 and omi["severe"] == 1 and omi["open_severe"] == 1
    )
    assert omi["trend"][-1] == 50.0 and omi["trend"][-2] is None
    assert (
        unassigned["id"] is None and unassigned["reviews"] == 0 and unassigned["open_severe"] == 1
    )
    assert unassigned["delta"]["reviews"] == -1


def test_overview_rejects_future_periods(client: TestClient, world: World, as_user: Any) -> None:
    res = client.get("/api/v1/metrics/projects?start=2999-01-05", headers=as_user(world.an))
    assert res.status_code == 422


def test_project_detail_has_repos_series_categories_and_scoped_recent(
    client: TestClient, world: World, omicrm: BusinessProject, as_user: Any
) -> None:
    url = f"/api/v1/metrics/projects/{omicrm.id}?period=week&start={WEEK}"
    body = client.get(url, headers=as_user(world.lead)).json()
    assert body["project"]["name"] == "OmiCRM"
    assert body["totals"]["reviews"] == 1 and body["totals"]["repos"] == 1
    repo = body["repos"][0]
    assert (
        repo["path_with_namespace"] == "demo/p"
        and repo["avg_score"] == 50.0
        and repo["open_severe"] == 1
    )
    assert len(body["series"]) == 12 and body["series"][-1]["reviews"] == 1
    assert body["categories"] == [{"category": "security", "count": 1}]
    # The lead of Backend sees An's and Binh's reviews; an outsider sees none of them.
    assert {r["id"] for r in body["recent"]} == {world.an_review.id, world.binh_review.id}
    outsider = client.get(url, headers=as_user(world.outsider)).json()
    assert outsider["recent"] == [] and outsider["totals"]["reviews"] == 1
    assert (
        client.get("/api/v1/metrics/projects/999", headers=as_user(world.lead)).status_code == 404
    )
