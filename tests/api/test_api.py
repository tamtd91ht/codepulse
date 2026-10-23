import json
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import STATE_COOKIE, get_oauth_http
from app.api.main import app
from app.api.security import SESSION_COOKIE
from app.config import Settings
from app.models import Account, AuditLog, Connection, User
from tests.api.conftest import BOX, WEEK, FakeQueue, World

pytestmark = pytest.mark.integration


def test_requests_need_a_valid_session(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/api/v1/auth/me", headers=bad).status_code == 401


def test_me_reports_roles(client: TestClient, world: World, as_user: Any) -> None:
    lead = client.get("/api/v1/auth/me", headers=as_user(world.lead)).json()
    assert (lead["is_admin"], lead["lead_teams"]) == (False, [world.backend.id])
    manager = client.get("/api/v1/auth/me", headers=as_user(world.manager)).json()
    assert set(manager["managed_teams"]) == {world.engineering.id, world.backend.id}  # sub-teams
    admin = client.get("/api/v1/auth/me", headers=as_user(world.admin)).json()
    assert admin["is_admin"] is True


@pytest.mark.parametrize(
    ("who", "status"),
    [
        ("an", 200),
        ("lead", 200),
        ("admin", 200),
        ("binh", 403),
        ("manager", 403),
        ("outsider", 403),
    ],
)
def test_individual_metrics_are_for_the_person_and_their_lead(
    client: TestClient, world: World, as_user: Any, who: str, status: int
) -> None:
    url = f"/api/v1/metrics/users/{world.an.id}?period=week&from={WEEK}&to={WEEK}"
    response = client.get(url, headers=as_user(getattr(world, who)))
    assert response.status_code == status
    if status == 200:
        assert response.json()["series"][0]["quality_index"] == "75.00"


@pytest.mark.parametrize(
    ("who", "status"), [("an", 200), ("lead", 200), ("manager", 200), ("outsider", 403)]
)
def test_team_metrics_visibility(
    client: TestClient, world: World, as_user: Any, who: str, status: int
) -> None:
    url = f"/api/v1/metrics/teams/{world.backend.id}?period=week&from={WEEK}&to={WEEK}"
    assert client.get(url, headers=as_user(getattr(world, who))).status_code == status


def test_leaderboard_hides_people_from_managers(
    client: TestClient, world: World, as_user: Any
) -> None:
    url = f"/api/v1/leaderboard?team_id={world.backend.id}&period=week&start={WEEK}"
    lead_view = client.get(url, headers=as_user(world.lead)).json()
    assert {m["user"]["full_name"] for m in lead_view["members"]} == {"An", "Binh"}
    manager_view = client.get(url, headers=as_user(world.manager)).json()
    assert manager_view["members"] is None
    assert manager_view["team_metrics"]["quality_index"] == "75.00"
    assert client.get(url, headers=as_user(world.an)).status_code == 403


def test_reviews_are_scoped_and_reads_of_others_are_audited(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    own = client.get("/api/v1/reviews", headers=as_user(world.an)).json()["items"]
    assert [r["id"] for r in own] == [world.an_review.id]
    team = client.get("/api/v1/reviews", headers=as_user(world.lead)).json()["items"]
    assert {r["id"] for r in team} == {world.an_review.id, world.binh_review.id}
    peek = client.get(f"/api/v1/reviews?user_id={world.an.id}", headers=as_user(world.binh))
    assert peek.status_code == 403
    assert (
        client.get(f"/api/v1/reviews/{world.an_review.id}", headers=as_user(world.binh)).status_code
        == 403
    )

    detail = client.get(f"/api/v1/reviews/{world.an_review.id}", headers=as_user(world.lead)).json()
    assert detail["findings"][0]["title"] == "SQL injection"
    assert detail["web_url"] == "https://gitlab.example.com/demo/p/-/merge_requests/7"
    actions = db_session.scalars(select(AuditLog.action)).all()
    assert "review.read" in actions


def test_review_list_pagination(client: TestClient, world: World, as_user: Any) -> None:
    first = client.get("/api/v1/reviews?limit=1", headers=as_user(world.admin)).json()
    assert len(first["items"]) == 1 and first["next_cursor"]
    second = client.get(
        f"/api/v1/reviews?limit=1&cursor={first['next_cursor']}", headers=as_user(world.admin)
    ).json()
    assert second["next_cursor"] is None
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_critical_board(client: TestClient, world: World, as_user: Any) -> None:
    board = client.get(
        "/api/v1/findings?severity=critical&severity=high", headers=as_user(world.lead)
    ).json()["items"]
    (item,) = board
    assert (item["title"], item["author"], item["target"]) == ("SQL injection", "An", "MR !7")
    assert item["age_days"] >= 0
    assert client.get("/api/v1/findings", headers=as_user(world.binh)).json()["items"] == []
    forbidden = client.get(
        f"/api/v1/findings?team_id={world.backend.id}", headers=as_user(world.outsider)
    )
    assert forbidden.status_code == 403


def test_rerun_only_failed_or_skipped(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue
) -> None:
    done = client.post(f"/api/v1/reviews/{world.an_review.id}/rerun", headers=as_user(world.lead))
    assert done.status_code == 409
    failed = client.post(
        f"/api/v1/reviews/{world.binh_review.id}/rerun", headers=as_user(world.lead)
    )
    assert failed.status_code == 202
    ((func, args, _),) = queue.jobs
    assert func.endswith("review_commit_job") and args[1] == "b" * 40
    dev = client.post(f"/api/v1/reviews/{world.binh_review.id}/rerun", headers=as_user(world.an))
    assert dev.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/connections"),
        ("patch", "/api/v1/connections/1"),
        ("post", "/api/v1/projects/1/webhook"),
        ("get", "/api/v1/projects"),
        ("patch", "/api/v1/projects/1"),
        ("get", "/api/v1/users"),
        ("get", "/api/v1/identity/unmapped"),
        ("get", "/api/v1/admin/llm-costs"),
    ],
)
def test_admin_endpoints_reject_non_admins(
    client: TestClient, world: World, as_user: Any, method: str, path: str
) -> None:
    response = client.request(method, path, headers=as_user(world.lead), json={})
    assert response.status_code == 403


def test_admin_changes_are_audited_and_secrets_never_returned(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    patched = client.patch(
        f"/api/v1/projects/{world.project.id}", headers=admin, json={"post_mr_comment": True}
    )
    assert patched.json()["post_mr_comment"] is True
    created = client.post(
        "/api/v1/connections",
        headers=admin,
        json={"name": "gl2", "base_url": "https://gl2.example.com", "token": "glpat-123456789"},
    ).json()
    assert "token" not in str(created) and "secret" not in str(created)
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "project.update")).one()
    assert (audit.actor_user_id, audit.details) == (world.admin.id, {"post_mr_comment": True})


def test_rotating_a_connection_token_checks_it_and_audits_without_it(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    connection_id = world.project.connection_id
    path = f"/api/v1/connections/{connection_id}"
    admin = as_user(world.admin)
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        user = router.get("https://gitlab.example.com/api/v4/user")
        user.respond(401)
        rejected = client.patch(path, headers=admin, json={"token": "glpat-wrong-123"})
        user.respond(json={"id": 1})
        accepted = client.patch(path, headers=admin, json={"token": "glpat-rotated-123"})
    assert rejected.status_code == 422 and "glpat" not in rejected.text
    assert accepted.status_code == 200
    connection = db_session.get(Connection, connection_id)
    assert connection is not None and BOX.decrypt(connection.token_encrypted) == "glpat-rotated-123"
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "connection.update")).one()
    assert audit.details == {"fields": ["token"]}


def test_connection_patch_rejects_empty_body_and_keeps_the_host(
    client: TestClient, world: World, as_user: Any
) -> None:
    path = f"/api/v1/connections/{world.project.connection_id}"
    assert client.patch(path, headers=as_user(world.admin), json={}).status_code == 422
    moved = client.patch(
        path, headers=as_user(world.admin), json={"base_url": "https://evil.example.com"}
    )
    assert moved.status_code == 422  # the host never changes: the token stays with its GitLab


def test_register_webhook_from_the_dashboard_is_idempotent(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    hooks: list[dict[str, Any]] = []
    gitlab = "https://gitlab.example.com/api/v4/projects/1001/hooks"

    def add(request: httpx.Request) -> httpx.Response:
        hooks.append({**json.loads(request.content), "id": 7})
        return httpx.Response(201, json=hooks[-1])

    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(gitlab).mock(side_effect=lambda r: httpx.Response(200, json=hooks))
        router.post(gitlab).mock(side_effect=add)
        update = router.put(f"{gitlab}/7").respond(json={})
        path = f"/api/v1/projects/{world.project.id}/webhook"
        first = client.post(path, headers=as_user(world.admin)).json()
        second = client.post(path, headers=as_user(world.admin)).json()

    url = "https://codepulse.example.com/api/v1/webhooks/gitlab"
    assert first == {"url": url, "created": True} and second == {"url": url, "created": False}
    (hook,) = hooks
    connection = db_session.get(Connection, world.project.connection_id)
    assert connection is not None and connection.webhook_secret_encrypted
    assert hook["token"] == BOX.decrypt(connection.webhook_secret_encrypted)
    assert update.call_count == 1


def test_register_webhook_refuses_a_localhost_base_url(
    client: TestClient, world: World, as_user: Any, settings: Settings
) -> None:
    settings.app_base_url = "http://localhost:8000"
    response = client.post(
        f"/api/v1/projects/{world.project.id}/webhook", headers=as_user(world.admin)
    )
    assert response.status_code == 409


@pytest.fixture
def oauth(client: TestClient) -> Any:
    def install(userinfo: dict[str, Any]) -> respx.MockRouter:
        router = respx.mock(assert_all_called=False)
        router.post("https://gitlab.example.com/oauth/token").respond(json={"access_token": "at"})
        router.get("https://gitlab.example.com/oauth/userinfo").respond(json=userinfo)
        app.dependency_overrides[get_oauth_http] = lambda: httpx.Client(
            transport=httpx.MockTransport(router.handler)
        )
        return router

    return install


def test_login_redirects_to_gitlab_with_state(client: TestClient) -> None:
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://gitlab.example.com/oauth/authorize?")
    assert (
        "redirect_uri=https%3A%2F%2Fcodepulse.example.com%2Fapi%2Fv1%2Fauth%2Fcallback"
        in (response.headers["location"])
    )
    assert response.cookies.get(STATE_COOKIE)


def test_callback_signs_in_a_registered_gitlab_user(
    client: TestClient, world: World, db_session: Session, oauth: Any
) -> None:
    db_session.add(Account(kind="gitlab_user", external_key="42", user_id=world.an.id))
    db_session.flush()
    oauth({"sub": "42", "email": "someone@else.com"})
    client.cookies.set(STATE_COOKIE, "st")
    response = client.get("/api/v1/auth/callback?code=c&state=st", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://dash.example.com"
    token = response.cookies.get(SESSION_COOKIE)
    assert token
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["id"] == world.an.id


def test_callback_rejects_bad_state_and_unknown_users(
    client: TestClient, world: World, oauth: Any
) -> None:
    oauth({"sub": "999", "email": "stranger@x.vn"})
    client.cookies.set(STATE_COOKIE, "st")
    assert client.get("/api/v1/auth/callback?code=c&state=other").status_code == 400
    assert client.get("/api/v1/auth/callback?code=c&state=st").status_code == 403


def test_first_sign_in_of_an_admin_email_creates_the_user(
    client: TestClient, settings: Settings, db_session: Session, oauth: Any
) -> None:
    settings.admin_emails = ["boss@x.vn"]
    client.cookies.set(STATE_COOKIE, "st")
    oauth({"sub": "7", "email": "Boss@x.vn", "name": "Boss"})
    unverified = client.get("/api/v1/auth/callback?code=c&state=st", follow_redirects=False)
    assert unverified.status_code == 403  # GitLab must vouch for the email

    oauth({"sub": "7", "email": "Boss@x.vn", "email_verified": True, "name": "Boss"})
    client.cookies.set(STATE_COOKIE, "st")
    response = client.get("/api/v1/auth/callback?code=c&state=st", follow_redirects=False)
    assert response.status_code == 302
    user = db_session.scalars(select(User).where(User.primary_email == "boss@x.vn")).one()
    assert user.full_name == "Boss"
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {response.cookies.get(SESSION_COOKIE)}"},
    ).json()
    assert me["id"] == user.id and me["is_admin"] is True

    oauth({"sub": "8", "email": "dev@x.vn", "email_verified": True})
    client.cookies.set(STATE_COOKIE, "st")
    assert client.get("/api/v1/auth/callback?code=c&state=st").status_code == 403


def test_dashboard_aggregates(client: TestClient, world: World, as_user: Any) -> None:
    lead = as_user(world.lead)
    hot = client.get(
        f"/api/v1/metrics/teams/{world.backend.id}/hotspots?period=week&start={WEEK}", headers=lead
    ).json()
    assert hot == {
        "categories": [{"category": "security", "count": 1}],
        "files": [{"file_path": "app/a.py", "count": 1}],
    }
    board = client.get(
        f"/api/v1/leaderboard?team_id={world.backend.id}&period=week&start={WEEK}", headers=lead
    ).json()
    assert len(board["members"][0]["trend"]) == 8
    assert board["members"][0]["trend"][-1] == "75.00"
    names = [t["name"] for t in client.get("/api/v1/teams/visible", headers=lead).json()]
    assert names == ["Backend"]
    assert (
        client.get(f"/api/v1/people/{world.an.id}", headers=as_user(world.binh)).status_code == 403
    )
    listed = client.get("/api/v1/reviews", headers=as_user(world.an)).json()["items"][0]
    assert (listed["target"], listed["project"]) == ("MR !7", "demo/p")
    heat = client.get(f"/api/v1/metrics/users/{world.an.id}/categories?weeks=52", headers=lead)
    assert heat.status_code == 200
