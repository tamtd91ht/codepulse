"""Per-dự-án grants (docs/specs/09 "Phân quyền theo dự án"): who sees and who manages a dự án."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditLog, BusinessProject, Commit, Project, ProjectGrant, Review, TelegramBot
from app.review.rubric import RUBRIC_VERSION
from app.telegram.service import NOT_GRANTED
from tests.api.conftest import WEEK, FakeQueue, World, grant
from tests.api.test_telegram import link, message, service

pytestmark = pytest.mark.integration

OTHER_SHA = "9" * 40


def demo_id(world: World) -> int:
    bp_id = world.project.business_project_id
    assert bp_id is not None
    return bp_id


def other_dự_án(session: Session, world: World) -> tuple[BusinessProject, Project, Review]:
    """A second dự án nobody in the world is granted, with a repo and a review of An's there."""
    bp = BusinessProject(name="Other")
    session.add(bp)
    session.flush()
    repo = Project(
        connection_id=world.project.connection_id,
        external_id=2002,
        path_with_namespace="other/r",
        business_project_id=bp.id,
    )
    session.add(repo)
    session.flush()
    session.add(Commit(sha=OTHER_SHA, project_id=repo.id))
    session.flush()
    review = Review(
        target_type="commit",
        commit_sha=OTHER_SHA,
        reviewed_sha=OTHER_SHA,
        status="done",
        author_user_id=world.an.id,
        score=Decimal(80),
        finished_at=datetime(2026, 10, 6, 10, tzinfo=UTC),
        rubric_version=RUBRIC_VERSION,
    )
    session.add(review)
    session.flush()
    return bp, repo, review


def test_a_dự_án_without_a_grant_does_not_exist_for_a_member(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp, _, _ = other_dự_án(db_session, world)
    an, admin = as_user(world.an), as_user(world.admin)
    overview = f"/api/v1/metrics/projects?period=week&start={WEEK}"
    detail = f"/api/v1/metrics/projects/{bp.id}?period=week&start={WEEK}"
    summary = f"/api/v1/knowledge/summary?business_project_id={bp.id}"

    assert [p["name"] for p in client.get(overview, headers=an).json()["projects"]] == ["Demo"]
    assert client.get(detail, headers=an).status_code == 404
    assert client.get(summary, headers=an).status_code == 404
    # Admins need no grant.
    assert {p["name"] for p in client.get(overview, headers=admin).json()["projects"]} == {
        "Demo",
        "Other",
    }
    assert client.get(detail, headers=admin).status_code == 200

    # A "view" grant shows it.
    grant(db_session, world.an, bp.id)
    assert {p["name"] for p in client.get(overview, headers=an).json()["projects"]} == {
        "Demo",
        "Other",
    }
    assert client.get(detail, headers=an).status_code == 200
    assert client.get(summary, headers=an).status_code == 200


def test_repos_are_filtered_by_grant(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _, other, _ = other_dự_án(db_session, world)
    unassigned = Project(
        connection_id=world.project.connection_id, external_id=3003, path_with_namespace="z/none"
    )
    db_session.add(unassigned)
    db_session.flush()

    def paths(user: Any, url: str = "/api/v1/repos") -> list[str]:
        res = client.get(url, headers=as_user(user))
        assert res.status_code == 200, res.text
        return [r["path_with_namespace"] for r in res.json()]

    assert paths(world.an) == ["demo/p"]
    assert paths(world.an, "/api/v1/repos/profiles") == ["demo/p"]
    assert paths(world.admin) == ["demo/p", "other/r", "z/none"]  # repos outside a dự án too
    # A hidden repo is "not found", before any GitLab call.
    for suffix in ("branches", "commits?ref=main"):
        res = client.get(f"/api/v1/repos/{other.id}/{suffix}", headers=as_user(world.an))
        assert res.status_code == 404, suffix
    res = client.get(f"/api/v1/projects/{other.id}/knowledge/stats", headers=as_user(world.an))
    assert res.status_code == 404
    queued = client.post(
        f"/api/v1/repos/{other.id}/review-commits",
        json={"ref": "main", "shas": [OTHER_SHA]},
        headers=as_user(world.an),
    )
    assert queued.status_code == 404


def test_rerun_needs_the_manage_grant(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    lead = as_user(world.lead)
    url = f"/api/v1/reviews/{world.binh_review.id}"
    # Leading Binh's team with a "view" grant: the review is readable, not re-runnable.
    assert client.get(url, headers=lead).json()["can_rerun"] is False
    assert client.post(f"{url}/rerun", headers=lead).status_code == 403
    assert queue.jobs == []

    grant(db_session, world.lead, demo_id(world), "manage")
    assert client.get(url, headers=lead).json()["can_rerun"] is True
    assert client.post(f"{url}/rerun", headers=lead).status_code == 202
    assert len(queue.jobs) == 1


def test_manage_shows_everyones_work_in_that_dự_án_only(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    other, _, elsewhere = other_dự_án(db_session, world)
    outsider = as_user(world.outsider)  # leads nobody
    an_url = f"/api/v1/reviews/{world.an_review.id}"
    assert client.get(an_url, headers=outsider).status_code == 403
    assert client.get(f"/api/v1/reviews?user_id={world.an.id}", headers=outsider).status_code == 403

    grant(db_session, world.outsider, demo_id(world), "manage")
    assert client.get(an_url, headers=outsider).status_code == 200
    listed = client.get("/api/v1/reviews", headers=outsider).json()["items"]
    assert {r["id"] for r in listed} == {world.an_review.id, world.binh_review.id}
    by_an = client.get(f"/api/v1/reviews?user_id={world.an.id}", headers=outsider)
    assert by_an.status_code == 200
    assert [r["id"] for r in by_an.json()["items"]] == [world.an_review.id]
    # Not in a dự án they do not manage, even with a "view" grant there.
    assert client.get(f"/api/v1/reviews/{elsewhere.id}", headers=outsider).status_code == 403
    grant(db_session, world.outsider, other.id, "view")
    assert client.get(f"/api/v1/reviews/{elsewhere.id}", headers=outsider).status_code == 403
    assert elsewhere.id not in {
        r["id"] for r in client.get("/api/v1/reviews", headers=outsider).json()["items"]
    }
    # The author still sees their own review anywhere.
    assert (
        client.get(f"/api/v1/reviews/{elsewhere.id}", headers=as_user(world.an)).status_code == 200
    )


def test_admin_replaces_the_grants_of_a_dự_án(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    path = f"/api/v1/business-projects/{demo_id(world)}/grants"
    before = client.get(path, headers=admin).json()
    assert [(g["full_name"], g["role"]) for g in before] == [
        ("An", "view"),
        ("Binh", "view"),
        ("Lead", "view"),
        ("Manager", "view"),
        ("Outsider", "view"),
    ]

    body = {
        "grants": [
            {"user_id": world.binh.id, "role": "view"},
            {"user_id": world.an.id, "role": "manage"},
        ]
    }
    res = client.put(path, json=body, headers=admin)
    assert res.status_code == 200, res.text
    after = [(g["user_id"], g["role"]) for g in res.json()]
    assert after == [(world.an.id, "manage"), (world.binh.id, "view")]
    assert [(g["user_id"], g["role"]) for g in client.get(path, headers=admin).json()] == after
    (listed,) = [
        bp
        for bp in client.get("/api/v1/business-projects", headers=admin).json()
        if bp["id"] == demo_id(world)
    ]
    assert listed["grants"] == 2
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "business_project.grants")
    ).one()
    assert entry.actor_user_id == world.admin.id
    assert entry.details == {
        "added": 0,
        "changed": {str(world.an.id): ["view", "manage"]},
        "removed": sorted([world.lead.id, world.manager.id, world.outsider.id]),
        "manage": [world.an.id],
        "view": [world.binh.id],
    }
    # Whoever was left out lost access at once.
    detail = f"/api/v1/metrics/projects/{demo_id(world)}?period=week&start={WEEK}"
    assert client.get(detail, headers=as_user(world.lead)).status_code == 404
    assert client.get(detail, headers=as_user(world.an)).status_code == 200

    twice = {
        "grants": [
            {"user_id": world.an.id, "role": "view"},
            {"user_id": world.an.id, "role": "manage"},
        ]
    }
    assert client.put(path, json=twice, headers=admin).status_code == 422
    unknown = {"grants": [{"user_id": 999999, "role": "view"}]}
    assert client.put(path, json=unknown, headers=admin).status_code == 422
    bad_role = {"grants": [{"user_id": world.an.id, "role": "owner"}]}
    assert client.put(path, json=bad_role, headers=admin).status_code == 422
    # Refused requests changed nothing.
    assert [(g["user_id"], g["role"]) for g in client.get(path, headers=admin).json()] == after
    assert client.get("/api/v1/business-projects/999999/grants", headers=admin).status_code == 404

    # An empty list takes everyone out.
    assert client.put(path, json={"grants": []}, headers=admin).json() == []
    assert db_session.scalars(select(ProjectGrant)).first() is None


def test_grants_are_admin_only(client: TestClient, world: World, as_user: Any) -> None:
    path = f"/api/v1/business-projects/{demo_id(world)}/grants"
    body = {"grants": [{"user_id": world.lead.id, "role": "manage"}]}
    for user in (world.lead, world.manager, world.an):
        assert client.get(path, headers=as_user(user)).status_code == 403
        assert client.put(path, json=body, headers=as_user(user)).status_code == 403


def test_me_lists_the_grants(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    grant(db_session, world.an, demo_id(world), "manage")
    me = client.get("/api/v1/auth/me", headers=as_user(world.an)).json()
    assert me["projects"] == {str(demo_id(world)): "manage"}
    admin = client.get("/api/v1/auth/me", headers=as_user(world.admin)).json()
    assert admin["projects"] == {}  # admins see every dự án without a grant


def test_telegram_refuses_a_linked_person_without_a_grant_on_the_bots_dự_án(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link(db_session, world.an)
    svc = service(db_session, bot, settings, queue)
    assert svc.handle(message("/status")) != NOT_GRANTED
    row = db_session.get(ProjectGrant, (bot.business_project_id, world.an.id))
    assert row is not None
    db_session.delete(row)
    db_session.flush()
    assert svc.handle(message("/status")) == NOT_GRANTED
    assert svc.handle(message("/review mr 7")) == NOT_GRANTED
    assert queue.jobs == []
