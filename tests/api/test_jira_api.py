"""Admin > DPI > Jira API (docs/specs/27 §13)."""

import logging
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
    StoredSecret,
)
from tests.api.conftest import BOX, FakeQueue, World

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
        "env": {"base_url": True, "token": True, "ca_bundle": False},
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
