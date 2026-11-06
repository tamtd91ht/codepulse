"""Admin > DPI > Jira API (docs/specs/27 §13)."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
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
from app.jira.client import JiraAuthError, JiraError
from app.jira.settings import (
    CONNECTION_KEY,
    TOKEN_SECRET,
    JiraConnection,
    mapping,
    task_source,
)
from app.models import (
    Account,
    AppSetting,
    AuditLog,
    BusinessProject,
    JiraProject,
    JiraProjectScope,
    PermissionSet,
    PermissionSetItem,
    StoredSecret,
    UserPermissionSet,
)
from tests.api.conftest import BOX, FakeQueue, World

pytestmark = pytest.mark.integration
API = "/api/v1/dpi/jira"
TOKEN = "pat-" + "fake-" + "secret"


class FakeApi:
    def __init__(
        self, fail: bool = False, version: str = "9.12.1", refuse_login: bool = False
    ) -> None:
        self.fail = fail
        self.version = version
        self.refuse_login = refuse_login
        self.closed = False

    def server_info(self) -> dict[str, Any]:
        if self.fail:
            raise JiraError(401, "GET /serverInfo -> HTTP 401")
        numbers = [int(n) for n in self.version.split(".")]
        return {
            "version": self.version,
            "versionNumbers": numbers,
            "serverTitle": "Jira Công ty",
            "deploymentType": "Server",
        }

    def myself(self) -> dict[str, Any]:
        if self.refuse_login:
            raise JiraAuthError(401, "GET /myself -> HTTP 401: Jira từ chối token")
        return {"name": "codepulse-bot", "key": "JIRAUSER10000"}

    def fields(self) -> list[dict[str, Any]]:
        return [
            {"id": "customfield_10002", "name": "Story Points", "custom": True},
            {"id": "customfield_10004", "name": "Sprint", "custom": True},
        ]

    def projects(self) -> list[dict[str, Any]]:
        if self.fail:
            raise JiraError(None, "GET /project: ConnectError")
        return [{"key": "XYZ", "name": "Xyz"}, {"key": "ABC", "name": "Alpha"}]

    def components(self, key: str) -> list[dict[str, Any]]:
        if self.fail:
            raise JiraError(None, f"GET /project/{key}/components: ConnectError")
        return [{"name": "Frontend"}, {"name": "Backend"}, {"id": "x"}]

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


def test_needs_jira_admin(client: TestClient, world: World, as_user: Any) -> None:
    for method, path in [
        ("get", ""),
        ("post", "/test"),
        ("get", "/remote-projects"),
        ("put", "/source"),
        ("post", "/sync"),
        ("put", "/connection"),
        ("delete", "/connection"),
    ]:
        response = client.request(method, API + path, headers=as_user(world.lead), json={})
        assert response.status_code in (403, 422), path
        if response.status_code == 422:  # body checked first: still no way in with a body
            ok = client.request(
                method, API + path, headers=as_user(world.lead), json={"source": "jira"}
            )
            assert ok.status_code == 403


def _grant_flag(db_session: Session, user_id: int, permission: str) -> None:
    row = PermissionSet(name=f"only {permission}", builtin=False)
    db_session.add(row)
    db_session.flush()
    db_session.add_all(
        [
            PermissionSetItem(set_id=row.id, permission=permission, scope="flag"),
            UserPermissionSet(user_id=user_id, set_id=row.id),
        ]
    )
    db_session.flush()


def test_jira_admin_is_its_own_permission(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """docs/specs/27 §11: jira.admin opens the Jira screens; dpi.admin alone no longer does."""
    _grant_flag(db_session, world.binh.id, "dpi.admin")
    refused = client.get(API, headers=as_user(world.binh))
    assert refused.status_code == 403 and "jira.admin" in refused.json()["detail"]
    _grant_flag(db_session, world.binh.id, "jira.admin")
    assert client.get(API, headers=as_user(world.binh)).status_code == 200


def test_overview(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    bp = db_session.get(BusinessProject, world.project.business_project_id)
    assert bp is not None
    db_session.add_all(
        [
            JiraProject(key="ZED", name="Zed", enabled=False),
            JiraProject(key="ABC", name="Alpha", issues_count=4),
            Account(kind="jira_user", external_key="JIRAUSER1", user_id=world.an.id),
            Account(kind="jira_user", external_key="JIRAUSER2"),
        ]
    )
    db_session.flush()
    abc_row = db_session.scalars(select(JiraProject).where(JiraProject.key == "ABC")).one()
    db_session.add(JiraProjectScope(jira_project_id=abc_row.id, business_project_id=bp.id))
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
        "scopes",
        "unmatched",
        "last_synced_at",
        "synced_until",
        "last_error",
        "issues_count",
    }
    assert abc["scopes"] == [
        {
            "business_project": {"id": bp.id, "name": bp.name},
            "components": [],
            "labels": [],
            "issues": 0,
        }
    ]
    assert abc["issues_count"] == 4 and abc["enabled"] is True
    assert body["projects"][1]["scopes"] == []
    assert body["sync"] == {"last_at": None, "failed": 0, "enabled": 1, "never": 1}
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
        "deployment_type": "Server",
        "pat_supported": True,
        "auth_type": "pat",
        "user": "codepulse-bot",
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
    assert projects[0]["scopes"][0]["business_project"]["id"] == bp_id
    assert projects[1]["scopes"] == []
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
    assert queue.jobs[0][2]["manual"] is True  # runs even while a refused login pauses
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "dpi.jira.sync")).one()


# -- the connection set on the web (docs/specs/27 §2, §13) -------------------------------------

WEB_TOKEN = "pat-" + "web-" + "secret"


@pytest.fixture
def made(client: TestClient) -> Iterator[list[JiraConnection]]:
    """The connections the API built a Jira client for."""
    conns: list[JiraConnection] = []

    def factory(conn: JiraConnection) -> FakeApi:
        conns.append(conn)
        return FakeApi()

    app.dependency_overrides[get_jira_factory] = lambda: factory
    yield conns
    app.dependency_overrides.pop(get_jira_factory, None)


def test_overview_connection_from_env(client: TestClient, world: World, as_user: Any) -> None:
    body = client.get(API, headers=as_user(world.admin)).json()
    assert body["connection"] == {
        "base_url": "https://jira.example.vn",
        "base_url_source": "env",
        "auth_type": "pat",
        "auth_type_source": "env",
        "username": None,
        "username_source": None,
        "token_set": True,
        "token_source": "env",
        "token_updated_at": None,
        "token_unreadable": False,
        "token_other_server": False,
        "ca_set": False,
        "ca_source": None,
        "interval_min": 15,
        "backfill_days": 180,
        "timeout_s": 20.0,
        "enabled": True,
        "configured": True,
        "sources": {
            "interval_min": "env",
            "backfill_days": "env",
            "timeout_s": "env",
            "enabled": "env",
        },
        "env": {
            "base_url": True,
            "token": True,
            "ca_bundle": False,
            "auth_type": False,
            "username": False,
        },
    }


def test_put_connection_saves_and_never_returns_the_token(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    admin = as_user(world.admin)
    caplog.set_level(logging.DEBUG)
    body = {
        "base_url": "https://jira.congty.vn/jira/",
        "token": WEB_TOKEN,
        "interval_min": 5,
        "backfill_days": 30,
        "timeout_s": 10,
    }
    response = client.put(f"{API}/connection", headers=admin, json=body)
    assert response.status_code == 200, response.text
    conn = response.json()["connection"]
    assert conn["base_url"] == "https://jira.congty.vn/jira" and conn["base_url_source"] == "web"
    assert conn["token_set"] and conn["token_source"] == "web" and conn["token_updated_at"]
    assert (conn["interval_min"], conn["backfill_days"], conn["timeout_s"]) == (5, 30, 10.0)
    overview = client.get(API, headers=admin).json()
    assert overview["configured"] is True
    assert overview["base_url"] == "https://jira.congty.vn/jira"
    assert overview["interval_min"] == 5
    # Encrypted at rest with the app's key; never in a response, the audit or the logs.
    row = db_session.get(StoredSecret, TOKEN_SECRET)
    assert row is not None and WEB_TOKEN not in row.value_encrypted
    assert BOX.decrypt(row.value_encrypted) == WEB_TOKEN
    audits = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "dpi.jira.connection")
    ).all()
    assert len(audits) == 1 and audits[0].details is not None
    assert audits[0].details["changed"] == [
        "backfill_days",
        "base_url",
        "interval_min",
        "timeout_s",
        "token",
    ]
    for text in (response.text, str(overview), str(audits[0].details), caplog.text):
        assert WEB_TOKEN not in text
    # Left out: kept.
    kept = client.put(f"{API}/connection", headers=admin, json={"enabled": True}).json()
    assert kept["connection"]["token_source"] == "web"
    # The token "" while the URL is another server than JIRA_BASE_URL's: JIRA_TOKEN would not
    # apply there, so it is refused (nothing changes).
    refused = client.put(f"{API}/connection", headers=admin, json={"token": ""})
    assert refused.status_code == 422 and "máy chủ" in refused.json()["detail"]
    assert db_session.get(StoredSecret, TOKEN_SECRET) is not None
    # Back on JIRA_BASE_URL's server with its own token, then the web token cleared: JIRA_TOKEN.
    client.put(
        f"{API}/connection",
        headers=admin,
        json={"base_url": "https://jira.example.vn", "token": WEB_TOKEN},
    )
    cleared = client.put(f"{API}/connection", headers=admin, json={"token": ""}).json()
    assert cleared["connection"]["token_source"] == "env"
    assert db_session.get(StoredSecret, TOKEN_SECRET) is None
    # "Dùng lại biến môi trường".
    reset = client.delete(f"{API}/connection", headers=admin)
    assert reset.status_code == 200
    assert reset.json()["connection"]["base_url"] == "https://jira.example.vn"
    assert reset.json()["connection"]["base_url_source"] == "env"
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "dpi.jira.connection.reset")
    ).one()


def test_put_connection_ca_and_switch_off(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue
) -> None:
    from tests.jira.test_connection import ca_pem

    admin = as_user(world.admin)
    response = client.put(
        f"{API}/connection", headers=admin, json={"ca_pem": ca_pem(), "enabled": False}
    )
    assert response.status_code == 200
    conn = response.json()["connection"]
    assert conn["ca_set"] and conn["ca_source"] == "web" and conn["enabled"] is False
    assert "BEGIN CERTIFICATE" not in response.text
    assert client.get(API, headers=admin).json()["configured"] is False
    # Switched off: no sync, but the connection can still be tested.
    assert client.post(f"{API}/sync", headers=admin).status_code == 409
    assert queue.jobs == []
    cleared = client.put(f"{API}/connection", headers=admin, json={"ca_pem": ""}).json()
    assert cleared["connection"]["ca_set"] is False


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"base_url": "ftp://jira.vn"}, "http(s)"),
        ({"base_url": "https://u:p@jira.example.vn"}, "tài khoản"),
        ({"base_url": "https://jira.example.vn/?x=1"}, "?"),
        ({"ca_pem": "abc"}, "PEM"),
        ({"interval_min": 0}, "interval_min"),
        ({"backfill_days": 4000}, "backfill_days"),
        ({"timeout_s": 0}, "timeout_s"),
        ({"token": "two words"}, "token"),
        # Another server without the token typed again: the token in effect is not sent there.
        ({"base_url": "https://evil.example.com"}, "nhập token cho máy chủ này"),
    ],
)
def test_put_connection_checks(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    body: dict[str, Any],
    message: str,
) -> None:
    response = client.put(f"{API}/connection", headers=as_user(world.admin), json=body)
    assert response.status_code == 422
    assert message in str(response.json()["detail"])
    assert db_session.get(AppSetting, CONNECTION_KEY) is None


def test_moving_server_with_a_token(
    client: TestClient, world: World, as_user: Any, settings: Settings
) -> None:
    admin = as_user(world.admin)
    same = client.put(
        f"{API}/connection", headers=admin, json={"base_url": "https://jira.example.vn/jira"}
    )
    assert same.status_code == 200  # same server: the token stays
    moved = client.put(
        f"{API}/connection",
        headers=admin,
        json={"base_url": "https://jira2.example.vn", "token": WEB_TOKEN},
    )
    assert moved.status_code == 200
    assert moved.json()["connection"]["token_source"] == "web"
    # Nothing in effect before: nothing to protect.
    settings.jira_base_url = ""
    settings.jira_token = SecretStr("")
    client.delete(f"{API}/connection", headers=admin)
    first = client.put(f"{API}/connection", headers=admin, json={"base_url": "jira.new.vn"})
    assert first.status_code == 200
    assert first.json()["connection"]["configured"] is False  # no token yet


def test_test_endpoint_with_unsaved_values(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
    made: list[JiraConnection],
) -> None:
    admin = as_user(world.admin)
    # No body: the connection in effect.
    assert client.post(f"{API}/test", headers=admin).json()["ok"] is True
    assert made[-1].base_url == "https://jira.example.vn" and made[-1].token == TOKEN
    # Values typed and not saved: tested as they are, nothing saved.
    response = client.post(
        f"{API}/test",
        headers=admin,
        json={"base_url": "jira.other.vn", "token": WEB_TOKEN},
    )
    assert response.status_code == 200 and response.json()["version"] == "9.12.1"
    assert made[-1].base_url == "https://jira.other.vn" and made[-1].token == WEB_TOKEN
    assert db_session.get(AppSetting, CONNECTION_KEY) is None
    assert db_session.get(StoredSecret, TOKEN_SECRET) is None
    assert WEB_TOKEN not in response.text
    # A token only: the URL in effect.
    client.post(f"{API}/test", headers=admin, json={"token": WEB_TOKEN})
    assert made[-1].base_url == "https://jira.example.vn" and made[-1].token == WEB_TOKEN
    # Another server without a token: refused, the token in effect is not sent there.
    moved = client.post(f"{API}/test", headers=admin, json={"base_url": "https://evil.vn"})
    assert moved.status_code == 422 and "nhập token cho máy chủ này" in moved.json()["detail"]
    bad = client.post(f"{API}/test", headers=admin, json={"ca_pem": "nope"})
    assert bad.status_code == 422
    # Nothing in effect: a test with typed values still works, without them 409.
    settings.jira_base_url = ""
    settings.jira_token = SecretStr("")
    assert client.post(f"{API}/test", headers=admin).status_code == 409
    ok = client.post(
        f"{API}/test", headers=admin, json={"base_url": "jira.new.vn", "token": WEB_TOKEN}
    )
    assert ok.status_code == 200
    # Switched off on the web: still testable.
    settings.jira_base_url = "https://jira.example.vn"
    settings.jira_token = SecretStr(TOKEN)
    client.put(f"{API}/connection", headers=admin, json={"enabled": False})
    assert client.post(f"{API}/test", headers=admin).status_code == 200
    assert client.get(f"{API}/remote-projects", headers=admin).status_code == 409


def test_saved_connection_is_used_by_the_calls(
    client: TestClient, world: World, as_user: Any, made: list[JiraConnection]
) -> None:
    admin = as_user(world.admin)
    client.put(
        f"{API}/connection",
        headers=admin,
        json={"base_url": "https://jira2.example.vn", "token": WEB_TOKEN, "timeout_s": 5},
    )
    assert client.get(f"{API}/remote-projects", headers=admin).status_code == 200
    assert made[-1].base_url == "https://jira2.example.vn"
    assert made[-1].token == WEB_TOKEN and made[-1].timeout_s == 5.0


def test_a_token_never_reaches_another_server(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    made: list[JiraConnection],
) -> None:
    """The review's two-step: a URL with a throwaway token, then the token cleared, must not
    send JIRA_TOKEN to that URL (the resolver binds every token to its own server)."""
    admin = as_user(world.admin)
    step1 = client.put(
        f"{API}/connection",
        headers=admin,
        json={"base_url": "https://evil.example.com", "token": "x-" + "throwaway"},
    )
    assert step1.status_code == 200
    step2 = client.put(f"{API}/connection", headers=admin, json={"token": ""})
    assert step2.status_code == 422
    # Even written straight into the store, the env token is not paired with that URL.
    from app.jira.settings import clear_token

    clear_token(db_session)
    db_session.flush()
    body = client.get(API, headers=admin).json()["connection"]
    assert body["base_url"] == "https://evil.example.com"
    assert body["token_set"] is False and body["token_other_server"] is True
    assert body["configured"] is False
    assert client.post(f"{API}/test", headers=admin).status_code == 409
    assert client.post(f"{API}/sync", headers=admin).status_code == 409
    assert all(c.token != TOKEN for c in made)
    # A test of another URL without a token: the env token is not sent there either.
    client.delete(f"{API}/connection", headers=admin)
    moved = client.post(f"{API}/test", headers=admin, json={"base_url": "https://evil.vn"})
    assert moved.status_code == 422
    assert all(c.token != TOKEN or c.base_url == "https://jira.example.vn" for c in made)


def test_loopback_and_cleartext(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    for url in ("http://localhost:8080", "http://127.0.0.1", "http://169.254.169.254"):
        response = client.put(
            f"{API}/connection", headers=admin, json={"base_url": url, "token": WEB_TOKEN}
        )
        assert response.status_code == 422, url
    plain = client.put(
        f"{API}/connection",
        headers=admin,
        json={"base_url": "http://jira.example.vn", "token": WEB_TOKEN},
    )
    assert plain.status_code == 200  # allowed; the screen warns the PAT travels in cleartext


# -- basic auth: Jira Server older than 8.14 has no PAT (docs/specs/27 §2, §13) -----------------

PASSWORD = "mật " + "khẩu-" + "fake"  # fake, built by concatenation


def test_basic_auth_put_get_and_audit(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    made: list[JiraConnection],
    caplog: pytest.LogCaptureFixture,
) -> None:
    admin = as_user(world.admin)
    caplog.set_level(logging.DEBUG)
    # Basic without a username: refused, nothing saved.
    refused = client.put(
        f"{API}/connection", headers=admin, json={"auth_type": "basic", "token": PASSWORD}
    )
    assert refused.status_code == 422 and "username" in refused.json()["detail"]
    assert db_session.get(AppSetting, CONNECTION_KEY) is None
    assert db_session.get(StoredSecret, TOKEN_SECRET) is None
    body = {
        "base_url": "https://manage.congty.vn",
        "auth_type": "basic",
        "username": "codepulse-bot",
        "token": PASSWORD,
    }
    response = client.put(f"{API}/connection", headers=admin, json=body)
    assert response.status_code == 200, response.text
    conn = response.json()["connection"]
    assert (conn["auth_type"], conn["auth_type_source"]) == ("basic", "web")
    assert (conn["username"], conn["username_source"]) == ("codepulse-bot", "web")
    assert conn["token_set"] and conn["token_source"] == "web" and conn["configured"]
    row = db_session.get(StoredSecret, TOKEN_SECRET)
    assert row is not None and BOX.decrypt(row.value_encrypted) == PASSWORD
    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "dpi.jira.connection")
    ).one()
    assert audit.details is not None
    assert audit.details["changed"] == ["auth_type", "base_url", "token", "username"]
    assert audit.details["username"] == "codepulse-bot"
    assert audit.details["auth_type"] == "basic"
    overview = client.get(API, headers=admin).json()
    assert overview["connection"]["auth_type"] == "basic" and overview["auth_failure"] is None
    for text in (response.text, str(overview), str(audit.details), caplog.text):
        assert PASSWORD not in text
    # The calls use it.
    assert client.get(f"{API}/remote-projects", headers=admin).status_code == 200
    assert made[-1].auth_type == "basic" and made[-1].username == "codepulse-bot"
    assert made[-1].token == PASSWORD
    # Another username on the same server: the password stays.
    same = client.put(f"{API}/connection", headers=admin, json={"username": "bot2"})
    assert same.status_code == 200 and same.json()["connection"]["token_source"] == "web"
    # Back to PAT on the same server: the saved password is not a PAT, so one must be typed.
    not_pat = client.put(f"{API}/connection", headers=admin, json={"auth_type": "pat"})
    assert not_pat.status_code == 422 and "PAT" in not_pat.json()["detail"]
    pat = client.put(
        f"{API}/connection", headers=admin, json={"auth_type": "pat", "token": WEB_TOKEN}
    )
    assert pat.status_code == 200 and pat.json()["connection"]["auth_type"] == "pat"
    # Another server with basic: the secret must be typed again.
    moved = client.put(
        f"{API}/connection",
        headers=admin,
        json={"base_url": "https://evil.example.com", "auth_type": "basic"},
    )
    assert moved.status_code == 422 and "máy chủ" in moved.json()["detail"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"auth_type": "oauth"}, "auth_type"),
        ({"username": "a:b"}, "username"),
        ({"auth_type": "basic", "username": "bot", "token": "a\nb"}, "mật khẩu"),
        ({"auth_type": "basic", "username": "bot", "token": "x" * 1001}, "mật khẩu"),
    ],
)
def test_basic_auth_checks(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    body: dict[str, Any],
    message: str,
) -> None:
    response = client.put(f"{API}/connection", headers=as_user(world.admin), json=body)
    assert response.status_code == 422
    assert message in str(response.json()["detail"])
    assert db_session.get(AppSetting, CONNECTION_KEY) is None


def test_test_endpoint_with_basic_auth_and_an_old_jira(
    client: TestClient, world: World, as_user: Any, made: list[JiraConnection]
) -> None:
    admin = as_user(world.admin)
    typed = {"auth_type": "basic", "username": "codepulse-bot", "token": PASSWORD}
    response = client.post(f"{API}/test", headers=admin, json=typed)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auth_type"] == "basic" and body["user"] == "codepulse-bot"
    assert made[-1].auth_type == "basic" and made[-1].token == PASSWORD
    assert PASSWORD not in response.text
    missing = client.post(f"{API}/test", headers=admin, json={"auth_type": "basic"})
    assert missing.status_code == 422 and "username" in missing.json()["detail"]


def test_old_jira_with_a_pat_says_to_use_basic_auth(
    client: TestClient, world: World, as_user: Any, jira: dict[str, FakeApi]
) -> None:
    admin = as_user(world.admin)
    jira["api"] = FakeApi(version="8.0.2")
    ok = client.post(f"{API}/test", headers=admin).json()
    assert ok["version"] == "8.0.2" and ok["pat_supported"] is False
    jira["api"] = FakeApi(version="8.0.2", refuse_login=True)
    failed = client.post(f"{API}/test", headers=admin)
    assert failed.status_code == 502
    assert "8.0.2" in failed.json()["detail"] and "Tài khoản + mật khẩu" in failed.json()["detail"]


def test_a_refused_login_shows_and_a_passing_test_clears_it(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
    jira: dict[str, FakeApi],
) -> None:
    from app.jira.settings import connection, record_auth_failure

    admin = as_user(world.admin)
    conn = connection(db_session, settings, BOX)
    record_auth_failure(db_session, "GET /field -> HTTP 401: CAPTCHA", conn, BOX)
    db_session.flush()
    shown = client.get(API, headers=admin).json()["auth_failure"]
    assert shown is not None and "CAPTCHA" in shown["error"] and shown["at"]
    # A test of other values typed does not resume the schedule, one of the saved ones does.
    assert client.post(f"{API}/test", headers=admin, json={"token": WEB_TOKEN}).status_code == 200
    assert client.get(API, headers=admin).json()["auth_failure"] is not None
    assert client.post(f"{API}/test", headers=admin, json={}).status_code == 200
    assert client.get(API, headers=admin).json()["auth_failure"] is None
    # A credentials change resumes it too; a change of the interval does not.
    record_auth_failure(db_session, "HTTP 401", conn, BOX)
    db_session.flush()
    client.put(f"{API}/connection", headers=admin, json={"interval_min": 30})
    assert client.get(API, headers=admin).json()["auth_failure"] is not None
    client.put(f"{API}/connection", headers=admin, json={"token": WEB_TOKEN})
    assert client.get(API, headers=admin).json()["auth_failure"] is None


def test_switching_off_is_allowed_whatever_the_login(
    client: TestClient, world: World, as_user: Any, settings: Settings
) -> None:
    """JIRA_AUTH_TYPE=basic without JIRA_USERNAME: a login change is refused, switching the
    integration off is not."""
    settings.jira_auth_type = "basic"
    admin = as_user(world.admin)
    off = client.put(f"{API}/connection", headers=admin, json={"enabled": False})
    assert off.status_code == 200 and off.json()["connection"]["enabled"] is False
    login = client.put(f"{API}/connection", headers=admin, json={"token": TOKEN})
    assert login.status_code == 422 and "username" in login.json()["detail"]


# -- several CodePulse dự án per Jira project (docs/specs/27 §15-16) -----------------------


def _shop(db_session: Session) -> tuple[JiraProject, int, int]:
    from app.jira.settings import JIRA_ID_OFFSET
    from app.models import Issue

    fe = BusinessProject(name="FE")
    be = BusinessProject(name="BE")
    shop = JiraProject(key="SHOP", name="Shop", synced_until=datetime(2026, 11, 1, tzinfo=UTC))
    db_session.add_all([fe, be, shop])
    db_session.flush()
    for n, comps, labels in [(1, ["Frontend"], []), (2, ["Backend"], ["api"]), (3, [], [])]:
        db_session.add(
            Issue(
                id=JIRA_ID_OFFSET + 9000 + n,
                source="jira",
                jira_project_id=shop.id,
                iid=n,
                key=f"SHOP-{n}",
                components=comps,
                labels=labels,
            )
        )
    db_session.flush()
    return shop, fe.id, be.id


def test_put_projects_with_scopes_routes_the_stored_issues(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    from app.models import Issue

    admin = as_user(world.admin)
    _project, fe, be = _shop(db_session)
    scopes = [
        {"business_project_id": fe, "components": ["frontend", " Frontend "]},
        {"business_project_id": be, "components": ["Backend"], "labels": ["api"]},
    ]
    item = {"key": "SHOP", "enabled": True, "scopes": scopes}
    response = client.put(f"{API}/projects", headers=admin, json={"items": [item]})
    assert response.status_code == 200, response.text
    body = response.json()
    row = next(p for p in body["projects"] if p["key"] == "SHOP")
    assert [
        (s["business_project"]["name"], s["components"], s["labels"], s["issues"])
        for s in row["scopes"]
    ] == [
        ("FE", ["frontend"], [], 2),  # SHOP-1, and SHOP-3 (no match: every dự án)
        ("BE", ["Backend"], ["api"], 2),
    ]
    assert row["unmatched"] == 1
    assert body["refetch"] == [] and queue.jobs == []  # components were stored already
    routed = dict(db_session.execute(select(Issue.key, Issue.business_project_ids)).all())
    assert routed == {"SHOP-1": [fe], "SHOP-2": [be], "SHOP-3": None}

    # Left out: the scopes are kept. The old one-dự án shape still works (one catch-all).
    keep = client.put(
        f"{API}/projects", headers=admin, json={"items": [{"key": "SHOP", "enabled": False}]}
    )
    assert len(next(p for p in keep.json()["projects"] if p["key"] == "SHOP")["scopes"]) == 2
    legacy = {"key": "SHOP", "enabled": True, "business_project_id": fe}
    again = client.put(f"{API}/projects", headers=admin, json={"items": [legacy]}).json()
    assert [
        s["components"] for s in next(p for p in again["projects"] if p["key"] == "SHOP")["scopes"]
    ] == [[]]
    db_session.expire_all()
    assert set(db_session.scalars(select(Issue.business_project_ids))) == {None}

    twice = {"key": "SHOP", "enabled": True, "scopes": [scopes[0], scopes[0]]}
    assert client.put(f"{API}/projects", headers=admin, json={"items": [twice]}).status_code == 422
    unknown = {"key": "SHOP", "enabled": True, "scopes": [{"business_project_id": 999999}]}
    assert (
        client.put(f"{API}/projects", headers=admin, json={"items": [unknown]}).status_code == 422
    )


def test_a_component_rule_on_issues_stored_without_components_refetches(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    from app.models import Issue

    shop, fe, _be = _shop(db_session)
    db_session.execute(
        Issue.__table__.update().where(Issue.key == "SHOP-3").values(components=None)
    )
    item = {
        "key": "SHOP",
        "enabled": True,
        "scopes": [{"business_project_id": fe, "components": ["Frontend"]}],
    }
    body = client.put(
        f"{API}/projects", headers=as_user(world.admin), json={"items": [item]}
    ).json()
    assert body["refetch"] == ["SHOP"] and body["refetch_queued"] is True
    db_session.refresh(shop)
    assert shop.synced_until is None  # the backfill window again, components included
    assert queue.jobs[-1][2]["keys"] == ["SHOP"]


def test_preview_and_options(
    client: TestClient, world: World, as_user: Any, db_session: Session, jira: dict[str, FakeApi]
) -> None:
    admin = as_user(world.admin)
    _project, fe, be = _shop(db_session)
    scopes = [
        {"business_project_id": fe, "components": ["Frontend"]},
        {"business_project_id": be},
    ]
    body = client.post(
        f"{API}/projects/SHOP/preview", headers=admin, json={"scopes": scopes}
    ).json()
    # SHOP-1 to FE; SHOP-2 and SHOP-3 match no rule: to the catch-all BE.
    assert body == {
        "total": 3,
        "per_business_project": {str(fe): 1, str(be): 2},
        "unmatched": 2,
        "components_missing": False,
    }
    assert (
        client.post(f"{API}/projects/NEW/preview", headers=admin, json={"scopes": []}).json()[
            "total"
        ]
        == 0
    )
    assert (
        client.post(f"{API}/projects/bad/preview", headers=admin, json={"scopes": []}).status_code
        == 422
    )

    options = client.get(f"{API}/projects/SHOP/options", headers=admin).json()
    assert options == {
        "components": ["Backend", "Frontend"],
        "components_source": "jira",
        "labels": ["api"],
    }
    jira["api"] = FakeApi(fail=True)  # Jira unreachable: the components seen on stored issues
    local = client.get(f"{API}/projects/SHOP/options", headers=admin).json()
    assert local["components_source"] == "local" and set(local["components"]) == {
        "Frontend",
        "Backend",
    }


def test_sync_one_project_or_from_the_start(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    admin = as_user(world.admin)
    shop, _fe, _be = _shop(db_session)
    body = client.post(f"{API}/sync", headers=admin, json={"keys": ["SHOP"], "full": True}).json()
    assert body["keys"] == ["SHOP"] and body["full"] is True
    assert queue.jobs[-1][2]["keys"] == ["SHOP"] and queue.jobs[-1][2]["manual"] is True
    db_session.refresh(shop)
    assert shop.synced_until is None
    shop.enabled = False
    db_session.flush()
    assert client.post(f"{API}/sync", headers=admin, json={"keys": ["SHOP"]}).status_code == 409


def test_deleting_a_dự_án_routes_its_jira_issues_again(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.models import Issue

    admin = as_user(world.admin)
    _project, fe, be = _shop(db_session)
    scopes = [
        {"business_project_id": fe, "components": ["Frontend"]},
        {"business_project_id": be},
    ]
    item = {"key": "SHOP", "enabled": True, "scopes": scopes}
    assert client.put(f"{API}/projects", headers=admin, json={"items": [item]}).status_code == 200
    assert client.delete(f"/api/v1/business-projects/{fe}", headers=admin).status_code == 204
    db_session.expire_all()
    # Only BE is left, without rules: every issue counts for it (NULL), none for the dead FE.
    assert set(db_session.scalars(select(Issue.business_project_ids))) == {None}
    row = next(p for p in client.get(API, headers=admin).json()["projects"] if p["key"] == "SHOP")
    assert [(s["business_project"]["id"], s["issues"]) for s in row["scopes"]] == [(be, 3)]


def test_sync_full_is_passed_to_the_job(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    _shop(db_session)
    client.post(f"{API}/sync", headers=as_user(world.admin), json={"keys": ["SHOP"], "full": True})
    assert queue.jobs[-1][2]["full"] is True
