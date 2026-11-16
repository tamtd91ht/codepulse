"""docs/specs/21 §10: an admin creates a dự án with no repo, names someone its Lead or Manager,
and that person adds the repos and sets the dự án up (repo settings, .claude repo, rule files,
review rules) without being admin."""

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, BusinessProject, Project, ProjectGrant
from tests.api.conftest import World

pytestmark = pytest.mark.integration

API = "https://gitlab.example.com/api/v4"


def empty_project(client: TestClient, world: World, as_user: Any) -> int:
    res = client.post(
        "/api/v1/business-projects", headers=as_user(world.admin), json={"name": "OmiCRM"}
    )
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


def name_admins(
    client: TestClient, world: World, as_user: Any, bp_id: int, **people: str
) -> list[dict[str, Any]]:
    """Admin names people Manager / Lead of the dự án (keyword: user attribute on World)."""
    grants = [
        {"user_id": getattr(world, who).id, "role": "manage", "admin_role": role}
        for who, role in people.items()
    ]
    res = client.put(
        f"/api/v1/business-projects/{bp_id}/grants",
        headers=as_user(world.admin),
        json={"grants": grants},
    )
    assert res.status_code == 200, res.text
    return list(res.json())


def add_repo(client: TestClient, headers: dict[str, str], bp_id: int, external_id: int) -> Any:
    path = f"vihat/omi/r{external_id}"
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/{path.replace('/', '%2F')}").respond(
            json={"id": external_id, "path_with_namespace": path, "default_branch": "main"}
        )
        return client.post(
            "/api/v1/projects",
            headers=headers,
            json={"repo": f"https://gitlab.example.com/{path}", "business_project_id": bp_id},
        )


def test_admin_names_a_manager_who_then_adds_repos_to_the_empty_du_an(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp_id = empty_project(client, world, as_user)
    rows = name_admins(client, world, as_user, bp_id, an="manager")
    assert [(r["user_id"], r["role"], r["admin_role"]) for r in rows] == [
        (world.an.id, "manage", "manager")
    ]
    an = as_user(world.an)

    me = client.get("/api/v1/auth/me", headers=an).json()
    assert me["project_admin"] == {str(bp_id): "manager"}
    assert me["setup_projects"] == [bp_id]
    assert me["permissions"]["project.setup"] == "project"
    assert (
        client.get("/api/v1/auth/me", headers=as_user(world.admin)).json()["setup_projects"] is None
    )

    # Only their dự án, and only its repos.
    listed = client.get("/api/v1/business-projects", headers=an).json()
    assert [b["id"] for b in listed] == [bp_id]
    assert client.get("/api/v1/projects", headers=an).json() == []
    assert client.get("/api/v1/connections", headers=an).status_code == 200

    res = add_repo(client, an, bp_id, 4001)
    assert res.status_code == 201, res.text
    assert res.json()["business_project_id"] == bp_id
    assert [
        p["path_with_namespace"] for p in client.get("/api/v1/projects", headers=an).json()
    ] == ["vihat/omi/r4001"]
    # Not into a dự án they do not set up, and not without a dự án.
    demo = world.project.business_project_id
    assert add_repo(client, an, demo or 0, 4002).status_code in (403, 404)
    res = client.post("/api/v1/projects", headers=an, json={"repo": "vihat/omi/x"})
    assert res.status_code == 422
    # Bulk import and hard removal stay the admins'.
    assert (
        client.post(
            "/api/v1/projects/import-group",
            headers=an,
            json={"connection_id": world.project.connection_id, "group": "vihat"},
        ).status_code
        == 403
    )
    repo_id = client.get("/api/v1/projects", headers=an).json()[0]["id"]
    assert (
        client.post(
            "/api/v1/projects/remove", headers=an, json={"project_ids": [repo_id], "confirm": "x"}
        ).status_code
        == 403
    )


def test_a_repo_of_another_du_an_is_not_taken_by_link(
    client: TestClient, world: World, as_user: Any
) -> None:
    bp_id = empty_project(client, world, as_user)
    name_admins(client, world, as_user, bp_id, an="lead")
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/{world.project.external_id}").respond(
            json={
                "id": world.project.external_id,
                "path_with_namespace": world.project.path_with_namespace,
                "default_branch": "main",
            }
        )
        res = client.post(
            "/api/v1/projects",
            headers=as_user(world.an),
            json={
                "external_id": world.project.external_id,
                "connection_id": world.project.connection_id,
                "business_project_id": bp_id,
            },
        )
    assert res.status_code == 409, res.text


def test_a_lead_configures_the_repos_of_their_du_an_only(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp_id = empty_project(client, world, as_user)
    name_admins(client, world, as_user, bp_id, an="lead")
    an = as_user(world.an)
    repo_id = add_repo(client, an, bp_id, 4101).json()["id"]

    res = client.patch(
        f"/api/v1/projects/{repo_id}",
        headers=an,
        json={"deep_review": False, "review_branch": "develop"},
    )
    assert res.status_code == 200, res.text
    assert (res.json()["deep_review"], res.json()["review_branch"]) == (False, "develop")
    # Sending code to an external LLM stays an admin's call.
    res = client.patch(f"/api/v1/projects/{repo_id}", headers=an, json={"allow_external_llm": True})
    assert res.status_code == 403
    assert client.post(f"/api/v1/projects/{repo_id}/untrack", headers=an).status_code == 200
    assert client.post(f"/api/v1/projects/{repo_id}/track", headers=an).status_code == 200
    res = client.put(
        "/api/v1/projects/review-branch",
        headers=an,
        json={"project_ids": [repo_id], "review_branch": "*"},
    )
    assert res.status_code == 200, res.text

    # The Demo repo is not theirs: An sees Demo, but does not set it up.
    other = world.project.id
    assert (
        client.patch(
            f"/api/v1/projects/{other}", headers=an, json={"deep_review": False}
        ).status_code
        == 403
    )
    res = client.put(
        "/api/v1/projects/review-branch",
        headers=an,
        json={"project_ids": [repo_id, other], "review_branch": ""},
    )
    assert res.status_code == 403
    # Out of their dự án is fine; into it from another is not.
    res = client.put(
        "/api/v1/projects/business-project",
        headers=an,
        json={"project_ids": [other], "business_project_id": bp_id},
    )
    assert res.status_code == 403
    res = client.put(
        "/api/v1/projects/business-project",
        headers=an,
        json={"project_ids": [repo_id], "business_project_id": None},
    )
    assert res.status_code == 204, res.text
    assert db_session.get(Project, repo_id).business_project_id is None  # type: ignore[union-attr]


def test_review_rules_yes_renaming_no(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp_id = empty_project(client, world, as_user)
    name_admins(client, world, as_user, bp_id, an="lead")
    an = as_user(world.an)
    res = client.patch(
        f"/api/v1/business-projects/{bp_id}",
        headers=an,
        json={"name": "OmiCRM", "description": None, "review_rules": "Không dùng print"},
    )
    assert res.status_code == 200, res.text
    assert db_session.get(BusinessProject, bp_id).review_rules == "Không dùng print"  # type: ignore[union-attr]
    res = client.patch(f"/api/v1/business-projects/{bp_id}", headers=an, json={"name": "Đổi tên"})
    assert res.status_code == 403
    assert client.delete(f"/api/v1/business-projects/{bp_id}", headers=an).status_code == 403


def test_claude_repo_and_rule_files_of_their_du_an(
    client: TestClient, world: World, as_user: Any
) -> None:
    bp_id = empty_project(client, world, as_user)
    name_admins(client, world, as_user, bp_id, an="lead")
    an = as_user(world.an)
    assert (
        client.get(f"/api/v1/business-projects/{bp_id}/rule-files", headers=an).status_code == 200
    )
    assert (
        client.delete(f"/api/v1/business-projects/{bp_id}/claude-repo", headers=an).status_code
        == 204
    )
    demo = world.project.business_project_id
    assert client.get(f"/api/v1/business-projects/{demo}/rule-files", headers=an).status_code == 403
    assert (
        client.delete(f"/api/v1/business-projects/{demo}/claude-repo", headers=an).status_code
        == 403
    )
    # Binh holds nothing: no project.setup at all.
    binh = as_user(world.binh)
    assert (
        client.get(f"/api/v1/business-projects/{bp_id}/rule-files", headers=binh).status_code == 403
    )
    assert (
        client.delete(f"/api/v1/business-projects/{bp_id}/claude-repo", headers=binh).status_code
        == 403
    )
    assert client.get("/api/v1/projects", headers=binh).status_code == 403


def test_a_plain_manage_grant_does_not_set_up_the_du_an(
    client: TestClient, world: World, as_user: Any
) -> None:
    bp_id = empty_project(client, world, as_user)
    res = client.put(
        f"/api/v1/business-projects/{bp_id}/grants",
        headers=as_user(world.admin),
        json={"grants": [{"user_id": world.an.id, "role": "manage"}]},
    )
    assert res.status_code == 200
    an = as_user(world.an)
    assert client.get("/api/v1/projects", headers=an).status_code == 403
    assert add_repo(client, an, bp_id, 4201).status_code == 403
    # A Lead / Manager needs a manage grant.
    res = client.put(
        f"/api/v1/business-projects/{bp_id}/grants",
        headers=as_user(world.admin),
        json={"grants": [{"user_id": world.an.id, "role": "view", "admin_role": "lead"}]},
    )
    assert res.status_code == 422


def test_the_manager_staffs_the_du_an_the_lead_does_not(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp_id = empty_project(client, world, as_user)
    name_admins(client, world, as_user, bp_id, an="manager", lead="lead")
    an, lead = as_user(world.an), as_user(world.lead)
    url = f"/api/v1/business-projects/{bp_id}/grants"
    current = [
        {"user_id": world.an.id, "role": "manage", "admin_role": "manager"},
        {"user_id": world.lead.id, "role": "manage", "admin_role": "lead"},
    ]
    assert client.get(url, headers=lead).status_code == 200
    # The Lead sets up, but does not decide who sees the dự án.
    res = client.put(url, headers=lead, json={"grants": current})
    assert res.status_code == 403

    # The Manager gives a view grant and names a Lead.
    res = client.put(
        url,
        headers=an,
        json={
            "grants": [
                *current,
                {"user_id": world.binh.id, "role": "manage", "admin_role": "lead"},
                {"user_id": world.outsider.id, "role": "view"},
            ]
        },
    )
    assert res.status_code == 200, res.text
    assert db_session.get(ProjectGrant, (bp_id, world.binh.id)).admin_role == "lead"  # type: ignore[union-attr]
    audit = db_session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "business_project.grants")
        .order_by(AuditLog.id.desc())
    ).first()
    assert audit is not None and audit.details["lead"] == sorted([world.lead.id, world.binh.id])

    # Not another Manager, not a plain manage grant, not their own row.
    for extra in (
        {"user_id": world.manager.id, "role": "manage", "admin_role": "manager"},
        {"user_id": world.manager.id, "role": "manage"},
    ):
        res = client.put(url, headers=an, json={"grants": [*current, extra]})
        assert res.status_code == 403, extra
    res = client.put(url, headers=an, json={"grants": current[1:]})
    assert res.status_code == 403


def test_only_the_manager_lists_people_to_add(
    client: TestClient, world: World, as_user: Any
) -> None:
    bp_id = empty_project(client, world, as_user)
    name_admins(client, world, as_user, bp_id, an="manager", lead="lead")
    url = f"/api/v1/business-projects/{bp_id}/grant-candidates"
    res = client.get(url, headers=as_user(world.an))
    assert res.status_code == 200
    assert world.binh.id in {p["id"] for p in res.json()}
    assert client.get(url, headers=as_user(world.lead)).status_code == 403
    assert client.get(url, headers=as_user(world.binh)).status_code == 403


def test_a_repo_outside_every_du_an_is_not_taken_by_link(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """The admins' repos (no dự án) stay theirs: a Lead cannot pull one in by its link."""
    bp_id = empty_project(client, world, as_user)
    name_admins(client, world, as_user, bp_id, an="lead")
    loose = Project(
        connection_id=world.project.connection_id,
        external_id=4301,
        path_with_namespace="vihat/omi/r4301",
        business_project_id=None,
    )
    db_session.add(loose)
    db_session.flush()
    res = add_repo(client, as_user(world.an), bp_id, 4301)
    assert res.status_code == 409, res.text
    db_session.refresh(loose)
    assert loose.business_project_id is None
