"""Admin > Người & Team: a member's profile (name, email) and their one avatar (2026-11-03)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditLog
from tests.api.conftest import World

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32


@pytest.fixture
def avatar_dir(settings: Settings, tmp_path: Path) -> Path:
    settings.avatar_dir = str(tmp_path / "avatars")
    settings.avatar_max_kb = 16
    return tmp_path / "avatars"


def put_avatar(client: TestClient, user_id: int, data: bytes, headers: dict[str, str]) -> Any:
    return client.put(
        f"/api/v1/users/{user_id}/avatar",
        content=data,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )


def test_admin_edits_name_and_email(
    client: TestClient, db_session: Session, world: World, as_user: Any
) -> None:
    h = as_user(world.admin)
    res = client.patch(
        f"/api/v1/users/{world.an.id}",
        headers=h,
        json={"full_name": "  Nguyễn Văn An ", "primary_email": "An.Nguyen@X.vn"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["full_name"], body["primary_email"]) == ("Nguyễn Văn An", "an.nguyen@x.vn")
    got = client.get(f"/api/v1/users/{world.an.id}", headers=h).json()
    assert got["primary_email"] == "an.nguyen@x.vn" and got["avatar_url"] is None
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "user.update").order_by(AuditLog.id.desc())
    ).first()
    assert entry is not None and entry.details and entry.details["previous_email"] == "an@x.vn"
    # Someone else's email, a malformed one, an empty name: refused.
    taken = client.patch(
        f"/api/v1/users/{world.an.id}", headers=h, json={"primary_email": "binh@x.vn"}
    )
    assert taken.status_code == 409
    for bad in ({"primary_email": "no-at-sign"}, {"full_name": ""}):
        assert client.patch(f"/api/v1/users/{world.an.id}", headers=h, json=bad).status_code == 422
    # Admins keep their rights by email: they may not move their own email off ADMIN_EMAILS.
    own = client.patch(
        f"/api/v1/users/{world.admin.id}", headers=h, json={"primary_email": "boss@x.vn"}
    )
    assert own.status_code == 409
    # Only admins.
    assert (
        client.patch(
            f"/api/v1/users/{world.an.id}", headers=as_user(world.lead), json={"full_name": "x"}
        ).status_code
        == 403
    )
    assert (
        client.get(f"/api/v1/users/{world.an.id}", headers=as_user(world.lead)).status_code == 403
    )


def test_one_avatar_per_person(
    client: TestClient, world: World, avatar_dir: Path, as_user: Any
) -> None:
    h = as_user(world.admin)
    res = put_avatar(client, world.an.id, PNG, h)
    assert res.status_code == 200, res.text
    url = res.json()["avatar_url"]
    assert url.startswith(f"/api/v1/users/{world.an.id}/avatar?v=")
    assert sorted(p.name for p in avatar_dir.iterdir()) == [f"{world.an.id}.png"]
    # The member page reads it from /people/{id}; nothing else leaks (the file name stays inside).
    person = client.get(f"/api/v1/people/{world.an.id}", headers=as_user(world.lead)).json()
    assert person["avatar_url"] == url and "avatar_file" not in person
    # The sidebar shows the signed-in person's own photo.
    assert client.get("/api/v1/auth/me", headers=as_user(world.an)).json()["avatar_url"] == url
    # Another format replaces it: still one file.
    res = put_avatar(client, world.an.id, JPEG, h)
    assert res.status_code == 200
    assert sorted(p.name for p in avatar_dir.iterdir()) == [f"{world.an.id}.jpg"]
    # Anyone signed in may load it; it is served as what its bytes are.
    img = client.get(f"/api/v1/users/{world.an.id}/avatar", headers=as_user(world.binh))
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert img.content == JPEG and img.headers["x-content-type-options"] == "nosniff"
    assert put_avatar(client, world.binh.id, WEBP, h).status_code == 200
    # Removing it.
    gone = client.delete(f"/api/v1/users/{world.an.id}/avatar", headers=h)
    assert gone.status_code == 200 and gone.json()["avatar_url"] is None
    assert client.get(f"/api/v1/users/{world.an.id}/avatar", headers=h).status_code == 404
    assert sorted(p.name for p in avatar_dir.iterdir()) == [f"{world.binh.id}.webp"]


def test_avatar_upload_is_checked(
    client: TestClient, world: World, avatar_dir: Path, as_user: Any
) -> None:
    h = as_user(world.admin)
    assert put_avatar(client, world.an.id, b"<svg onload=alert(1)>", h).status_code == 422
    assert put_avatar(client, world.an.id, b"", h).status_code == 422
    assert put_avatar(client, world.an.id, PNG + b"\x00" * 17_000, h).status_code == 413
    assert put_avatar(client, world.an.id, PNG, as_user(world.an)).status_code == 403
    assert put_avatar(client, 999_999, PNG, h).status_code == 404
    assert client.get("/api/v1/users/999999/avatar", headers=h).status_code == 404
    assert client.get(f"/api/v1/users/{world.an.id}/avatar").status_code == 401
    assert not avatar_dir.exists() or not any(avatar_dir.iterdir())


def test_deleting_a_person_deletes_their_avatar(
    client: TestClient, world: World, avatar_dir: Path, as_user: Any
) -> None:
    h = as_user(world.admin)
    assert put_avatar(client, world.outsider.id, PNG, h).status_code == 200
    assert client.delete(f"/api/v1/users/{world.outsider.id}", headers=h).status_code == 204
    assert list(avatar_dir.iterdir()) == []


def test_an_admin_email_cannot_be_handed_out(
    client: TestClient, world: World, settings: Settings, as_user: Any
) -> None:
    """An ADMIN_EMAILS address makes its holder admin: that is granted in the config only."""
    settings.admin_emails = [*settings.admin_emails, "boss@x.vn"]
    res = client.patch(
        f"/api/v1/users/{world.an.id}",
        headers=as_user(world.admin),
        json={"primary_email": "Boss@x.vn"},
    )
    assert res.status_code == 409 and "ADMIN_EMAILS" in res.json()["detail"]
