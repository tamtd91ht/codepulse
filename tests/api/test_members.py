"""GitLab members of a repo or dự án -> CodePulse people; commit authors -> people."""

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Account, AuditLog, BusinessProject, Project, User
from tests.api.conftest import World

pytestmark = pytest.mark.integration

API = "https://gitlab.example.com/api/v4"
MEMBERS = [
    {
        "id": 11,
        "username": "an",
        "name": "An Nguyen",
        "state": "active",
        "access_level": 30,
        "web_url": "https://gitlab.example.com/an",
    },
    {"id": 12, "username": "binh.tran", "name": "Binh", "state": "active", "access_level": 40},
    {"id": 13, "username": "newdev", "name": "New Dev", "state": "active", "access_level": 30},
    {"id": 14, "username": "renovate-bot", "name": "Renovate", "state": "active"},
    {"id": 15, "username": "gone", "name": "Gone", "state": "blocked", "access_level": 30},
    {"id": 16, "username": "lead", "name": "Lead", "state": "active", "access_level": 50},
]
USERS = {m["id"]: m for m in MEMBERS}


def mock_gitlab(router: respx.MockRouter) -> respx.Route:
    router.route(host="testserver").pass_through()
    members = router.get(f"{API}/projects/1001/members/all").respond(json=MEMBERS)
    router.get(f"{API}/users/13").respond(json={**USERS[13], "public_email": "NewDev@Gmail.com"})
    for gitlab_id, user in USERS.items():
        if gitlab_id != 13:
            router.get(f"{API}/users/{gitlab_id}").respond(json={**user, "public_email": ""})
    router.get(f"{API}/users/99").respond(404)
    return members


@pytest.fixture
def members_world(db_session: Session, world: World, settings: Settings) -> World:
    settings.allowed_email_domains = ["x.vn"]
    db_session.add_all(
        [
            Account(
                kind="gitlab_user", external_key="16", display_name="lead", user_id=world.lead.id
            ),
            # newdev already pushed with GitLab's noreply address; nobody owns it yet.
            Account(kind="git_email", external_key="13-newdev@users.noreply.gitlab.example.com"),
        ]
    )
    db_session.flush()
    return world


def listed(client: TestClient, world: World, as_user: Any) -> dict[str, Any]:
    res = client.get(
        f"/api/v1/projects/{world.project.id}/gitlab-members", headers=as_user(world.admin)
    )
    assert res.status_code == 200, res.text
    return dict(res.json())


def test_repo_members_come_with_what_to_do(
    client: TestClient, members_world: World, as_user: Any
) -> None:
    world = members_world
    with respx.mock(assert_all_called=False) as router:
        members = mock_gitlab(router)
        body = listed(client, world, as_user)
        assert members.called
    by_name = {m["username"]: m for m in body["members"]}
    assert body["repos"] == 1 and body["truncated"] is False
    assert (by_name["an"]["action"], by_name["an"]["user_id"]) == ("link", world.an.id)
    assert by_name["an"]["reason"] == "username_domain"
    # The same full name is only a hint: not preselected.
    assert (by_name["binh.tran"]["action"], by_name["binh.tran"]["reason"]) == (
        "review",
        "same_name",
    )
    assert by_name["binh.tran"]["user_id"] == world.binh.id
    assert by_name["newdev"]["action"] == "create"
    assert by_name["newdev"]["email"] == "newdev@gmail.com"
    assert by_name["renovate-bot"]["action"] == "skip"
    assert (by_name["gone"]["action"], by_name["gone"]["reason"]) == ("skip", "blocked")
    assert (by_name["lead"]["action"], by_name["lead"]["user_id"]) == ("linked", world.lead.id)
    assert by_name["lead"]["access"] == "owner"
    assert by_name["an"]["connection_id"] == world.project.connection_id
    # GitLab id and profile page: to check it is the right member.
    assert (by_name["an"]["gitlab_id"], by_name["an"]["web_url"]) == (
        11,
        "https://gitlab.example.com/an",
    )
    assert by_name["binh.tran"]["web_url"] is None
    # Only admins.
    for url in (
        f"/api/v1/projects/{world.project.id}/gitlab-members",
        "/api/v1/business-projects/1/gitlab-members",
    ):
        assert client.get(url, headers=as_user(world.lead)).status_code == 403


def test_dự_án_members_list_every_tracked_repo(
    client: TestClient, members_world: World, as_user: Any, db_session: Session
) -> None:
    world = members_world
    bp = BusinessProject(name="OmiCRM")
    db_session.add(bp)
    db_session.flush()
    world.project.business_project_id = bp.id
    db_session.flush()
    with respx.mock(assert_all_called=False) as router:
        mock_gitlab(router)
        res = client.get(
            f"/api/v1/business-projects/{bp.id}/gitlab-members", headers=as_user(world.admin)
        )
    assert res.status_code == 200, res.text
    assert len(res.json()["members"]) == len(MEMBERS)
    assert res.json()["members"][0]["repos"] == ["demo/p"]


def test_a_repo_gitlab_refuses_is_reported_and_the_others_listed(
    client: TestClient, members_world: World, as_user: Any, db_session: Session
) -> None:
    world = members_world
    other = Project(
        connection_id=world.project.connection_id,
        external_id=1002,
        path_with_namespace="demo/secret",
        business_project_id=world.project.business_project_id,
    )
    db_session.add(other)
    db_session.flush()
    bp_id = world.project.business_project_id
    with respx.mock(assert_all_called=False) as router:
        mock_gitlab(router)
        router.get(f"{API}/projects/1002/members/all").respond(403)
        res = client.get(
            f"/api/v1/business-projects/{bp_id}/gitlab-members", headers=as_user(world.admin)
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["members"]) == len(MEMBERS)
    assert [(f["repo"], f["reason"]) for f in body["failed"]] == [("demo/secret", "forbidden")]
    assert "không đủ quyền" in body["failed"][0]["message"]


def test_a_bad_token_fails_the_connection_s_other_repos_at_once(
    client: TestClient, members_world: World, as_user: Any, db_session: Session
) -> None:
    world = members_world
    other = Project(
        connection_id=world.project.connection_id,
        external_id=1002,
        path_with_namespace="demo/zz",
        business_project_id=world.project.business_project_id,
    )
    db_session.add(other)
    db_session.flush()
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        first = router.get(f"{API}/projects/1001/members/all").respond(401)
        second = router.get(f"{API}/projects/1002/members/all").respond(json=MEMBERS)
        res = client.get(
            f"/api/v1/business-projects/{world.project.business_project_id}/gitlab-members",
            headers=as_user(world.admin),
        )
    assert first.called and not second.called
    assert res.status_code == 502
    assert "demo/p (và 1 repo khác)" in res.json()["detail"]


def test_nothing_listed_says_why(client: TestClient, members_world: World, as_user: Any) -> None:
    world = members_world
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/1001/members/all").respond(401)
        res = client.get(
            f"/api/v1/projects/{world.project.id}/gitlab-members", headers=as_user(world.admin)
        )
    assert res.status_code == 502
    assert "demo/p" in res.json()["detail"] and "hết hạn" in res.json()["detail"]


def test_a_member_is_added_by_username_id_or_profile_url(
    client: TestClient, members_world: World, as_user: Any
) -> None:
    world = members_world

    def lookup(q: str, who: Any = None) -> Any:
        return client.get(
            "/api/v1/gitlab-members/lookup", params={"q": q}, headers=as_user(who or world.admin)
        )

    with respx.mock(assert_all_called=False) as router:
        mock_gitlab(router)
        router.get(f"{API}/users", params={"username": "newdev"}).respond(
            json=[{**USERS[13], "public_email": "NewDev@Gmail.com"}]
        )
        router.get(f"{API}/users", params={"username": "an"}).respond(json=[USERS[11]])
        router.get(f"{API}/users", params={"username": "ghost"}).respond(json=[])
        new = lookup("@newdev")
        by_url = lookup("https://gitlab.example.com/an/?tab=activity")
        by_id = lookup("16")
        ghost = lookup("ghost")
        router.get(f"{API}/users", params={"username": "²"}).respond(json=[])
        odd_digit = lookup("\u00b2")  # not an id: looked up as a username, not a 500
        refused = lookup("an", world.an)
    assert new.status_code == 200, new.text
    m = new.json()["members"][0]
    assert (m["gitlab_id"], m["action"], m["email"], m["access"]) == (
        13,
        "create",
        "newdev@gmail.com",
        "none",
    )
    assert (by_url.json()["members"][0]["action"], by_url.json()["members"][0]["user_id"]) == (
        "link",
        world.an.id,
    )
    assert by_id.json()["members"][0]["action"] == "linked"
    assert ghost.status_code == 404 and "ghost" in ghost.json()["detail"]
    assert odd_digit.status_code == 404
    assert refused.status_code == 403


def test_import_creates_and_links_the_confirmed_members(
    client: TestClient, members_world: World, as_user: Any, db_session: Session
) -> None:
    world = members_world
    conn = world.project.connection_id
    choices = [
        {"gitlab_id": 11, "connection_id": conn, "action": "link", "user_id": world.an.id},
        {
            "gitlab_id": 13,
            "connection_id": conn,
            "action": "create",
            "full_name": "New Dev",
            "email": "newdev@gmail.com",
        },
        {"gitlab_id": 14, "connection_id": conn, "action": "skip"},
    ]
    with respx.mock(assert_all_called=False) as router:
        mock_gitlab(router)
        res = client.post(
            "/api/v1/gitlab-members/import",
            json={"members": choices},
            headers=as_user(world.admin),
        )
    assert res.status_code == 200, res.text
    assert res.json() == {
        "created": 1,
        "linked": 2,
        "skipped": 1,
        "emails_linked": 1,
        "reviews_attributed": 0,
        "periods_queued": 0,
    }
    new = db_session.scalars(select(User).where(User.primary_email == "newdev@gmail.com")).one()
    accounts = {
        a.external_key: (a.user_id, a.display_name)
        for a in db_session.scalars(select(Account).where(Account.kind == "gitlab_user"))
    }
    # The username stored comes from GitLab.
    assert accounts["11"] == (world.an.id, "an") and accounts["13"] == (new.id, "newdev")
    assert "14" not in accounts
    # The noreply commit email of newdev follows the GitLab account.
    noreply = db_session.scalars(
        select(Account).where(Account.external_key.startswith("13-newdev@"))
    ).one()
    assert noreply.user_id == new.id
    # One audit entry per linked member: who went to whom, from whom.
    entries = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "identity.import_member")
    ).all()
    assert len(entries) == 2


def test_import_refuses_to_move_an_account_or_trust_the_request(
    client: TestClient, members_world: World, as_user: Any, db_session: Session
) -> None:
    world = members_world
    conn = world.project.connection_id
    admin = as_user(world.admin)

    def post(*members: dict[str, Any]) -> Any:
        with respx.mock(assert_all_called=False) as router:
            mock_gitlab(router)
            return client.post(
                "/api/v1/gitlab-members/import", json={"members": list(members)}, headers=admin
            )

    # Lead's GitLab account to An: lead would sign in as An. Refused unless moved explicitly.
    move = {"gitlab_id": 16, "connection_id": conn, "action": "link", "user_id": world.an.id}
    res = post(move)
    assert res.status_code == 409 and "đã được gắn" in res.json()["detail"]
    account = db_session.scalars(select(Account).where(Account.external_key == "16")).one()
    db_session.refresh(account)
    assert account.user_id == world.lead.id
    # A person who already has a GitLab account does not get a second one silently.
    second = {"gitlab_id": 11, "connection_id": conn, "action": "link", "user_id": world.lead.id}
    assert post(second).status_code == 409
    # Blocked on GitLab, unknown on GitLab, another person's email, the same member twice.
    blocked = {"gitlab_id": 15, "connection_id": conn, "action": "create"}
    assert post(blocked).status_code == 409
    assert post({"gitlab_id": 99, "connection_id": conn, "action": "create"}).status_code == 404
    clash = {"gitlab_id": 13, "connection_id": conn, "action": "create", "email": "an@x.vn"}
    assert post(clash).status_code == 409
    assert post(clash, {**clash, "email": None}).status_code == 422
    nobody = {"gitlab_id": 11, "connection_id": conn, "action": "link", "user_id": 9999}
    assert post(nobody).status_code == 404
    assert not db_session.scalars(
        select(Account).where(Account.kind == "gitlab_user", Account.external_key == "13")
    ).first()
    # The move, asked for.
    assert post({**move, "move": True}).status_code == 200
    db_session.refresh(account)
    assert account.user_id == world.an.id
    lead = as_user(world.lead)
    res = client.post("/api/v1/gitlab-members/import", json={"members": [move]}, headers=lead)
    assert res.status_code == 403


def test_a_commit_author_links_to_a_person_or_a_new_one(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    res = client.post(
        "/api/v1/identity/authors",
        json={"email": "An.Personal@Gmail.com", "name": "An", "user_id": world.an.id},
        headers=admin,
    )
    assert res.status_code == 200, res.text
    assert (res.json()["external_key"], res.json()["user_id"]) == (
        "an.personal@gmail.com",
        world.an.id,
    )
    # Already An's: not moved to someone else.
    moved = {"email": "an.personal@gmail.com", "user_id": world.binh.id}
    assert client.post("/api/v1/identity/authors", json=moved, headers=admin).status_code == 409
    res = client.post(
        "/api/v1/identity/authors",
        json={"email": "c@x.vn", "name": "Cuong", "create": True},
        headers=admin,
    )
    assert res.status_code == 200, res.text
    cuong = db_session.scalars(select(User).where(User.primary_email == "c@x.vn")).one()
    assert cuong.full_name == "Cuong" and res.json()["user_id"] == cuong.id
    taken = {"email": "AN@x.vn", "create": True}
    assert client.post("/api/v1/identity/authors", json=taken, headers=admin).status_code == 409
    nobody = {"email": "d@x.vn"}
    assert client.post("/api/v1/identity/authors", json=nobody, headers=admin).status_code == 422
    lead = as_user(world.lead)
    assert client.post("/api/v1/identity/authors", json=nobody, headers=lead).status_code == 403


# -- GET /users/:id closed on the instance (a non-admin token on a locked-down GitLab) --


def _create(world: World, repos: list[str]) -> dict[str, Any]:
    return {
        "gitlab_id": 13,
        "connection_id": world.project.connection_id,
        "action": "create",
        "full_name": "New Dev",
        "email": "newdev@gmail.com",
        "repos": repos,
    }


def test_a_member_is_read_from_the_repo_when_the_users_api_is_closed(
    client: TestClient, members_world: World, as_user: Any, db_session: Session
) -> None:
    world = members_world
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/users/13").respond(403)
        entry = router.get(f"{API}/projects/1001/members/all/13").respond(json=USERS[13])
        res = client.post(
            "/api/v1/gitlab-members/import",
            json={"members": [_create(world, ["demo/p", "not/tracked"])]},
            headers=as_user(world.admin),
        )
    assert res.status_code == 200, res.text
    assert entry.called and res.json()["created"] == 1
    account = db_session.scalars(
        select(Account).where(Account.kind == "gitlab_user", Account.external_key == "13")
    ).one()
    assert account.display_name == "newdev" and account.user_id is not None


def test_nothing_is_saved_and_the_admin_is_told_why(
    client: TestClient, members_world: World, as_user: Any, db_session: Session
) -> None:
    world = members_world
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/users/13").respond(403)
        res = client.post(
            "/api/v1/gitlab-members/import",
            json={"members": [_create(world, [])]},
            headers=as_user(world.admin),
        )
    assert res.status_code == 502
    assert "không đủ quyền" in res.json()["detail"] and "Chưa lưu gì" in res.json()["detail"]
    assert db_session.scalars(select(Account).where(Account.external_key == "13")).first() is None
