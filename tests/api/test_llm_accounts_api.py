"""Admin > LLM: several Claude accounts, one default and backups (user 2026-11-09)."""

import json
import subprocess
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.main import app
from app.api.routers.llm import get_llm_runner
from app.config import Settings
from app.models import LlmAccount
from tests.api.conftest import World

API = "/api/v1/admin/llm-accounts"
A = "sk-ant-oat01-" + "a" * 30 + "AAAA"
B = "sk-ant-oat01-" + "b" * 30 + "BBBB"
KEY = "sk-ant-api03-" + "k" * 30 + "KKKK"


@pytest.fixture(autouse=True)
def _no_env_token(settings: Settings) -> None:
    settings.claude_code_oauth_token = SecretStr("")
    settings.llm_api_key = SecretStr("")


def _add(client: TestClient, admin: Any, label: str, token: str) -> dict[str, Any]:
    res = client.post(API, headers=admin, json={"label": label, "token": token})
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def test_accounts_are_admin_only(client: TestClient, world: World, as_user: Any) -> None:
    lead = as_user(world.lead)
    assert client.get(API, headers=lead).status_code == 403
    assert client.post(API, headers=lead, json={"label": "x", "token": A}).status_code == 403


def test_the_first_account_is_the_default_and_tokens_are_never_returned(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    _add(client, admin, "Chính", A)
    body = _add(client, admin, "Dự phòng (API key)", KEY)
    assert A not in json.dumps(body) and KEY not in json.dumps(body)
    rows = body["accounts"]
    assert [(r["label"], r["is_default"]) for r in rows] == [
        ("Chính", True),
        ("Dự phòng (API key)", False),
    ]
    assert rows[0]["token_hint"] == "sk-ant-oat01-…AAAA"
    assert body["active_id"] == rows[0]["id"] and body["model"] and body["retry_min"] == 15


def test_default_order_rename_disable_and_delete(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    first = _add(client, admin, "A", A)["accounts"][0]["id"]
    second = _add(client, admin, "B", B)["accounts"][1]["id"]

    # The default cannot be switched off: another one is made default first.
    res = client.patch(f"{API}/{first}", headers=admin, json={"enabled": False})
    assert res.status_code == 409
    res = client.patch(f"{API}/{second}", headers=admin, json={"is_default": True, "label": "B2"})
    assert [(r["label"], r["is_default"]) for r in res.json()["accounts"]] == [
        ("B2", True),
        ("A", False),
    ]
    res = client.patch(f"{API}/{first}", headers=admin, json={"enabled": False})
    assert res.status_code == 200 and res.json()["accounts"][1]["enabled"] is False

    assert client.delete(f"{API}/{second}", headers=admin).status_code == 204
    left = db_session.scalars(select(LlmAccount)).all()
    # The only one left is switched off: it does not become the default by itself (an admin
    # switched it off on purpose); the next account added will be.
    assert [(r.label, r.is_default) for r in left] == [("A", False)]
    assert client.delete(f"{API}/999999", headers=admin).status_code == 404


def test_a_login_can_add_an_account_or_renew_one(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.api.routers.llm import (
        get_login_sessions,
        get_login_supported,
        get_setup_token_factory,
    )
    from app.review.claude_login import LoginSessions
    from tests.api.test_llm_login import FakeSetupToken

    sessions = LoginSessions()
    app.dependency_overrides[get_setup_token_factory] = lambda: lambda: FakeSetupToken(B)
    app.dependency_overrides[get_login_sessions] = lambda: sessions
    app.dependency_overrides[get_login_supported] = lambda: True
    try:
        admin = as_user(world.admin)
        first = _add(client, admin, "A", A)["accounts"][0]["id"]
        row = db_session.get(LlmAccount, first)
        assert row is not None
        row.state, row.failures = "auth", 3
        db_session.flush()

        def login(**extra: Any) -> None:
            login_id = client.post("/api/v1/admin/llm-login", headers=admin).json()["login_id"]
            res = client.post(
                f"/api/v1/admin/llm-login/{login_id}",
                headers=admin,
                json={"code": "abcdefgh#state", **extra},
            )
            assert res.status_code == 200, res.text

        login(label="Thêm")  # a new backup
        login(account_id=first)  # a new token for A: usable again at once
        rows = client.get(API, headers=admin).json()["accounts"]
        assert [(r["label"], r["state"], r["source"]) for r in rows] == [
            ("A", "ok", "login"),
            ("Thêm", "ok", "login"),
        ]
    finally:
        for dep in (get_setup_token_factory, get_login_sessions, get_login_supported):
            app.dependency_overrides.pop(dep, None)


def test_a_check_that_fails_on_the_login_marks_the_account_and_one_that_works_clears_it(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    account = _add(client, admin, "A", A)["accounts"][0]["id"]
    failing = json.dumps(
        {"is_error": True, "subtype": "success", "api_error_status": 401, "result": "bad token"}
    )
    ok = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": {"ok": True},
            "usage": {},
            "modelUsage": {"claude-opus-5-5": {}},
        }
    )
    outcomes = iter([("1.0", 0), ("{}", 0), (failing, 1), ("1.0", 0), ("{}", 0), (ok, 0)])

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        out, code = next(outcomes)
        return subprocess.CompletedProcess(cmd, code, out, "")

    app.dependency_overrides[get_llm_runner] = lambda: runner
    try:
        res = client.post(f"{API}/{account}/check", headers=admin)
        assert res.status_code == 200 and res.json()["status"]["ok"] is False
        row = db_session.get(LlmAccount, account)
        assert row is not None
        db_session.refresh(row)
        assert row.state == "auth" and row.retry_at is not None
        res = client.post(f"{API}/{account}/check", headers=admin)
        assert res.json()["status"]["ok"] is True
        db_session.refresh(row)
        assert row.state == "ok" and row.retry_at is None
    finally:
        app.dependency_overrides.pop(get_llm_runner, None)
