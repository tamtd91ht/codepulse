"""GET /projects/{id}/knowledge/stats (docs/specs/19 §5) against a real database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import FindingDispute, Review, ReviewFinding
from app.review.knowledge_stats import rule_stats
from tests.api.conftest import World

pytestmark = pytest.mark.integration


def review(
    session: Session,
    world: World,
    sha: str,
    *,
    days_ago: int,
    applied: list[str],
    status: str = "done",
) -> Review:
    row = Review(
        target_type="mr",
        mr_id=world.mr.id,
        reviewed_sha=sha,
        status=status,
        author_user_id=world.an.id,
        score=Decimal(70),
        finished_at=datetime.now(UTC) - timedelta(days=days_ago),
        basis={
            "knowledge": {
                "version": 1,
                "applied_rules": [{"rule_id": r, "applies": True, "where": "x"} for r in applied],
            }
        },
    )
    session.add(row)
    session.flush()
    return row


def finding(session: Session, review_id: int, rule_id: str, verified: str) -> ReviewFinding:
    row = ReviewFinding(
        review_id=review_id,
        severity="high",
        category="security",
        rule_id=rule_id,
        verified=verified,
        title=rule_id,
    )
    session.add(row)
    session.flush()
    return row


def seed(session: Session, world: World) -> dict[str, Any]:
    recent = review(session, world, "1" * 40, days_ago=3, applied=["rk:inv-1", "rk:inv-2"])
    older = review(session, world, "2" * 40, days_ago=60, applied=["rk:inv-2"])
    stale = review(session, world, "3" * 40, days_ago=200, applied=["rk:inv-3"])
    # a later push replaced this one: still history, still counted
    replaced = review(session, world, "4" * 40, days_ago=2, applied=[], status="superseded")
    finding(session, replaced.id, "rk:inv-4", "confirmed")
    running = review(session, world, "5" * 40, days_ago=1, applied=["rk:inv-5"], status="running")
    finding(session, running.id, "rk:inv-5", "skipped")
    f1 = finding(session, recent.id, "rk:inv-1", "confirmed")
    finding(session, recent.id, "rk:inv-1", "rejected")
    finding(session, recent.id, "rubric:2", "confirmed")
    finding(session, older.id, "rk:inv-1", "rejected")
    finding(session, stale.id, "rk:inv-3", "confirmed")
    session.add(
        FindingDispute(
            finding_id=f1.id,
            raised_by=world.an.id,
            reason="n/a",
            decision="accepted",
            decided_by=world.lead.id,
            decided_at=datetime.now(UTC),
        )
    )
    session.flush()
    return {"recent": recent, "older": older}


def test_rule_stats_30_and_90_days(db_session: Session, world: World) -> None:
    seed(db_session, world)
    month = rule_stats(db_session, world.project.id, 30)
    assert month["days"] == 30 and month["reviews"] == 2
    rules = {r["rule_id"]: r for r in month["rules"]}
    assert set(rules) == {"rk:inv-1", "rk:inv-2", "rk:inv-4"}
    assert rules["rk:inv-4"]["fired"] == 1 and rules["rk:inv-4"]["confirmed"] == 1
    inv1 = rules["rk:inv-1"]
    assert inv1["fired"] == 2 and inv1["confirmed"] == 1 and inv1["rejected"] == 1
    assert inv1["disputes_accepted"] == 1
    assert inv1["label"] == "noisy"  # (1 rejected + 1 dispute) / 2 fired
    assert rules["rk:inv-2"] == {
        "rule_id": "rk:inv-2",
        "fired": 0,
        "confirmed": 0,
        "downgraded": 0,
        "rejected": 0,
        "unverified": 0,
        "disputes_accepted": 0,
        "applied_not_fired": 1,
        "label": "dead",
    }

    quarter = rule_stats(db_session, world.project.id, 90)
    assert quarter["reviews"] == 3
    rules = {r["rule_id"]: r for r in quarter["rules"]}
    assert rules["rk:inv-1"]["fired"] == 3 and rules["rk:inv-1"]["rejected"] == 2
    assert rules["rk:inv-2"]["applied_not_fired"] == 2
    assert "rk:inv-3" not in rules  # outside the window


def test_endpoint_is_open_to_any_signed_in_user(
    client: TestClient, db_session: Session, world: World, as_user: Any
) -> None:
    seed(db_session, world)
    response = client.get(
        f"/api/v1/projects/{world.project.id}/knowledge/stats?days=90",
        headers=as_user(world.outsider),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == world.project.id and body["days"] == 90
    assert body["reviews"] == 3 and body["since"]
    assert [r["rule_id"] for r in body["rules"]] == ["rk:inv-1", "rk:inv-4", "rk:inv-2"]

    assert client.get(f"/api/v1/projects/{world.project.id}/knowledge/stats").status_code == 401
    assert (
        client.get(
            f"/api/v1/projects/{world.project.id}/knowledge/stats?days=0",
            headers=as_user(world.an),
        ).status_code
        == 422
    )
    assert (
        client.get("/api/v1/projects/999999/knowledge/stats", headers=as_user(world.an)).status_code
        == 404
    )
