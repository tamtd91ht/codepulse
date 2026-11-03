"""docs/specs/21 phase B1: reviews, findings, closings and disputes follow the permission sets.

The built-in sets keep every rule as before (the other test modules); here a set given by hand
widens access, and a permission taken out of a built-in set takes access away.
"""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Commit,
    PermissionSet,
    PermissionSetItem,
    Review,
    ReviewFinding,
    User,
    UserPermissionSet,
)
from app.review.rubric import RUBRIC_VERSION
from tests.api.conftest import World, grant

pytestmark = pytest.mark.integration


def give(session: Session, user: User, items: dict[str, str]) -> None:
    """A permission set made by hand, given to `user`."""
    custom = PermissionSet(name=f"Riêng {user.full_name}", builtin=False)
    session.add(custom)
    session.flush()
    session.add_all(
        [PermissionSetItem(set_id=custom.id, permission=k, scope=v) for k, v in items.items()]
        + [UserPermissionSet(user_id=user.id, set_id=custom.id)]
    )
    session.flush()


def take(session: Session, role: str, key: str) -> None:
    """Take `key` out of the built-in set of `role`."""
    builtin = session.scalars(select(PermissionSet).where(PermissionSet.auto_role == role)).one()
    session.execute(
        delete(PermissionSetItem).where(
            PermissionSetItem.set_id == builtin.id, PermissionSetItem.permission == key
        )
    )
    session.flush()


def raise_dispute(client: TestClient, finding_id: int, headers: dict[str, str]) -> int:
    res = client.post(
        f"/api/v1/findings/{finding_id}/dispute",
        headers=headers,
        json={"reason": "The input is validated by the caller."},
    )
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


def decide(client: TestClient, dispute_id: int, headers: dict[str, str]) -> Any:
    return client.post(
        f"/api/v1/disputes/{dispute_id}/decide", headers=headers, json={"decision": "rejected"}
    )


def own_finding(session: Session, world: World, user: User) -> ReviewFinding:
    """A critical finding in a review of `user`'s own commit."""
    sha = "c" * 40
    session.add(Commit(sha=sha, project_id=world.project.id))
    session.flush()
    review = Review(
        target_type="commit",
        commit_sha=sha,
        reviewed_sha=sha,
        status="done",
        author_user_id=user.id,
        score=Decimal(50),
        rubric_version=RUBRIC_VERSION,
    )
    session.add(review)
    session.flush()
    finding = ReviewFinding(
        review_id=review.id,
        severity="critical",
        category="security",
        file_path="app/b.py",
        line_start=1,
        title="Hard-coded secret",
        confidence=Decimal("0.9"),
    )
    session.add(finding)
    session.flush()
    return finding


def test_a_set_given_by_hand_lets_a_manager_decide_disputes_of_their_team(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    an_dispute = raise_dispute(client, world.an_finding.id, as_user(world.an))
    manager = as_user(world.manager)
    # The Manager set holds neither dispute.decide nor review.view over the team: refused.
    assert decide(client, an_dispute, manager).status_code == 403

    # "team" on disputes reaches the people one leads, never a managed team (Phase 0, spec 21
    # catalog managed_team): a manager's team is given "all" (within dự án they see).
    give(db_session, world.manager, {"review.view": "all", "dispute.decide": "all"})
    mine = raise_dispute(client, own_finding(db_session, world, world.manager).id, manager)
    # Never their own dispute (docs/specs/21 §3), whatever the sets say.
    refused = decide(client, mine, manager)
    assert refused.status_code == 403 and "own" in refused.json()["detail"]
    # Their team (Engineering and its sub-team Backend, where An is): now theirs to decide.
    assert decide(client, an_dispute, manager).status_code == 200


def test_removing_dispute_decide_from_the_lead_set_takes_it_from_every_lead(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    dispute_id = raise_dispute(client, world.an_finding.id, as_user(world.an))
    take(db_session, "lead", "dispute.decide")
    assert decide(client, dispute_id, as_user(world.lead)).status_code == 403
    # The lead still sees the dispute: review.view is untouched.
    listed = client.get("/api/v1/disputes", headers=as_user(world.lead)).json()
    assert [d["id"] for d in listed] == [dispute_id]
    assert decide(client, dispute_id, as_user(world.admin)).status_code == 200  # admins: full


def test_review_view_widened_by_hand_and_taken_out_of_the_member_set(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    binh_review = f"/api/v1/reviews/{world.binh_review.id}"
    an = as_user(world.an)
    assert client.get(binh_review, headers=an).status_code == 403
    give(db_session, world.an, {"review.view": "all"})
    assert client.get(binh_review, headers=an).status_code == 200
    listed = client.get("/api/v1/reviews", headers=an).json()["items"]
    assert {r["id"] for r in listed} == {world.an_review.id, world.binh_review.id}
    db_session.execute(delete(UserPermissionSet).where(UserPermissionSet.user_id == world.an.id))
    db_session.flush()

    take(db_session, "member", "review.view")
    binh = as_user(world.binh)
    assert client.get(f"/api/v1/reviews/{world.binh_review.id}", headers=binh).status_code == 403
    assert client.get("/api/v1/reviews", headers=binh).status_code == 403
    # The lead's own set still covers their team.
    assert client.get(binh_review, headers=as_user(world.lead)).status_code == 200


def test_close_requests_follow_close_request_and_close_decide(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    path = f"/api/v1/findings/{world.an_finding.id}/close-requests"
    body = {"commits": ["d" * 40]}
    take(db_session, "member", "finding.close_request")
    assert client.post(path, json=body, headers=as_user(world.an)).status_code == 403
    give(db_session, world.an, {"finding.close_request": "own"})
    made = client.post(path, json=body, headers=as_user(world.an))
    assert made.status_code == 201, made.text
    reject = f"/api/v1/close-requests/{made.json()['id']}/reject"
    note = {"note": "Chưa sửa hết chỗ này."}
    take(db_session, "lead", "finding.close_decide")
    assert client.post(reject, json=note, headers=as_user(world.lead)).status_code == 403
    # The manager of Engineering decides as before (their own set).
    assert client.post(reject, json=note, headers=as_user(world.manager)).status_code == 200


def test_closing_by_hand_can_be_given_and_a_project_manager_still_decides_nothing(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    close = f"/api/v1/findings/{world.an_finding.id}/close"
    body = {"commits": [], "outcome": "medium", "note": "Chấp nhận rủi ro."}
    assert client.post(close, json=body, headers=as_user(world.lead)).status_code == 403
    give(db_session, world.lead, {"finding.close_manual": "all"})
    assert client.post(close, json=body, headers=as_user(world.lead)).status_code == 200

    # Managing a dự án gives "Quản lý dự án", whose dispute.decide at "project" reaches no
    # further than "team" for now (app/api/access.TEAM_AT_MOST): as before spec 21.
    grant(db_session, world.outsider, world.project.business_project_id, "manage")
    dispute_id = raise_dispute(
        client, own_finding(db_session, world, world.binh).id, as_user(world.binh)
    )
    assert decide(client, dispute_id, as_user(world.outsider)).status_code == 403
