"""Admin > DPI > Jira API (docs/specs/27 §13)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.main import app
from app.api.routers.jira import get_jira_factory
from app.collector.sync import SYNC_JOB_TIMEOUT
from app.config import Settings
from app.jira.client import JiraError
from app.jira.settings import mapping, task_source
from app.models import Account, AuditLog, BusinessProject, JiraProject
from tests.api.conftest import FakeQueue, World

pytestmark = pytest.mark.integration
API = "/api/v1/dpi/jira"
TOKEN = "pat-" + "fake-" + "secret"


class FakeApi:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.closed = False

    def server_info(self) -> dict[str, Any]:
        if self.fail:
            raise JiraError(401, "GET /serverInfo -> HTTP 401")
        return {"version": "9.12.1", "serverTitle": "Jira Công ty"}

    def fields(self) -> list[dict[str, Any]]:
        return [
            {"id": "customfield_10002", "name": "Story Points", "custom": True},
            {"id": "customfield_10004", "name": "Sprint", "custom": True},
        ]

    def projects(self) -> list[dict[str, Any]]:
        if self.fail:
            raise JiraError(None, "GET /project: ConnectError")
        return [{"key": "XYZ", "name": "Xyz"}, {"key": "ABC", "name": "Alpha"}]

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def settings() -> Settings:
    return Settings(
        session_secret=SecretStr("s" * 48),
        admin_emails=["admin@x.vn"],
        gitlab_oauth_url="https://gitlab.example.com",
        gitlab_oauth_client_id="cid",
        gitlab_oauth_client_secret=SecretStr("csecret"),
        app_base_url="https://codepulse.example.com",
        dashboard_url="https://dash.example.com",
        jira_base_url="https://jira.example.vn/",
        jira_token=SecretStr(TOKEN),
    )


@pytest.fixture
def jira(client: TestClient) -> Iterator[dict[str, FakeApi]]:
    made: dict[str, FakeApi] = {"api": FakeApi()}
    app.dependency_overrides[get_jira_factory] = lambda: lambda s: made["api"]
    yield made
    app.dependency_overrides.pop(get_jira_factory, None)


def test_needs_dpi_admin(client: TestClient, world: World, as_user: Any) -> None:
    for method, path in [
        ("get", ""),
        ("post", "/test"),
        ("get", "/remote-projects"),
        ("put", "/source"),
        ("post", "/sync"),
    ]:
        response = client.request(method, API + path, headers=as_user(world.lead), json={})
        assert response.status_code in (403, 422), path
        if response.status_code == 422:  # body checked first: still no way in with a body
            ok = client.request(
                method, API + path, headers=as_user(world.lead), json={"source": "jira"}
            )
            assert ok.status_code == 403


def test_overview(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    bp = db_session.get(BusinessProject, world.project.business_project_id)
    assert bp is not None
    db_session.add_all(
        [
            JiraProject(key="ZED", name="Zed", enabled=False),
            JiraProject(key="ABC", name="Alpha", business_project_id=bp.id, issues_count=4),
            Account(kind="jira_user", external_key="JIRAUSER1", user_id=world.an.id),
            Account(kind="jira_user", external_key="JIRAUSER2"),
        ]
    )
    db_session.flush()
    body = client.get(API, headers=as_user(world.admin)).json()
    assert body["configured"] is True
    assert body["base_url"] == "https://jira.example.vn"
    assert body["task_source"] == "jira"  # the default once Jira is configured
    assert body["mapping"]["story_points_field"] == "Story Points"
    assert "feature" in body["dpi_types"]
    assert body["accounts"] == {"total": 2, "linked": 1}
    assert body["interval_min"] == 15
    assert [p["key"] for p in body["projects"]] == ["ABC", "ZED"]
    abc = body["projects"][0]
    assert set(abc) == {
        "id",
        "key",
        "name",
        "enabled",
        "business_project",
        "last_synced_at",
        "synced_until",
        "last_error",
        "issues_count",
    }
    assert abc["business_project"] == {"id": bp.id, "name": bp.name}
    assert abc["issues_count"] == 4 and abc["enabled"] is True
    assert body["projects"][1]["business_project"] is None
    assert TOKEN not in str(body)


def test_connection_test_and_remote_projects(
    client: TestClient, world: World, as_user: Any, jira: dict[str, FakeApi]
) -> None:
    admin = as_user(world.admin)
    body = client.post(f"{API}/test", headers=admin).json()
    assert body == {
        "ok": True,
        "version": "9.12.1",
        "server_title": "Jira Công ty",
        "fields": {
            "story_points": {"id": "customfield_10002", "name": "Story Points"},
            "sprint": {"id": "customfield_10004", "name": "Sprint"},
            "flagged": None,
        },
    }
    assert jira["api"].closed
    assert client.get(f"{API}/remote-projects", headers=admin).json() == {
        "items": [{"key": "ABC", "name": "Alpha"}, {"key": "XYZ", "name": "Xyz"}]
    }
    jira["api"] = FakeApi(fail=True)
    failed = client.post(f"{API}/test", headers=admin)
    assert failed.status_code == 502 and "Jira" in failed.json()["detail"]
    assert client.get(f"{API}/remote-projects", headers=admin).status_code == 502


def test_not_configured_is_409(
    client: TestClient,
    world: World,
    as_user: Any,
    settings: Settings,
    jira: dict[str, FakeApi],
    queue: FakeQueue,
) -> None:
    settings.jira_base_url = ""
    admin = as_user(world.admin)
    assert client.post(f"{API}/test", headers=admin).status_code == 409
    assert client.get(f"{API}/remote-projects", headers=admin).status_code == 409
    assert client.post(f"{API}/sync", headers=admin).status_code == 409
    assert queue.jobs == []
    assert client.get(API, headers=admin).json()["configured"] is False


def test_put_projects(client: TestClient, world: World, as_user: Any, db_session: Session) -> None:
    admin = as_user(world.admin)
    bp_id = world.project.business_project_id
    db_session.add(JiraProject(key="OLD", name="Old"))
    db_session.flush()
    items = [
        {"key": "ABC", "name": "Alpha", "enabled": True, "business_project_id": bp_id},
        {"key": "OLD", "enabled": False, "business_project_id": None},
    ]
    response = client.put(f"{API}/projects", headers=admin, json={"items": items})
    assert response.status_code == 200
    projects = response.json()["projects"]
    assert [(p["key"], p["name"], p["enabled"]) for p in projects] == [
        ("ABC", "Alpha", True),
        ("OLD", "Old", False),  # no name given: kept
    ]
    assert projects[0]["business_project"]["id"] == bp_id
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "dpi.jira.projects")).one()
    bad = [{"key": "abc", "enabled": True}]
    assert client.put(f"{API}/projects", headers=admin, json={"items": bad}).status_code == 422
    unknown = [{"key": "ABC", "enabled": True, "business_project_id": 999999}]
    assert client.put(f"{API}/projects", headers=admin, json={"items": unknown}).status_code == 422
    too_many = [{"key": f"K{n:03d}", "enabled": True} for n in range(201)]
    assert client.put(f"{API}/projects", headers=admin, json={"items": too_many}).status_code == 422


def test_put_mapping_and_source(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
    queue: FakeQueue,
) -> None:
    admin = as_user(world.admin)
    response = client.put(
        f"{API}/mapping",
        headers=admin,
        json={"story_points_field": "Story point estimate", "types": {"Epic Task": "feature"}},
    )
    assert response.status_code == 200
    assert response.json()["mapping"]["story_points_field"] == "Story point estimate"
    assert mapping(db_session).types == {"Epic Task": "feature"}
    bad = client.put(f"{API}/mapping", headers=admin, json={"types": {"Story": "nope"}})
    assert bad.status_code == 422 and "chỉ nhận" in bad.json()["detail"]
    assert task_source(db_session, settings) == "jira"  # the default once Jira is configured
    # Back to GitLab: its issues were not fetched meanwhile, every tracked repo syncs in full.
    assert client.put(f"{API}/source", headers=admin, json={"source": "gitlab"}).json() == {
        "task_source": "gitlab",
        "full_syncs_queued": 1,
    }
    assert task_source(db_session, settings) == "gitlab"
    assert queue.jobs == [
        (
            "app.collector.sync.sync_project_job",
            (world.project.id,),
            {
                "full": True,
                "job_id": f"sync-project-{world.project.id}-full",
                "job_timeout": SYNC_JOB_TIMEOUT,
            },
        )
    ]
    # GitLab to GitLab, or to Jira: nothing to fetch again.
    queue.jobs.clear()
    assert client.put(f"{API}/source", headers=admin, json={"source": "gitlab"}).json() == {
        "task_source": "gitlab",
        "full_syncs_queued": 0,
    }
    assert client.put(f"{API}/source", headers=admin, json={"source": "jira"}).json() == {
        "task_source": "jira",
        "full_syncs_queued": 0,
    }
    assert queue.jobs == []
    assert client.put(f"{API}/source", headers=admin, json={"source": "x"}).status_code == 422
    actions = set(db_session.scalars(select(AuditLog.action)))
    assert {"dpi.jira.mapping", "dpi.jira.source"} <= actions


def test_sync_now_queues_the_job(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue, db_session: Session
) -> None:
    response = client.post(f"{API}/sync", headers=as_user(world.admin))
    assert response.status_code == 202
    body = response.json()
    assert body["queued"] is True and body["job_id"].startswith("jira-sync")
    assert queue.jobs[0][0] == "app.jira.sync.sync_all_job"
    assert queue.jobs[0][2]["job_id"] == body["job_id"]
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "dpi.jira.sync")).one()
