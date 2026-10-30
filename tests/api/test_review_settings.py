from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin_settings import DEFAULT_REVIEW_MODEL, link_base, with_overrides
from app.config import Settings
from app.models import AuditLog
from app.workers.review_jobs import review_settings
from tests.api.conftest import World


def _item(body: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return next(item for item in body if item["key"] == key)


def _loc(body: list[dict[str, Any]]) -> dict[str, Any]:
    return _item(body, "max_review_loc")


@pytest.mark.parametrize("who", ["lead", "manager", "an"])
def test_review_settings_are_admin_only(
    client: TestClient, world: World, as_user: Any, who: str
) -> None:
    headers = as_user(getattr(world, who))
    assert client.get("/api/v1/admin/review-settings", headers=headers).status_code == 403
    response = client.put(
        "/api/v1/admin/review-settings", headers=headers, json={"max_review_loc": 5000}
    )
    assert response.status_code == 403


def test_max_review_loc_overrides_the_environment_and_reaches_the_worker(
    client: TestClient, world: World, as_user: Any, settings: Settings, db_session: Session
) -> None:
    admin = as_user(world.admin)
    initial = _loc(client.get("/api/v1/admin/review-settings", headers=admin).json())
    assert initial == {
        "key": "max_review_loc",
        "env": "MAX_REVIEW_LOC",
        "kind": "int",
        "value": settings.max_review_loc,
        "default": settings.max_review_loc,
        "overridden": False,
        "minimum": 1,
        "maximum": 1_000_000,
    }

    response = client.put(
        "/api/v1/admin/review-settings", headers=admin, json={"max_review_loc": 5000}
    )
    assert response.status_code == 200
    assert _loc(response.json())["value"] == 5000 and _loc(response.json())["overridden"]
    assert review_settings(settings, db_session).max_review_loc == 5000

    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "settings.update")).one()
    assert audit.target == "setting:max_review_loc"
    assert audit.details == {"old": settings.max_review_loc, "new": 5000}

    # null restores the environment default.
    response = client.put(
        "/api/v1/admin/review-settings", headers=admin, json={"max_review_loc": None}
    )
    assert _loc(response.json())["overridden"] is False
    assert review_settings(settings, db_session).max_review_loc == settings.max_review_loc


def test_review_agent_min_loc_reaches_the_worker(
    client: TestClient, world: World, as_user: Any, settings: Settings, db_session: Session
) -> None:
    admin = as_user(world.admin)
    url = "/api/v1/admin/review-settings"
    assert review_settings(settings, db_session).agent_min_loc == settings.review_agent_min_loc
    # 0 is allowed: every diff of a repo with "Sâu" on gets the deep pass.
    response = client.put(url, headers=admin, json={"review_agent_min_loc": 0})
    assert response.status_code == 200
    assert review_settings(settings, db_session).agent_min_loc == 0
    assert client.put(url, headers=admin, json={"review_agent_min_loc": -1}).status_code == 422
    client.put(url, headers=admin, json={"review_agent_min_loc": None})
    assert review_settings(settings, db_session).agent_min_loc == settings.review_agent_min_loc


def test_review_model_defaults_to_opus_and_reaches_the_provider(
    client: TestClient, world: World, as_user: Any, settings: Settings, db_session: Session
) -> None:
    admin = as_user(world.admin)
    settings.llm_model = ""
    initial = _item(client.get("/api/v1/admin/review-settings", headers=admin).json(), "llm_model")
    assert initial["value"] == DEFAULT_REVIEW_MODEL == "claude-opus-5-5"
    assert initial["kind"] == "model" and initial["overridden"] is False

    response = client.put(
        "/api/v1/admin/review-settings", headers=admin, json={"llm_model": " claude-sonnet-5-5 "}
    )
    assert response.status_code == 200
    assert _item(response.json(), "llm_model")["value"] == "claude-sonnet-5-5"
    assert with_overrides(db_session, settings).llm_model == "claude-sonnet-5-5"
    assert settings.llm_model == ""  # the shared Settings object is not changed

    client.put("/api/v1/admin/review-settings", headers=admin, json={"llm_model": None})
    assert with_overrides(db_session, settings).llm_model == DEFAULT_REVIEW_MODEL


def test_public_url_replaces_the_link_base(
    client: TestClient, world: World, as_user: Any, settings: Settings, db_session: Session
) -> None:
    admin = as_user(world.admin)
    assert link_base(db_session, settings) == "https://dash.example.com"
    response = client.put(
        "/api/v1/admin/review-settings", headers=admin, json={"public_url": "abc.xyz/codepulse/"}
    )
    assert response.status_code == 200
    assert _item(response.json(), "public_url")["value"] == "https://abc.xyz/codepulse"
    assert link_base(db_session, settings) == "https://abc.xyz/codepulse"
    assert with_overrides(db_session, settings).dashboard_url == "https://abc.xyz/codepulse"

    client.put("/api/v1/admin/review-settings", headers=admin, json={"public_url": None})
    assert link_base(db_session, settings) == "https://dash.example.com"


@pytest.mark.parametrize(
    "body",
    [
        {"max_review_loc": 0},
        {"max_review_loc": "x"},
        {"max_review_loc": True},
        {"other": 1},
        {"llm_model": "--dangerously-skip-permissions"},
        {"llm_model": "opus 5"},
        {"public_url": "ftp://abc.xyz"},
        {"public_url": "https://abc.xyz/?a=1"},
        {"public_url": "https://user:pw@abc.xyz"},
        {"public_url": "abc.xyz:port"},
    ],
)
def test_bad_values_are_refused(
    client: TestClient, world: World, as_user: Any, body: dict[str, Any]
) -> None:
    response = client.put("/api/v1/admin/review-settings", headers=as_user(world.admin), json=body)
    assert response.status_code == 422


def test_claude_limits_are_editable_and_reach_the_review_worker(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    admin = as_user(world.admin)
    body = client.get("/api/v1/admin/review-settings", headers=admin).json()
    assert _item(body, "claude_file_chars")["value"] == settings.claude_file_chars == 60_000
    assert _item(body, "deep_instructions_chars")["env"] == "DEEP_INSTRUCTIONS_CHARS"
    low = client.put("/api/v1/admin/review-settings", headers=admin, json={"claude_file_chars": 10})
    assert low.status_code == 422
    put = {"claude_file_chars": 90_000, "fast_guidance_chars": 200_000}
    saved = client.put("/api/v1/admin/review-settings", headers=admin, json=put)
    assert saved.status_code == 200, saved.text
    worker = review_settings(settings, db_session)
    assert (worker.claude_file_chars, worker.fast_guidance_chars) == (90_000, 200_000)
    assert worker.deep_instructions_chars == settings.deep_instructions_chars
