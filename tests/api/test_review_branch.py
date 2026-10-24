"""One review branch for many repos at once (docs/specs/17 §Nhánh review)."""

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Project
from tests.api.conftest import World

pytestmark = pytest.mark.integration
API = "https://gitlab.example.com/api/v4"


def second_repo(db_session: Session, world: World) -> Project:
    other = Project(
        connection_id=world.project.connection_id, external_id=1002, path_with_namespace="demo/q"
    )
    db_session.add(other)
    db_session.commit()
    return other


def test_bulk_branch_sets_every_repo_and_names_the_ones_without_it(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    other = second_repo(db_session, world)
    ids = [world.project.id, other.id]
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{API}/projects/1001/repository/branches/develop").respond(
            json={"name": "develop"}
        )
        router.get(f"{API}/projects/1002/repository/branches/develop").respond(404)
        body = client.put(
            "/api/v1/projects/review-branch",
            headers=as_user(world.admin),
            json={"project_ids": ids, "review_branch": "develop"},
        ).json()
    assert body == {"updated": 2, "missing": ["demo/q"], "checked": True}
    db_session.expire_all()
    assert {p.review_branch for p in db_session.scalars(select(Project))} == {"develop"}
    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "project.review_branch")
    ).one()
    assert audit.details is not None and audit.details["review_branch"] == "develop"


def test_every_branch_and_default_skip_the_gitlab_check(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    other = second_repo(db_session, world)
    ids = [world.project.id, other.id]
    with respx.mock() as router:  # any GitLab call would fail the test
        everything = client.put(
            "/api/v1/projects/review-branch",
            headers=as_user(world.admin),
            json={"project_ids": ids, "review_branch": "*"},
        ).json()
        back = client.put(
            "/api/v1/projects/review-branch",
            headers=as_user(world.admin),
            json={"project_ids": ids, "review_branch": ""},
        ).json()
        assert not router.calls
    assert everything == {"updated": 2, "missing": [], "checked": False}
    assert back["updated"] == 2
    db_session.expire_all()
    assert {p.review_branch for p in db_session.scalars(select(Project))} == {None}


def test_a_gitlab_error_does_not_block_the_change(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    with respx.mock() as router:
        router.get(f"{API}/projects/1001/repository/branches/main").respond(502)
        body = client.put(
            "/api/v1/projects/review-branch",
            headers=as_user(world.admin),
            json={"project_ids": [world.project.id], "review_branch": "main"},
        ).json()
        assert len(router.calls) == 1  # no retries inside an admin request
    assert body == {"updated": 1, "missing": [], "checked": True}


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"project_ids": [], "review_branch": "main"}, 422),
        ({"project_ids": [1], "review_branch": "two words"}, 422),
        ({"project_ids": [1], "review_branch": "a/../b"}, 422),
        ({"project_ids": [999999], "review_branch": "main"}, 404),
        ({"project_ids": [1], "review_branch": "main", "extra": 1}, 422),
    ],
)
def test_bulk_branch_rejects_bad_input(
    client: TestClient, world: World, as_user: Any, payload: dict[str, Any], status: int
) -> None:
    if payload["project_ids"] == [1]:
        payload = {**payload, "project_ids": [world.project.id]}
    response = client.put(
        "/api/v1/projects/review-branch", headers=as_user(world.admin), json=payload
    )
    assert response.status_code == status


def test_only_admins_change_branches(client: TestClient, world: World, as_user: Any) -> None:
    response = client.put(
        "/api/v1/projects/review-branch",
        headers=as_user(world.lead),
        json={"project_ids": [world.project.id], "review_branch": "main"},
    )
    assert response.status_code == 403


def test_a_slash_in_the_branch_name_is_encoded_for_the_exact_lookup(
    client: TestClient, world: World, as_user: Any
) -> None:
    with respx.mock() as router:
        route = router.get(f"{API}/projects/1001/repository/branches/feature%2Fx").respond(
            json={"name": "feature/x"}
        )
        body = client.put(
            "/api/v1/projects/review-branch",
            headers=as_user(world.admin),
            json={"project_ids": [world.project.id], "review_branch": "feature/x"},
        ).json()
        assert route.called
    assert body == {"updated": 1, "missing": [], "checked": True}


def test_reset_to_default_is_audited(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    client.put(
        "/api/v1/projects/review-branch",
        headers=as_user(world.admin),
        json={"project_ids": [world.project.id], "review_branch": ""},
    )
    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "project.review_branch")
    ).one()
    assert audit.details is not None and audit.details["review_branch"] == "default"
