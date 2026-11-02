"""Tying the author of a review with no person to someone, from the review list."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Commit, Review
from app.review.rubric import RUBRIC_VERSION
from tests.api.conftest import FakeQueue, World, grant
from tests.api.test_attribution import unmapped_work

pytestmark = pytest.mark.integration


def listed(client: TestClient, headers: dict[str, str]) -> dict[int, dict[str, Any]]:
    res = client.get(
        "/api/v1/reviews", params={"from": "2026-01-01", "to": "2026-12-31"}, headers=headers
    )
    assert res.status_code == 200, res.text
    return {r["id"]: r for r in res.json()["items"]}


def test_the_list_shows_the_author_behind_a_review_without_a_person(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _, _, commit_review, mr_review = unmapped_work(db_session, world)
    rows = listed(client, as_user(world.admin))
    commit_row, mr_row = rows[commit_review.id], rows[mr_review.id]
    assert commit_row["author"] is None
    assert commit_row["author_account"] == {
        "id": commit_row["author_account"]["id"],
        "kind": "git_email",
        "name": None,
        "email": "an.home@gmail.com",
        "user": None,
        "can_link": True,
    }
    assert (mr_row["author_account"]["kind"], mr_row["author_account"]["name"]) == (
        "gitlab_user",
        "an.home",
    )
    assert mr_row["author_account"]["email"] is None  # a GitLab id is not shown
    # A mapped review carries no account.
    assert rows[world.an_review.id]["author_account"] is None
    # A lead with view only does not see work nobody is mapped to, and may not link it.
    assert commit_review.id not in listed(client, as_user(world.lead))
    res = client.get(f"/api/v1/reviews/{commit_review.id}/author", headers=as_user(world.lead))
    assert res.status_code == 403


def test_a_manager_links_the_author_and_chooses_whether_earlier_reviews_follow(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    email, _, commit_review, mr_review = unmapped_work(db_session, world)
    grant(db_session, world.lead, world.project.business_project_id, "manage")
    manager = as_user(world.lead)
    path = f"/api/v1/reviews/{commit_review.id}/author"

    form = client.get(path, headers=manager).json()
    assert form["account"]["email"] == "an.home@gmail.com" and form["reviews"] == 1
    assert form["can_create"] is False  # creating a person is the admin's
    candidates = {u["id"] for u in form["candidates"]}
    assert {world.an.id, world.binh.id} <= candidates and world.admin.id not in candidates

    # Not someone outside the dự án, nor a new person.
    refused = client.post(path, json={"user_id": world.admin.id, "sync": True}, headers=manager)
    assert refused.status_code == 403
    assert (
        client.post(path, json={"create": True, "sync": True}, headers=manager).status_code == 403
    )

    # "Không, chỉ gắn": the account is An's, the old review keeps no person (yet).
    res = client.post(path, json={"user_id": world.an.id, "sync": False}, headers=manager)
    assert res.status_code == 200, res.text
    assert (res.json()["reviews_attributed"], res.json()["periods_queued"]) == (0, 0)
    db_session.refresh(commit_review)
    assert commit_review.author_user_id is None and queue.jobs == []
    row = listed(client, manager)[commit_review.id]
    assert row["author_account"]["user"]["id"] == world.an.id  # "Đã gắn An, chưa đồng bộ"

    # "Có, đồng bộ": every review of that email is An's and the periods are re-scored.
    res = client.post(path, json={"user_id": world.an.id, "sync": True}, headers=manager)
    assert res.status_code == 200, res.text
    assert (res.json()["reviews_attributed"], res.json()["periods_queued"]) == (1, 2)
    db_session.refresh(commit_review)
    db_session.refresh(mr_review)
    assert commit_review.author_user_id == world.an.id
    assert mr_review.author_user_id is None  # another account (the GitLab user): untouched
    assert queue.jobs[-1][0] == "app.scoring.aggregate.aggregate_periods_job"

    # Moving an author someone already has is the admin's call.
    moved = client.post(path, json={"user_id": world.binh.id, "sync": True}, headers=manager)
    assert moved.status_code == 409
    db_session.refresh(email)
    assert email.user_id == world.an.id


def test_an_admin_may_create_the_person_from_a_git_email_only(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _, gitlab, commit_review, mr_review = unmapped_work(db_session, world)
    admin = as_user(world.admin)
    mr_path = f"/api/v1/reviews/{mr_review.id}/author"
    assert client.get(mr_path, headers=admin).json()["can_create"] is False
    res = client.post(mr_path, json={"create": True, "sync": True}, headers=admin)
    assert res.status_code == 422
    res = client.post(
        f"/api/v1/reviews/{commit_review.id}/author",
        json={"create": True, "sync": True},
        headers=admin,
    )
    assert res.status_code == 200, res.text
    db_session.refresh(commit_review)
    assert commit_review.author_user_id is not None
    assert commit_review.author_user_id not in (world.an.id, world.binh.id)
    # Nothing chosen.
    res = client.post(mr_path, json={"sync": True}, headers=admin)
    assert res.status_code == 422
    assert gitlab.user_id is None


def test_a_managers_sync_takes_only_reviews_nobody_has(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """Admin unlinked the account without touching reviews: one is still Binh's. A manager's
    sync gives An only the review with no person; moving Binh's is the admin's call."""
    email, _, commit_review, _ = unmapped_work(db_session, world)
    other = Commit(sha="e" * 40, project_id=world.project.id, author_account_id=email.id)
    db_session.add(other)
    db_session.flush()
    binhs = Review(
        target_type="commit",
        commit_sha=other.sha,
        reviewed_sha=other.sha,
        status="done",
        author_user_id=world.binh.id,
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(binhs)
    db_session.flush()
    grant(db_session, world.lead, world.project.business_project_id, "manage")
    path = f"/api/v1/reviews/{commit_review.id}/author"
    res = client.post(
        path, json={"user_id": world.an.id, "sync": True}, headers=as_user(world.lead)
    )
    assert res.status_code == 200, res.text
    assert res.json()["reviews_attributed"] == 1
    db_session.refresh(commit_review)
    db_session.refresh(binhs)
    assert commit_review.author_user_id == world.an.id and binhs.author_user_id == world.binh.id
    # The admin's sync moves every review of the account.
    res = client.post(
        path, json={"user_id": world.an.id, "sync": True}, headers=as_user(world.admin)
    )
    assert res.json()["reviews_attributed"] == 1
    db_session.refresh(binhs)
    assert binhs.author_user_id == world.an.id
    # A person outside the dự án is not shown to a manager.
    email.user_id = world.admin.id
    db_session.flush()
    form = client.get(path, headers=as_user(world.lead)).json()
    assert form["account"]["user"] is None
