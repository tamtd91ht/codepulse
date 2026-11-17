"""docs/specs/31 §20.7 (R2): a team kept in sync with a GitLab group, GitLab mocked."""

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.models import Account, AuditLog, Connection, Team, TeamMember
from app.teams.gitlab_sync import normalize_group_path, sync_team
from tests.api.conftest import BOX, World

pytestmark = pytest.mark.integration

API = "/api/v1"
GITLAB = "https://gitlab.example.com/api/v4"
GROUP_URL = f"{GITLAB}/groups/omicx%2Fqa/members"


def member(gitlab_id: int, username: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": gitlab_id,
        "username": username,
        "name": username.title(),
        "state": "active",
        "access_level": 30,
        **extra,
    }


AN, BINH, LEAD = member(11, "an"), member(12, "binh"), member(16, "lead")
STRANGER = member(13, "newdev")
SKIPPED = [
    member(14, "renovate-bot"),
    member(15, "gone", state="blocked"),
    member(17, "invited", membership_state="awaiting"),
    member(18, "minimal", access_level=5),
]


@pytest.fixture
def qa(db_session: Session, world: World) -> Team:
    team = Team(name="QA", gitlab_group_path="omicx/qa")
    db_session.add(team)
    db_session.add_all(
        Account(kind="gitlab_user", external_key=str(gid), user_id=user.id)
        for gid, user in ((11, world.an), (12, world.binh), (16, world.lead))
    )
    db_session.flush()
    # The outsider was put in QA by hand long ago: the sync never ends that row.
    db_session.add(
        TeamMember(team_id=team.id, user_id=world.outsider.id, joined_at=date(2025, 1, 1))
    )
    db_session.flush()
    return team


def today() -> date:
    return datetime.now(UTC).date()


def rows(db: Session, team: Team) -> dict[int, TeamMember]:
    """The current row of each person in the team."""
    db.expire_all()
    found = db.scalars(select(TeamMember).where(TeamMember.team_id == team.id))
    return {r.user_id: r for r in found if r.left_at is None or r.left_at > today()}


def sync(client: TestClient, team: Team, headers: dict[str, str]) -> httpx.Response:
    return client.post(f"{API}/teams/{team.id}/gitlab/sync", headers=headers)


def gitlab(router: respx.MockRouter, members: list[dict[str, Any]]) -> respx.Route:
    router.route(host="testserver").pass_through()
    return router.get(GROUP_URL).respond(json=members)


def test_group_path_is_normalized() -> None:
    assert normalize_group_path(" /omicx/qa/ ") == "omicx/qa"
    assert normalize_group_path("https://gitlab.x/omicx/qa") == "omicx/qa"
    assert normalize_group_path("https://gitlab.x/groups/omicx/qa/-/group_members") == "omicx/qa"
    for bad in ("", "/", "omicx qa", "a//b", "x" * 300):
        with pytest.raises(ValueError):
            normalize_group_path(bad)


def test_only_admins_link_a_group(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    team = world.mobile
    url = f"{API}/teams/{team.id}/gitlab"
    for who in (world.an, world.lead, world.manager):
        res = client.put(url, json={"group_path": "omicx/qa"}, headers=as_user(who))
        assert res.status_code == 403
    admin = as_user(world.admin)
    res = client.put(url, json={"group_path": "not a path"}, headers=admin)
    assert res.status_code == 422
    res = client.put(url, json={"group_path": "https://gitlab.example.com/omicx/qa"}, headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["gitlab_group_path"] == "omicx/qa"
    listed = client.get(f"{API}/teams", headers=admin).json()
    assert next(t for t in listed if t["id"] == team.id)["gitlab_group_path"] == "omicx/qa"
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "team.gitlab_group")).one()
    res = client.put(url, json={"group_path": None}, headers=admin)
    assert res.json()["gitlab_group_path"] is None
    # A team without a group has nothing to sync.
    assert sync(client, team, admin).status_code == 409


def test_sync_adds_mapped_members_and_lists_the_rest(
    client: TestClient, world: World, qa: Team, as_user: Any, db_session: Session
) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = gitlab(router, [AN, BINH, STRANGER, *SKIPPED])
        res = sync(client, qa, as_user(world.admin))
        assert route.called
    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(p["user_id"] for p in body["added"]) == sorted([world.an.id, world.binh.id])
    assert body["ended"] == [] and body["error"] is None
    assert body["unmapped"] == [{"gitlab_id": 13, "username": "newdev", "name": "Newdev"}]
    assert body["skipped"] == len(SKIPPED)
    assert body["team"]["gitlab_sync_at"] is not None
    current = rows(db_session, qa)
    assert set(current) == {world.an.id, world.binh.id, world.outsider.id}
    an = current[world.an.id]
    assert (an.source, an.role, an.joined_at) == ("gitlab", "member", today())
    assert current[world.outsider.id].source == "manual"
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "team.gitlab_sync")).one()
    assert audit.actor_user_id == world.admin.id
    # The member list says where each row came from.
    listed = client.get(f"{API}/teams/{qa.id}/members", headers=as_user(world.admin)).json()
    assert {m["user_id"]: m["source"] for m in listed}[world.an.id] == "gitlab"
    # Nothing changed in GitLab: a second run changes nothing and writes no audit row.
    with respx.mock(assert_all_called=False) as router:
        gitlab(router, [AN, BINH, STRANGER, *SKIPPED])
        again = sync(client, qa, as_user(world.admin)).json()
    assert again["added"] == [] and again["ended"] == []
    assert (
        len(db_session.scalars(select(AuditLog).where(AuditLog.action == "team.gitlab_sync")).all())
        == 1
    )


def test_sync_ends_only_its_own_member_rows(
    client: TestClient, world: World, qa: Team, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    with respx.mock(assert_all_called=False) as router:
        gitlab(router, [AN, BINH, LEAD])
        assert sync(client, qa, admin).status_code == 200
    # The lead's row came from GitLab, then an admin made them the team's lead.
    lead_row = rows(db_session, qa)[world.lead.id]
    lead_row.role = "lead"
    db_session.flush()
    # Everyone left the group in GitLab.
    with respx.mock(assert_all_called=False) as router:
        gitlab(router, [])
        body = sync(client, qa, admin).json()
    assert sorted(p["user_id"] for p in body["ended"]) == sorted([world.an.id, world.binh.id])
    current = rows(db_session, qa)
    # The hand-added outsider and the lead stay.
    assert set(current) == {world.outsider.id, world.lead.id}
    # Back in the group the same day: the row that ended today opens again.
    with respx.mock(assert_all_called=False) as router:
        gitlab(router, [AN])
        body = sync(client, qa, admin).json()
    assert [p["user_id"] for p in body["added"]] == [world.an.id]
    an_rows = db_session.scalars(
        select(TeamMember).where(TeamMember.team_id == qa.id, TeamMember.user_id == world.an.id)
    ).all()
    assert len(an_rows) == 1 and an_rows[0].left_at is None


def test_a_gitlab_failure_changes_no_member(
    client: TestClient, world: World, qa: Team, as_user: Any, db_session: Session
) -> None:
    db_session.add(
        TeamMember(team_id=qa.id, user_id=world.an.id, joined_at=date(2025, 1, 1), source="gitlab")
    )
    db_session.flush()
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(GROUP_URL).respond(404)
        body = sync(client, qa, as_user(world.admin)).json()
    assert body["error"] and "omicx/qa" in body["error"]
    assert body["added"] == [] and body["ended"] == []
    assert body["team"]["gitlab_sync_error"] == body["error"]
    assert world.an.id in rows(db_session, qa)


def test_the_teams_permission_sets_follow_the_group(
    client: TestClient, world: World, qa: Team, as_user: Any, db_session: Session
) -> None:
    """The point of R2: the tester role of a QA team reaches whoever is in the GitLab group."""
    admin = as_user(world.admin)
    res = client.patch(f"{API}/teams/{qa.id}", headers=admin, json={"is_qa": True})
    assert res.status_code == 200, res.text
    assert res.json()["is_qa"] is True

    def held(user: Any) -> dict[str, str]:
        res = client.get(f"{API}/admin/users/{user.id}/permissions", headers=admin).json()
        return {p["key"]: p["scope"] for p in res["permissions"]}

    assert "tests.edit" not in held(world.an)
    with respx.mock(assert_all_called=False) as router:
        gitlab(router, [AN])
        assert sync(client, qa, admin).status_code == 200
    assert held(world.an)["tests.edit"] == "all"
    with respx.mock(assert_all_called=False) as router:
        gitlab(router, [])
        assert sync(client, qa, admin).status_code == 200
    assert "tests.edit" not in held(world.an)


def test_the_job_reads_through_the_connection_that_sees_the_group(
    world: World, qa: Team, db_session: Session
) -> None:
    """A GitLab that does not know the group (404) is passed over for the next one, and the
    scheduled run (no actor) adds the members."""
    other = Connection(
        name="other", base_url="https://other.example.com", token_encrypted=BOX.encrypt("o")
    )
    db_session.add(other)
    db_session.flush()

    def make_client(connection: Connection) -> GitLabClient:
        return GitLabClient(
            connection.base_url, BOX.decrypt(connection.token_encrypted), sleep=lambda _: None
        )

    with respx.mock(assert_all_called=False) as router:
        router.get(GROUP_URL).respond(404)
        router.get("https://other.example.com/api/v4/groups/omicx%2Fqa/members").respond(
            json=[BINH]
        )
        outcome = sync_team(db_session, qa.id, make_client)
    assert outcome is not None and outcome.added == [world.binh.id] and outcome.error is None
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "team.gitlab_sync")).one()
    assert audit.actor_user_id is None
