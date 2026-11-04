"""A decision on a finding holds for the same finding re-reported by another review of the
same MR (user report 2026-11-04: "khiếu nại đã duyệt nhưng finding chưa đóng")."""

from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Review, ReviewFinding
from app.review.rubric import RUBRIC_VERSION
from app.review.status import decided_before
from tests.api.conftest import World

pytestmark = pytest.mark.integration

FP = "fp-sql-injection"


def newer_round(world: World, db_session: Session) -> tuple[Review, ReviewFinding]:
    """A new push of An's MR: its review reports the same SQL injection again, as a new row."""
    world.an_finding.fingerprint = FP
    review = Review(
        target_type="mr",
        mr_id=world.mr.id,
        reviewed_sha="d" * 40,
        status="done",
        author_user_id=world.an.id,
        score=Decimal(50),
        verdict="block",
        finished_at=datetime(2026, 10, 7, 9, tzinfo=UTC),
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(review)
    db_session.flush()
    copy = ReviewFinding(
        review_id=review.id,
        severity="critical",
        category="security",
        file_path="app/a.py",
        line_start=3,
        title="SQL injection",
        confidence=Decimal("0.9"),
        fingerprint=FP,
    )
    db_session.add(copy)
    db_session.flush()
    return review, copy


def test_accepted_dispute_closes_the_copy_in_the_newer_review(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    review, copy = newer_round(world, db_session)
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
    db_session.refresh(copy)
    db_session.refresh(review)
    assert copy.status == "false_positive"
    assert review.verdict == "approve"  # nothing that counts is left
    assert review.score == Decimal("100.00")


def test_rejected_dispute_leaves_the_copy_open(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _, copy = newer_round(world, db_session)
    raised = client.post(
        f"/api/v1/findings/{world.an_finding.id}/dispute",
        headers=as_user(world.an),
        json={"reason": "The input is validated by the caller."},
    ).json()
    client.post(
        f"/api/v1/disputes/{raised['id']}/decide",
        headers=as_user(world.lead),
        json={"decision": "rejected"},
    )
    db_session.refresh(copy)
    assert copy.status == "open"


def test_closed_as_accepted_risk_carries_the_lowered_severity(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _, copy = newer_round(world, db_session)
    closed = client.post(
        f"/api/v1/findings/{world.an_finding.id}/close",
        headers=as_user(world.admin),
        json={"note": "Rủi ro đã được chấp nhận", "outcome": "medium"},
    )
    assert closed.status_code == 200
    db_session.refresh(copy)
    assert (copy.status, copy.severity) == ("accepted_risk", "medium")


def test_a_new_review_reads_what_was_decided(world: World, db_session: Session) -> None:
    review, _ = newer_round(world, db_session)
    world.an_finding.status = "false_positive"
    db_session.flush()
    assert decided_before(db_session, review) == {FP: ("false_positive", "critical")}
    world.an_finding.status = "fixed"  # a fix is not carried: reported again = not fixed
    db_session.flush()
    assert decided_before(db_session, review) == {}


def test_cli_repairs_decisions_made_before(world: World, db_session: Session) -> None:
    from app.cli import build_parser, run

    _, copy = newer_round(world, db_session)
    world.an_finding.status = "false_positive"  # decided before the rule existed
    db_session.flush()
    out = StringIO()
    args = build_parser().parse_args(["findings", "carry-decisions"])
    assert run(args, db_session, None, None, out) is not False  # type: ignore[arg-type]
    db_session.refresh(copy)
    assert copy.status == "false_positive"
    assert out.getvalue() == "1 review(s) updated\n"
