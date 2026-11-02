"""A manager of a dự án (project_grants "manage") sees everyone in it, not only their team
(user request 2026-11-02): people, evaluations, review results, disputes. Stored weekly scores
mix every dự án of a person and stay with the person, their lead and admins."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import (
    BusinessProject,
    Commit,
    FindingDispute,
    Project,
    ProjectGrant,
    Review,
    ReviewFinding,
    User,
)
from app.review.rubric import RUBRIC_VERSION
from tests.api.conftest import World

pytestmark = pytest.mark.integration


@pytest.fixture
def pm(db_session: Session, world: World) -> World:
    """`outsider` (no team role) manages the dự án of `world.project`."""
    db_session.execute(
        update(ProjectGrant)
        .where(
            ProjectGrant.user_id == world.outsider.id,
            ProjectGrant.business_project_id == world.project.business_project_id,
        )
        .values(role="manage")
    )
    db_session.flush()
    return world


def test_a_project_manager_opens_everyone_in_the_project(
    client: TestClient, pm: World, as_user: Any
) -> None:
    world, me = pm, as_user(pm.outsider)
    assert client.get(f"/api/v1/people/{world.an.id}", headers=me).status_code == 200
    detail = client.get(f"/api/v1/members/{world.an.id}/evaluation", headers=me)
    assert detail.status_code == 200, detail.text
    heat = client.get(f"/api/v1/metrics/users/{world.an.id}/categories", headers=me)
    assert heat.status_code == 200
    listed = client.get("/api/v1/members/evaluation", headers=me).json()
    ids = {m["user_id"] for m in listed["members"]}
    assert {world.an.id, world.binh.id, world.outsider.id} <= ids
    people = {p["id"] for p in client.get("/api/v1/people", headers=me).json()}
    assert {world.an.id, world.binh.id} <= people
    # Stored weekly scores mix every dự án: not the manager's.
    assert client.get(f"/api/v1/metrics/users/{world.an.id}", headers=me).status_code == 403


def test_a_viewer_of_the_project_still_sees_only_themselves(
    client: TestClient, world: World, as_user: Any
) -> None:
    me = as_user(world.outsider)  # "view" grant only
    assert client.get(f"/api/v1/people/{world.an.id}", headers=me).status_code == 403
    assert client.get(f"/api/v1/members/{world.an.id}/evaluation", headers=me).status_code == 403
    listed = client.get("/api/v1/members/evaluation", headers=me).json()
    assert {m["user_id"] for m in listed["members"]} == {world.outsider.id}


def test_a_project_manager_sees_the_disputes_in_the_project(
    client: TestClient, pm: World, as_user: Any, db_session: Session
) -> None:
    world = pm
    db_session.add(
        FindingDispute(finding_id=world.an_finding.id, raised_by=world.an.id, reason="not a bug")
    )
    db_session.flush()
    mine = client.get("/api/v1/disputes", headers=as_user(world.outsider)).json()
    assert [d["raised_by"] for d in mine] == [world.an.id]
    # Seeing is not deciding: that stays with the lead and admins.
    dispute_id = mine[0]["id"]
    decided = client.post(
        f"/api/v1/disputes/{dispute_id}/decide",
        json={"decision": "accepted"},
        headers=as_user(world.outsider),
    )
    assert decided.status_code == 403


def test_a_project_manager_sees_only_their_project_of_someone_working_in_two(
    client: TestClient, pm: World, as_user: Any, db_session: Session
) -> None:
    """an works in A (managed) and B (not): categories and disputes of B stay hidden."""
    world = pm
    now = datetime.now(UTC)
    other = BusinessProject(name="B")
    db_session.add(other)
    db_session.flush()
    repo_b = Project(
        connection_id=world.project.connection_id,
        external_id=2002,
        path_with_namespace="b/repo",
        business_project_id=other.id,
    )
    db_session.add(repo_b)
    db_session.flush()
    db_session.add(Commit(sha="c" * 40, project_id=repo_b.id))
    db_session.flush()
    review_b = Review(
        target_type="commit",
        commit_sha="c" * 40,
        reviewed_sha="c" * 40,
        status="done",
        author_user_id=world.an.id,
        score=Decimal(40),
        finished_at=now,  # counted then: in the last weeks
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(review_b)
    db_session.flush()
    finding_b = ReviewFinding(review_id=review_b.id, severity="high", category="performance")
    db_session.add(finding_b)
    world.an_review.finished_at = now
    db_session.flush()
    db_session.add(FindingDispute(finding_id=finding_b.id, raised_by=world.an.id, reason="b"))
    # Someone only in B: not the A manager's to open.
    stranger = User(full_name="Only B")
    db_session.add(stranger)
    db_session.flush()
    db_session.add(ProjectGrant(business_project_id=other.id, user_id=stranger.id, role="view"))
    db_session.flush()

    me = as_user(world.outsider)
    heat = client.get(f"/api/v1/metrics/users/{world.an.id}/categories", headers=me).json()
    assert {c["category"] for c in heat} == {"security"}
    # Not in the admin's view? It is: the hiding is the manager's scope.
    admin_heat = client.get(
        f"/api/v1/metrics/users/{world.an.id}/categories", headers=as_user(world.admin)
    ).json()
    assert {c["category"] for c in admin_heat} == {"security", "performance"}
    assert client.get("/api/v1/disputes", headers=me).json() == []
    assert client.get(f"/api/v1/people/{stranger.id}", headers=me).status_code == 403
    assert client.get(f"/api/v1/members/{stranger.id}/evaluation", headers=me).status_code == 403
    people = {p["id"] for p in client.get("/api/v1/people", headers=me).json()}
    assert world.an.id in people and stranger.id not in people
