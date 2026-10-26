from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AppSetting,
    AuditLog,
    BusinessProject,
    Commit,
    Connection,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    Team,
    User,
)
from tests.api.conftest import FakeQueue, World

PATH = "/api/v1/admin/purge"


def test_only_admins_see_and_use_it(client: TestClient, world: World, as_user: Any) -> None:
    lead = as_user(world.lead)
    assert client.get(PATH, headers=lead).status_code == 403
    body = {"scope": "reviews", "confirm": "xoa review"}
    assert client.post(PATH, headers=lead, json=body).status_code == 403
    info = client.get(PATH, headers=as_user(world.admin)).json()
    assert [s["scope"] for s in info["scopes"]] == ["reviews", "collected", "all", "people"]
    assert info["counts"]["reviews"] == 2 and info["counts"]["projects"] == 1


def test_reviews_scope_keeps_collected_data_and_reaggregates(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    world.mr.merged_at = datetime(2026, 10, 6, 9, tzinfo=UTC)
    db_session.flush()
    admin = as_user(world.admin)
    wrong = client.post(PATH, headers=admin, json={"scope": "reviews", "confirm": "yes"})
    assert wrong.status_code == 422 and "xoa review" in wrong.text

    done = client.post(PATH, headers=admin, json={"scope": "reviews", "confirm": " XOA REVIEW "})
    assert done.status_code == 200, done.text
    assert done.json()["rows"]["reviews"] == 2 and done.json()["recompute_queued"] is True
    db_session.expire_all()
    assert db_session.scalars(select(Review)).first() is None
    assert db_session.scalars(select(ReviewFinding)).first() is None
    assert db_session.get(MergeRequest, world.mr.id) is not None
    assert db_session.get(Commit, "b" * 40) is not None
    ((func, args, _),) = queue.jobs
    assert func.endswith("recompute_job") and args == ("2026-10-06", "2026-10-06")
    entry = db_session.scalars(select(AuditLog).where(AuditLog.action == "data.purge")).one()
    assert entry.details is not None and entry.details["rows"]["reviews"] == 2


def test_collected_scope_keeps_repos_and_resyncs_them(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    world.project.last_synced_at = datetime(2026, 10, 20, tzinfo=UTC)
    db_session.flush()
    body = {"scope": "collected", "confirm": "xoa du lieu"}
    done = client.post(PATH, headers=as_user(world.admin), json=body)
    assert done.status_code == 200, done.text
    db_session.expire_all()
    for model in (Review, MergeRequest, Commit):
        assert db_session.scalars(select(model)).first() is None
    project = db_session.get(Project, world.project.id)
    assert project is not None and project.last_synced_at is None


def test_all_scope_keeps_people_and_connections(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = BusinessProject(name="OmiCRM")
    db_session.add_all([bp, AppSetting(key="max_review_loc", value=900)])
    db_session.flush()
    world.project.business_project_id = bp.id
    db_session.flush()
    body = {"scope": "all", "confirm": "xoa toan bo"}
    done = client.post(PATH, headers=as_user(world.admin), json=body)
    assert done.status_code == 200, done.text
    db_session.expire_all()
    for model in (Project, BusinessProject, AppSetting, Review):
        assert db_session.scalars(select(model)).first() is None
    assert db_session.scalars(select(Connection)).first() is not None
    assert db_session.get(User, world.admin.id) is not None
    assert db_session.scalars(select(Team)).first() is not None


def test_a_lock_timeout_asks_to_try_again(
    client: TestClient, world: World, as_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(*_: Any) -> None:
        raise OperationalError("DELETE FROM reviews", {}, Exception("lock timeout"))

    monkeypatch.setattr("app.api.routers.purge.purge", locked)
    body = {"scope": "reviews", "confirm": "xoa review"}
    answer = client.post(PATH, headers=as_user(world.admin), json=body)
    assert answer.status_code == 409 and "try again" in answer.text


def test_people_scope_keeps_only_the_admins(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:

    db_session.add_all(
        [
            Account(kind="gitlab_user", external_key="1", user_id=world.admin.id),
            Account(kind="git_email", external_key="an@x.vn", user_id=world.an.id),
        ]
    )
    db_session.commit()
    body = {"scope": "people", "confirm": "xoa ca nguoi dung"}
    done = client.post(PATH, headers=as_user(world.admin), json=body)
    assert done.status_code == 200, done.text
    db_session.expire_all()
    assert [u.id for u in db_session.scalars(select(User))] == [world.admin.id]
    assert [a.external_key for a in db_session.scalars(select(Account))] == ["1"]
    assert db_session.scalars(select(Team)).first() is None
    assert db_session.scalars(select(Connection)).first() is not None
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "data.purge")).one()
