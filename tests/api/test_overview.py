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
    # Critical and high apart, found in the period and open now.
    totals = body["totals"]
    assert (totals["open_critical"], totals["open_high"]) == (1, 1)
    assert (totals["delta"]["critical"], totals["delta"]["high"]) == (1, -1)
    assert (omi["open_critical"], omi["open_high"]) == (1, 0)
    assert (unassigned["open_critical"], unassigned["open_high"]) == (0, 1)


def test_dashboard_counts_link_to_the_same_findings(
    client: TestClient, world: World, omicrm: BusinessProject, db_session: Session, as_user: Any
) -> None:
    # A repo of the dự án no longer tracked, with an open critical from last week, and a high
    # of this week already fixed: open now vs found in the period must both match the list.
    old = Project(
        connection_id=world.project.connection_id,
        external_id=1003,
        path_with_namespace="demo/old",
        business_project_id=omicrm.id,
        tracked=False,
    )
    db_session.add(old)
    db_session.flush()
    db_session.add(Commit(sha="d" * 40, project_id=old.id))
    db_session.flush()
    stale = Review(
        target_type="commit",
        commit_sha="d" * 40,
        reviewed_sha="d" * 40,
        status="done",
        score=Decimal(70),
        finished_at=datetime(2026, 9, 29, 9, tzinfo=UTC),
        rubric_version="v1",
    )
    db_session.add(stale)
    db_session.flush()
    db_session.add_all(
        [
            ReviewFinding(review_id=stale.id, severity="critical", category="bug", title="z"),
            ReviewFinding(
                review_id=world.an_review.id,
                severity="high",
                category="bug",
                title="fixed",
                status="fixed",
            ),
        ]
    )
    db_session.flush()
    admin = as_user(world.admin)
    body = client.get(f"/api/v1/metrics/projects?period=week&start={WEEK}", headers=admin).json()
    lo, hi = body["period_range"]
    omi = body["projects"][0]

    def findings(**params: Any) -> list[dict[str, Any]]:
        res = client.get("/api/v1/findings", params={"limit": 200, **params}, headers=admin)
        assert res.status_code == 200, res.text
        return list(res.json()["items"])

    new = {"business_project_id": omicrm.id, "found_from": lo, "found_to": hi, "status": "any"}
    assert len(findings(severity="critical", **new)) == omi["critical"] == 1
    # The fixed high was found this week: counted as new, not as open.
    assert len(findings(severity="high", **new)) == omi["high"] == 1
    assert findings(severity="high", business_project_id=omicrm.id) == []
    assert omi["open_high"] == 0
    # Last week's high (demo/q, no dự án) is outside this period and this dự án.
    assert len(findings(severity="high", found_from=lo, found_to=hi, status="any")) == 1
    assert len(findings(severity="high")) == body["totals"]["open_high"] == 1
    # Open now includes the untracked repo, on the card, the detail page and the list alike.
    assert len(findings(severity="critical", business_project_id=omicrm.id)) == 2
    assert omi["open_critical"] == 2
    detail = client.get(
        f"/api/v1/metrics/projects/{omicrm.id}?period=week&start={WEEK}", headers=admin
    ).json()
    assert detail["totals"]["open_critical"] == 2
    assert client.get("/api/v1/findings?status=Any", headers=admin).status_code == 422


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
        and repo["open_critical"] == 1
    )
    assert body["totals"]["open_critical"] == 1 and len(body["period_range"]) == 2
    assert len(body["series"]) == 12 and body["series"][-1]["reviews"] == 1
    assert body["categories"] == [{"category": "security", "count": 1}]
    # The lead of Backend sees An's and Binh's reviews; an outsider sees none of them.
    assert {r["id"] for r in body["recent"]} == {world.an_review.id, world.binh_review.id}
    outsider = client.get(url, headers=as_user(world.outsider)).json()
    assert outsider["recent"] == [] and outsider["totals"]["reviews"] == 1
    assert (
        client.get("/api/v1/metrics/projects/999", headers=as_user(world.lead)).status_code == 404
    )
