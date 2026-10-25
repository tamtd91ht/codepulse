import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.main import app
from app.api.routers.llm import (
    get_login_sessions,
    get_login_supported,
    get_setup_token_factory,
)
from app.config import Settings
from app.models import AuditLog, StoredSecret
from app.review.claude_login import LoginError, LoginSessions
from app.review.credentials import CLAUDE_TOKEN, stored_claude_token
from tests.api.conftest import BOX, World

TOKEN = "sk-ant-oat01-LOGINlogin_LOGINlogin-ABCD"
PASTED = "sk-ant-oat01-PASTEDpasted-PASTEDpasted-EFGH"
URL = "https://claude.ai/oauth/authorize?code=true&client_id=x&state=y"


class FakeSetupToken:
    def __init__(self, token: str | None = TOKEN) -> None:
        self.token = token
        self.codes: list[str] = []
        self.closed = False

    def wait_for_url(self, timeout_s: float) -> str:
        return URL

    def submit(self, code: str, timeout_s: float) -> str:
        self.codes.append(code)
        if self.token is None:
            raise LoginError("the CLI exited 1 without a token: invalid code")
        return self.token

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def cli(settings: Settings) -> Iterator[list[FakeSetupToken]]:
    started: list[FakeSetupToken] = []
    settings.claude_code_oauth_token = SecretStr("")
    settings.llm_api_key = SecretStr("")

    def factory() -> FakeSetupToken:
        started.append(FakeSetupToken())
        return started[-1]

    sessions = LoginSessions()
    app.dependency_overrides[get_setup_token_factory] = lambda: factory
    app.dependency_overrides[get_login_sessions] = lambda: sessions
    app.dependency_overrides[get_login_supported] = lambda: True
    yield started
    for dep in (get_setup_token_factory, get_login_sessions, get_login_supported):
        app.dependency_overrides.pop(dep, None)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/admin/llm-token"),
        ("put", "/admin/llm-token"),
        ("delete", "/admin/llm-token"),
        ("post", "/admin/llm-login"),
        ("post", "/admin/llm-login/x"),
    ],
)
def test_token_endpoints_are_admin_only(
    client: TestClient,
    world: World,
    as_user: Any,
    cli: list[FakeSetupToken],
    method: str,
    path: str,
) -> None:
    body = {"token": PASTED} if method == "put" else {"code": "abcdefgh#state"}
    kwargs: dict[str, Any] = {"headers": as_user(world.lead)}
    if method in ("put", "post"):
        kwargs["json"] = body
    response = getattr(client, method)(f"/api/v1{path}", **kwargs)
    assert response.status_code == 403
    assert cli == []


def test_login_stores_the_token_encrypted_and_never_returns_it(
    client: TestClient, world: World, as_user: Any, cli: list[FakeSetupToken], db_session: Session
) -> None:
    admin = as_user(world.admin)
    assert client.get("/api/v1/admin/llm-token", headers=admin).json()["active"] == "none"

    started = client.post("/api/v1/admin/llm-login", headers=admin)
    assert started.status_code == 200
    assert started.json()["url"] == URL
    login_id = started.json()["login_id"]

    done = client.post(
        f"/api/v1/admin/llm-login/{login_id}", headers=admin, json={"code": "abcdefgh#state"}
    )
    assert done.status_code == 200
    body = done.json()
    assert body["active"] == "stored" and body["stored_source"] == "login"
    assert body["token_hint"] == "sk-ant-oat01-…ABCD"
    assert TOKEN not in done.text
    assert cli[0].codes == ["abcdefgh#state"] and cli[0].closed

    row = db_session.get(StoredSecret, CLAUDE_TOKEN)
    assert row is not None and TOKEN not in row.value_encrypted
    stored = stored_claude_token(db_session, BOX)
    assert stored is not None and stored.token == TOKEN and stored.set_by == world.admin.id

    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "llm.login")).one()
    assert audit.details == {"ok": True} and TOKEN not in json.dumps(audit.details)

    # The login is over: the same id cannot be used twice.
    again = client.post(
        f"/api/v1/admin/llm-login/{login_id}", headers=admin, json={"code": "abcdefgh#state"}
    )
    assert again.status_code == 404


def test_login_with_a_bad_code_reports_and_stores_nothing(
    client: TestClient, world: World, as_user: Any, cli: list[FakeSetupToken], db_session: Session
) -> None:
    admin = as_user(world.admin)
    login_id = client.post("/api/v1/admin/llm-login", headers=admin).json()["login_id"]
    cli[0].token = None
    response = client.post(
        f"/api/v1/admin/llm-login/{login_id}", headers=admin, json={"code": "wrongcode#s"}
    )
    assert response.status_code == 502
    assert "invalid code" in response.json()["detail"]
    assert db_session.get(StoredSecret, CLAUDE_TOKEN) is None


def test_a_new_login_cancels_the_previous_one(
    client: TestClient, world: World, as_user: Any, cli: list[FakeSetupToken]
) -> None:
    admin = as_user(world.admin)
    first = client.post("/api/v1/admin/llm-login", headers=admin).json()["login_id"]
    client.post("/api/v1/admin/llm-login", headers=admin)
    assert cli[0].closed and not cli[1].closed
    response = client.post(
        f"/api/v1/admin/llm-login/{first}", headers=admin, json={"code": "abcdefgh#state"}
    )
    assert response.status_code == 404


def test_login_is_refused_where_no_terminal_can_run_it(
    client: TestClient, world: World, as_user: Any, cli: list[FakeSetupToken]
) -> None:
    app.dependency_overrides[get_login_supported] = lambda: False
    response = client.post("/api/v1/admin/llm-login", headers=as_user(world.admin))
    assert response.status_code == 501
    assert cli == []


def test_pasted_token_wins_over_the_environment_and_can_be_cleared(
    client: TestClient,
    world: World,
    as_user: Any,
    cli: list[FakeSetupToken],
    settings: Settings,
    db_session: Session,
) -> None:
    settings.claude_code_oauth_token = SecretStr("sk-ant-oat01-fromtheenvironment-ZZZZ")
    admin = as_user(world.admin)
    assert client.get("/api/v1/admin/llm-token", headers=admin).json()["active"] == "env_oauth"

    response = client.put("/api/v1/admin/llm-token", headers=admin, json={"token": PASTED})
    assert response.status_code == 200
    assert response.json()["active"] == "stored" and response.json()["stored_source"] == "paste"
    assert PASTED not in response.text

    assert client.delete("/api/v1/admin/llm-token", headers=admin).status_code == 204
    assert client.get("/api/v1/admin/llm-token", headers=admin).json()["active"] == "env_oauth"
    actions = db_session.scalars(
        select(AuditLog.action).where(AuditLog.action.like("llm.token.%"))
    ).all()
    assert sorted(actions) == ["llm.token.clear", "llm.token.set"]


def test_only_subscription_tokens_can_be_pasted(
    client: TestClient, world: World, as_user: Any, cli: list[FakeSetupToken]
) -> None:
    response = client.put(
        "/api/v1/admin/llm-token",
        headers=as_user(world.admin),
        json={"token": "sk-ant-api03-anapikeyanapikeyanapikey"},
    )
    assert response.status_code == 422
