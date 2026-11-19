"""docs/specs/21 §13 (P2): the rights a dự án's own set gives hold over that dự án only, while
its holder still has a grant there, and never widen what they hold elsewhere."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.access import whole_projects
from app.api.security import load_principal
from app.config import Settings
from app.models import (
    BusinessProject,
    PermissionSet,
    PermissionSetItem,
    ProjectGrant,
    UserPermissionSet,
)
from tests.api.conftest import World, grant

pytestmark = pytest.mark.integration


def project_set(
    session: Session, bp_id: int, user_id: int, *keys: str, scope: str = "project"
) -> int:
    row = PermissionSet(name=f"Bộ {bp_id}-{len(keys)}", business_project_id=bp_id)
    session.add(row)
    session.flush()
    session.add_all(PermissionSetItem(set_id=row.id, permission=k, scope=scope) for k in keys)
    session.add(UserPermissionSet(user_id=user_id, set_id=row.id))
    session.flush()
    return row.id


def other_project(session: Session, world: World) -> int:
    other = BusinessProject(name="Other")
    session.add(other)
    session.flush()
    grant(session, world.binh, other.id)
    return other.id


def test_a_project_set_holds_in_its_project_only(
    db_session: Session, world: World, settings: Settings
) -> None:
    demo = world.project.business_project_id
    assert demo is not None
    other = other_project(db_session, world)
    project_set(db_session, demo, world.binh.id, "review.view", "dispute.decide", "tasks.edit")

    p = load_principal(db_session, world.binh.id, settings)
    assert p.project_perms == {demo: frozenset({"review.view", "dispute.decide", "tasks.edit"})}
    # Over everyone's work in Demo; not in Other; the global scope is unchanged.
    assert p.covers("review.view", world.an.id, demo)
    assert not p.covers("review.view", world.an.id, other)
    assert p.covers("dispute.decide", world.an.id, demo)
    assert not p.covers("dispute.decide", world.an.id, other)
    assert p.covers_project("tasks.edit", demo) and not p.covers_project("tasks.edit", other)
    assert p.scope("review.view") == "own" and p.scope("dispute.decide") is None
    assert p.can("dispute.decide")
    assert whole_projects(p, "review.view") == {demo}


def test_rights_go_with_the_grant_and_off_catalog_items_are_dropped(
    db_session: Session, world: World, settings: Settings
) -> None:
    demo = world.project.business_project_id
    assert demo is not None
    # admin.people is never a dự án's right: dropped even if a row says so.
    project_set(db_session, demo, world.binh.id, "review.view", "admin.people", "insights.view")
    p = load_principal(db_session, world.binh.id, settings)
    assert p.project_perms == {demo: frozenset({"review.view"})}
    assert not p.can("admin.people")
    db_session.execute(
        delete(ProjectGrant).where(
            ProjectGrant.business_project_id == demo, ProjectGrant.user_id == world.binh.id
        )
    )
    db_session.flush()
    assert load_principal(db_session, world.binh.id, settings).project_perms == {}


def test_project_sets_never_count_across_the_system(
    db_session: Session, world: World, settings: Settings
) -> None:
    demo = world.project.business_project_id
    assert demo is not None
    other = other_project(db_session, world)
    project_set(db_session, demo, world.binh.id, "review.rerun", scope="all")
    p = load_principal(db_session, world.binh.id, settings)
    assert p.perms.get("review.rerun") is None
    assert p.covers("review.rerun", None, demo) and not p.covers("review.rerun", None, other)


def test_api_review_detail_follows_the_project_set(
    client: TestClient, db_session: Session, world: World, as_user: Any
) -> None:
    demo = world.project.business_project_id
    assert demo is not None
    url = f"/api/v1/reviews/{world.an_review.id}"
    assert client.get(url, headers=as_user(world.binh)).status_code in (403, 404)
    project_set(db_session, demo, world.binh.id, "review.view")
    assert client.get(url, headers=as_user(world.binh)).status_code == 200
