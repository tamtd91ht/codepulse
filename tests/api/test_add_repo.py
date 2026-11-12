"""POST /projects: add a repo by GitLab link, path or ID, straight into a dự án."""

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, BusinessProject, Connection, Project
from tests.api.conftest import BOX, FakeQueue, World

pytestmark = pytest.mark.integration

API = "https://gitlab.example.com/api/v4"


def new_project(session: Session, name: str = "OmiCRM") -> BusinessProject:
    bp = BusinessProject(name=name)
    session.add(bp)
    session.flush()
    return bp


def test_a_link_adds_the_repo_into_the_du_an_and_queues_its_sync(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = new_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/vihat%2Fomi%2Fapi").respond(
            json={"id": 3003, "path_with_namespace": "vihat/omi/api", "default_branch": "main"}
        )
        res = client.post(
            "/api/v1/projects",
            headers=as_user(world.admin),
            json={
                "repo": "https://gitlab.example.com/vihat/omi/api/-/merge_requests/3",
                "business_project_id": bp.id,
            },
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["path_with_namespace"], body["business_project_id"], body["existed"]) == (
        "vihat/omi/api",
        bp.id,
        False,
    )
    assert [f for f, _, _ in queue.jobs] == ["app.collector.sync.sync_project_job"]
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "project.create")).one()
    assert audit.details == {"business_project_id": bp.id}


def test_an_id_still_works_and_an_existing_repo_is_moved_and_tracked_again(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    bp = new_project(db_session)
    world.project.tracked = False
    db_session.flush()
    info = {"id": 1001, "path_with_namespace": "demo/p", "default_branch": "main"}
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/1001").respond(json=info)
        res = client.post(
            "/api/v1/projects",
            headers=as_user(world.admin),
            json={"repo": "1001", "business_project_id": bp.id},
        )
    assert res.status_code == 200, res.text
    assert res.json()["existed"] is True and res.json()["tracked"] is True
    db_session.expire_all()
    project = db_session.get(Project, world.project.id)
    assert project is not None and project.business_project_id == bp.id
    assert len(queue.jobs) == 1
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "project.add_existing")
    ).one()


def test_bad_references_are_explained(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/no%2Fsuch").respond(404)
        missing = client.post("/api/v1/projects", headers=admin, json={"repo": "no/such"})
        foreign = client.post(
            "/api/v1/projects", headers=admin, json={"repo": "https://github.com/a/b"}
        )
        neither = client.post("/api/v1/projects", headers=admin, json={})
    assert missing.status_code == 404 and "token bot" in missing.json()["detail"]
    assert foreign.status_code == 422 and "github.com" in foreign.json()["detail"]
    assert neither.status_code == 422


def test_with_two_connections_an_id_needs_one_chosen(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    db_session.add(
        Connection(name="gl2", base_url="https://gl2.example.com", token_encrypted=BOX.encrypt("t"))
    )
    db_session.flush()
    res = client.post("/api/v1/projects", headers=as_user(world.admin), json={"repo": "77"})
    assert res.status_code == 422 and "chọn kết nối" in res.json()["detail"]


def test_a_delegated_projects_admin_adds_no_repo_into_their_own_du_an(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from tests.api.test_permissions_escalation import give

    give(db_session, world.binh, {"admin.projects": "flag"})
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/4004").respond(
            json={"id": 4004, "path_with_namespace": "x/new", "default_branch": "main"}
        )
        res = client.post(
            "/api/v1/projects",
            headers=as_user(world.binh),
            json={"repo": "4004", "business_project_id": world.project.business_project_id},
        )
    assert res.status_code == 403
    assert db_session.scalars(select(Project).where(Project.external_id == 4004)).first() is None


def test_an_existing_repo_renamed_on_gitlab_gets_its_new_path(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    info = {"id": 1001, "path_with_namespace": "demo/renamed", "default_branch": "develop"}
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/1001").respond(json=info)
        res = client.post("/api/v1/projects", headers=as_user(world.admin), json={"repo": "1001"})
        refused = client.post(
            "/api/v1/projects", headers=as_user(world.admin), json={"external_id": 1001}
        )
    assert res.status_code == 200 and res.json()["path_with_namespace"] == "demo/renamed"
    assert res.json()["default_branch"] == "develop"
    assert queue.jobs == []  # still tracked: nothing to catch up
    assert refused.status_code == 422  # an ID alone does not say which connection
