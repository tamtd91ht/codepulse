from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, MetricsUserPeriod
from tests.api.conftest import WEEK, World

pytestmark = pytest.mark.integration


def dispute(client: TestClient, world: World, headers: dict[str, str]) -> Any:
    return client.post(
        f"/api/v1/findings/{world.an_finding.id}/dispute",
        headers=headers,
        json={"reason": "The input is validated by the caller."},
    )


def test_accepted_dispute_clears_the_finding_and_rescores(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    raised = dispute(client, world, as_user(world.an))
    assert raised.status_code == 201
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "disputed"

    pending = client.get("/api/v1/disputes", headers=as_user(world.lead)).json()
    assert [d["id"] for d in pending] == [raised.json()["id"]]

    decided = client.post(
        f"/api/v1/disputes/{raised.json()['id']}/decide",
        headers=as_user(world.lead),
        json={"decision": "accepted"},
    )
    assert decided.status_code == 200
    db_session.refresh(world.an_finding)
    db_session.refresh(world.an_review)
    assert world.an_finding.status == "false_positive"
    assert world.an_review.score == Decimal("100.00")  # was 50 with the critical
    week = db_session.scalars(
        select(MetricsUserPeriod).filter_by(
            user_id=world.an.id, period_type="week", period_start=WEEK
        )
    ).one()
    assert (week.critical, week.avg_score) == (0, Decimal("100.00"))
    actions = set(db_session.scalars(select(AuditLog.action)))
    assert {"dispute.raise", "dispute.decide"} <= actions


def test_rejected_dispute_reopens_the_finding(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    raised = dispute(client, world, as_user(world.an)).json()
    client.post(
        f"/api/v1/disputes/{raised['id']}/decide",
        headers=as_user(world.admin),
        json={"decision": "rejected"},
    )
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"


def test_dispute_permissions(client: TestClient, world: World, as_user: Any) -> None:
    assert dispute(client, world, as_user(world.binh)).status_code == 403  # not the author
    raised = dispute(client, world, as_user(world.an)).json()
    assert dispute(client, world, as_user(world.an)).status_code == 409  # already disputed
    url = f"/api/v1/disputes/{raised['id']}/decide"
    body = {"decision": "accepted"}
    assert client.post(url, headers=as_user(world.an), json=body).status_code == 403  # own
    assert client.post(url, headers=as_user(world.binh), json=body).status_code == 403  # peer
    assert client.post(url, headers=as_user(world.manager), json=body).status_code == 403
    assert client.post(url, headers=as_user(world.lead), json=body).status_code == 200
    assert client.post(url, headers=as_user(world.lead), json=body).status_code == 409


def test_dispute_list_names_the_finding_and_person(
    client: TestClient, world: World, as_user: Any
) -> None:
    dispute(client, world, as_user(world.an))
    (item,) = client.get("/api/v1/disputes", headers=as_user(world.lead)).json()
    assert (item["finding_title"], item["finding_severity"], item["raised_by_name"]) == (
        "SQL injection",
        "critical",
        "An",
    )
    assert item["review_id"] == world.an_review.id
