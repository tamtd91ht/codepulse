"""A finding judged not an error never shows a waiting "chưa đủ điều kiện đóng" next to it, and
may be closed by hand (user report 2026-11-04)."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FindingClosure, Review, ReviewFinding
from app.review.rubric import RUBRIC_VERSION
from tests.api.conftest import World

pytestmark = pytest.mark.integration


def accept_dispute(client: TestClient, world: World, as_user: Any) -> None:
    raised = client.post(
        f"/api/v1/findings/{world.an_finding.id}/dispute",
        headers=as_user(world.an),
        json={"reason": "The input is validated by the caller."},
    ).json()
    decided = client.post(
        f"/api/v1/disputes/{raised['id']}/decide",
        headers=as_user(world.lead),
        json={"decision": "accepted"},
    )
    assert decided.status_code == 200


def test_an_accepted_dispute_closes_the_waiting_request_of_the_same_finding_elsewhere(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    world.an_finding.fingerprint = "fp-1"
    newer = Review(
        target_type="mr",
        mr_id=world.mr.id,
        reviewed_sha="d" * 40,
        status="done",
        author_user_id=world.an.id,
        score=Decimal(50),
        finished_at=datetime(2026, 10, 7, tzinfo=UTC),
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(newer)
    db_session.flush()
    copy = ReviewFinding(
        review_id=newer.id,
        severity="critical",
        category="security",
        file_path="app/a.py",
        line_start=3,
        title="SQL injection",
        confidence=Decimal("0.9"),
        fingerprint="fp-1",
    )
    db_session.add(copy)
    db_session.flush()
    waiting = FindingClosure(
        finding_id=copy.id, kind="request", status="unmet", reason="Chưa đủ điều kiện đóng"
    )
    db_session.add(waiting)
    db_session.flush()
    accept_dispute(client, world, as_user)
    db_session.refresh(waiting)
    assert (waiting.status, waiting.closed_as) == ("closed", "false_positive")
    assert waiting.reason is not None and waiting.reason.startswith("Finding được duyệt")


def test_a_finding_judged_wrong_can_be_closed_by_hand_and_not_reopened_by_the_close_flow(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    old = FindingClosure(
        finding_id=world.an_finding.id,
        kind="request",
        status="rejected",
        reason="Chưa đủ điều kiện đóng — các commit này chưa sửa lỗi",
    )
    db_session.add(old)
    db_session.flush()
    url = f"/api/v1/findings/{world.an_finding.id}/close-decided"
    assert client.post(url, headers=as_user(world.admin), json={}).status_code == 409  # still open
    accept_dispute(client, world, as_user)
    assert client.post(url, headers=as_user(world.binh), json={}).status_code == 403
    closed = client.post(url, headers=as_user(world.lead), json={"note": "đã thống nhất"})
    assert closed.status_code == 200
    body = closed.json()
    assert (body["status"], body["closed_as"]) == ("closed", "false_positive")
    assert client.post(url, headers=as_user(world.lead), json={}).status_code == 409  # done
    rows = db_session.scalars(
        select(FindingClosure).where(FindingClosure.finding_id == world.an_finding.id)
    ).all()
    assert len(rows) == 2  # the old refusal stays as history
    reopen = client.post(
        f"/api/v1/findings/{world.an_finding.id}/reopen",
        headers=as_user(world.admin),
        json={"note": "mở lại"},
    )
    assert reopen.status_code == 409  # decided by the dispute, not by a close


def test_a_closing_by_the_system_is_recorded_after_a_refused_request(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """The duplicate case: the request on one copy was refused, then the same finding was closed
    another way (a new push no longer reports it). History must say closed, not only refused."""
    from app.review.status import mark_fixed_findings

    world.an_finding.fingerprint = "fp-dup"
    refused = FindingClosure(
        finding_id=world.an_finding.id,
        kind="request",
        status="rejected",
        reason="Chưa đủ điều kiện đóng",
    )
    db_session.add(refused)
    newer = Review(
        target_type="mr",
        mr_id=world.mr.id,
        reviewed_sha="e" * 40,
        status="done",
        author_user_id=world.an.id,
        finished_at=datetime(2026, 10, 8, tzinfo=UTC),
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(newer)
    db_session.flush()
    assert mark_fixed_findings(db_session, world.mr.id, newer.id) == [world.an_finding.id]
    rows = db_session.scalars(
        select(FindingClosure)
        .where(FindingClosure.finding_id == world.an_finding.id)
        .order_by(FindingClosure.id)
    ).all()
    assert [(r.status, r.closed_as, r.kind) for r in rows] == [
        ("rejected", None, "request"),
        ("closed", "fixed", "auto"),
    ]
    assert rows[-1].review_id == newer.id
    history = client.get(
        "/api/v1/close-requests?view=history", headers=as_user(world.admin)
    ).json()["items"]
    mine = [i for i in history if i["finding"]["id"] == world.an_finding.id]
    assert mine[0]["status"] == "closed"  # newest first: the closing, then the refusal
    assert {i["finding"]["status"] for i in mine} == {"fixed"}
