"""Personal access tokens for Claude Code (docs/specs/26 §2): the REST endpoints of the "Của
tôi" page and Admin -> Người dùng, and app/mcp/tokens.authenticate."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.mcp.tokens import (
    MAX_ACTIVE_PER_USER,
    SCOPE_CLOSE,
    SCOPE_READ,
    TOKEN_PREFIX,
    TokenError,
    authenticate,
    create_token,
    hash_token,
)
from app.models import ApiToken, AuditLog
from tests.api.conftest import World

pytestmark = pytest.mark.integration

FIELDS = {
    "id",
    "name",
    "prefix",
    "scopes",
    "expires_at",
    "last_used_at",
    "revoked_at",
    "created_at",
    "active",
}


def make(client: TestClient, headers: dict[str, str], **body: Any) -> Any:
    return client.post("/api/v1/me/tokens", headers=headers, json={"name": "laptop", **body})


# --- create -------------------------------------------------------------------------------


def test_create_defaults_to_both_scopes_and_90_days(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    made = make(client, as_user(world.an))
    assert made.status_code == 201, made.text
    body = made.json()
    assert set(body) == FIELDS | {"token"}
    assert body["token"].startswith(TOKEN_PREFIX)
    assert body["prefix"] == body["token"][len(TOKEN_PREFIX) :][:8]
    assert body["scopes"] == [SCOPE_READ, SCOPE_CLOSE]
    assert body["active"] is True and body["revoked_at"] is None
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(days=89) < expires - datetime.now(UTC) <= timedelta(days=90)
    row = db_session.get(ApiToken, body["id"])
    assert row is not None and row.user_id == world.an.id
    assert row.token_hash == hash_token(body["token"])  # only the hash is stored
    logged = db_session.scalars(select(AuditLog).where(AuditLog.action == "api_token.create"))
    (entry,) = list(logged)
    assert entry.actor_user_id == world.an.id and entry.target == f"api_token:{body['id']}"
    assert body["token"] not in str(entry.details)


def test_create_read_only_and_read_is_always_granted(
    client: TestClient, world: World, as_user: Any
) -> None:
    only_close = make(client, as_user(world.an), scopes=[SCOPE_CLOSE], days=30)
    assert only_close.status_code == 201
    assert only_close.json()["scopes"] == [SCOPE_READ, SCOPE_CLOSE]
    read_only = make(client, as_user(world.an), scopes=[], days=365)
    assert read_only.status_code == 201
    assert read_only.json()["scopes"] == [SCOPE_READ]


@pytest.mark.parametrize(
    "body",
    [
        {"days": 7},
        {"days": 0},
        {"scopes": ["findings:delete"]},
        {"name": "   "},
        {"name": "x" * 81},
    ],
)
def test_create_refuses_bad_input(
    client: TestClient, world: World, as_user: Any, body: dict[str, Any]
) -> None:
    assert make(client, as_user(world.an), **body).status_code == 422


def test_eleventh_active_token_is_refused_revoked_ones_do_not_count(
    client: TestClient, world: World, as_user: Any
) -> None:
    headers = as_user(world.an)
    ids = [make(client, headers, name=f"t{i}").json()["id"] for i in range(MAX_ACTIVE_PER_USER)]
    assert make(client, headers, name="one too many").status_code == 409
    assert client.post(f"/api/v1/me/tokens/{ids[0]}/revoke", headers=headers).status_code == 200
    assert make(client, headers, name="now fits").status_code == 201


# --- list ---------------------------------------------------------------------------------


def test_list_is_own_newest_first_and_never_shows_the_token(
    client: TestClient, world: World, as_user: Any
) -> None:
    first = make(client, as_user(world.an), name="first").json()
    second = make(client, as_user(world.an), name="second").json()
    make(client, as_user(world.binh), name="binh's")
    listed = client.get("/api/v1/me/tokens", headers=as_user(world.an))
    assert listed.status_code == 200
    rows = listed.json()
    assert [r["name"] for r in rows] == ["second", "first"]
    assert all(set(r) == FIELDS for r in rows)
    text = listed.text
    for made in (first, second):
        assert made["token"] not in text
        assert hash_token(made["token"]) not in text


# --- revoke -------------------------------------------------------------------------------


def test_revoke_own_token(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    made = make(client, as_user(world.an)).json()
    revoked = client.post(f"/api/v1/me/tokens/{made['id']}/revoke", headers=as_user(world.an))
    assert revoked.status_code == 200
    body = revoked.json()
    assert set(body) == FIELDS
    assert body["revoked_at"] is not None and body["active"] is False
    row = db_session.get(ApiToken, made["id"])
    assert row is not None and row.revoked_by == world.an.id
    actions = [a.action for a in db_session.scalars(select(AuditLog))]
    assert actions.count("api_token.revoke") == 1
    # Again: the same row, no second audit.
    again = client.post(f"/api/v1/me/tokens/{made['id']}/revoke", headers=as_user(world.an))
    assert again.status_code == 200
    actions = [a.action for a in db_session.scalars(select(AuditLog))]
    assert actions.count("api_token.revoke") == 1


def test_revoke_someone_elses_token_is_404(client: TestClient, world: World, as_user: Any) -> None:
    made = make(client, as_user(world.an)).json()
    other = client.post(f"/api/v1/me/tokens/{made['id']}/revoke", headers=as_user(world.binh))
    assert other.status_code == 404
    missing = client.post("/api/v1/me/tokens/999999/revoke", headers=as_user(world.an))
    assert missing.status_code == 404


# --- admin --------------------------------------------------------------------------------


def test_admin_lists_and_revokes_anyones_token(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    made = make(client, as_user(world.an)).json()
    listed = client.get(f"/api/v1/admin/users/{world.an.id}/tokens", headers=as_user(world.admin))
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()] == [made["id"]]
    assert made["token"] not in listed.text
    revoked = client.post(f"/api/v1/admin/tokens/{made['id']}/revoke", headers=as_user(world.admin))
    assert revoked.status_code == 200 and revoked.json()["active"] is False
    row = db_session.get(ApiToken, made["id"])
    assert row is not None and row.revoked_by == world.admin.id
    (entry,) = db_session.scalars(select(AuditLog).where(AuditLog.action == "api_token.revoke"))
    assert entry.actor_user_id == world.admin.id
    assert entry.details is not None and entry.details.get("by_admin") is True
    assert (
        client.get("/api/v1/admin/users/999999/tokens", headers=as_user(world.admin)).status_code
        == 404
    )
    assert (
        client.post("/api/v1/admin/tokens/999999/revoke", headers=as_user(world.admin)).status_code
        == 404
    )


def test_non_admin_cannot_use_the_admin_endpoints(
    client: TestClient, world: World, as_user: Any
) -> None:
    made = make(client, as_user(world.an)).json()
    for who in (world.an, world.lead, world.manager):
        headers = as_user(who)
        assert (
            client.get(f"/api/v1/admin/users/{world.an.id}/tokens", headers=headers).status_code
            == 403
        )
        assert (
            client.post(f"/api/v1/admin/tokens/{made['id']}/revoke", headers=headers).status_code
            == 403
        )


def test_a_personal_token_does_not_sign_in_to_rest(
    client: TestClient, world: World, as_user: Any
) -> None:
    raw = make(client, as_user(world.an)).json()["token"]
    bearer = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/v1/me/tokens", headers=bearer).status_code == 401
    assert client.post("/api/v1/me/tokens", headers=bearer, json={"name": "x"}).status_code == 401
    assert (
        client.get(f"/api/v1/admin/users/{world.an.id}/tokens", headers=bearer).status_code == 401
    )
    assert client.get("/api/v1/inbox", headers=bearer).status_code == 401
    assert client.get("/api/v1/me/tokens").status_code == 401  # nothing at all


# --- authenticate (app/mcp/tokens.py) -----------------------------------------------------

NOW = datetime(2026, 11, 5, 9, tzinfo=UTC)


def test_authenticate_gives_the_owner_and_scopes(
    db_session: Session, world: World, settings: Settings
) -> None:
    token, raw = create_token(db_session, world.an.id, "cli", [], now=NOW)
    auth = authenticate(db_session, raw, settings, now=NOW + timedelta(hours=1))
    assert auth.token.id == token.id and auth.principal.user_id == world.an.id
    assert auth.has(SCOPE_READ) and not auth.has(SCOPE_CLOSE)


@pytest.mark.parametrize("raw", [None, "", "Bearer x", "eyJhbGciOi.jwt.like"])
def test_authenticate_refuses_what_is_not_a_personal_token(
    db_session: Session, settings: Settings, raw: str | None
) -> None:
    with pytest.raises(TokenError):
        authenticate(db_session, raw, settings, now=NOW)


def test_authenticate_refuses_unknown_revoked_expired_and_inactive(
    db_session: Session, world: World, settings: Settings
) -> None:
    with pytest.raises(TokenError):
        authenticate(db_session, TOKEN_PREFIX + "not" + "-a-real-one", settings, now=NOW)

    revoked, raw_revoked = create_token(db_session, world.an.id, "old", [], now=NOW)
    revoked.revoked_at = NOW
    with pytest.raises(TokenError, match="thu hồi"):
        authenticate(db_session, raw_revoked, settings, now=NOW + timedelta(minutes=1))

    _, raw_short = create_token(db_session, world.an.id, "short", [], days=30, now=NOW)
    with pytest.raises(TokenError, match="hết hạn"):
        authenticate(db_session, raw_short, settings, now=NOW + timedelta(days=30))

    _, raw_binh = create_token(db_session, world.binh.id, "binh", [], now=NOW)
    world.binh.active = False
    db_session.flush()
    with pytest.raises(TokenError, match="không còn hoạt động"):
        authenticate(db_session, raw_binh, settings, now=NOW + timedelta(minutes=1))


def test_last_used_at_is_touched_at_most_every_five_minutes(
    db_session: Session, world: World, settings: Settings
) -> None:
    token, raw = create_token(db_session, world.an.id, "cli", [], now=NOW)
    assert token.last_used_at is None
    authenticate(db_session, raw, settings, now=NOW)
    assert token.last_used_at == NOW
    authenticate(db_session, raw, settings, now=NOW + timedelta(minutes=4, seconds=59))
    assert token.last_used_at == NOW
    later = NOW + timedelta(minutes=5)
    authenticate(db_session, raw, settings, now=later)
    assert token.last_used_at == later
