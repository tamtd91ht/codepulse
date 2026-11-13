"""A review's detail says whose change it is (user 2026-11-13: shown beside the commit id)."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.conftest import World


def test_detail_names_the_author(client: TestClient, world: World, as_user: Any) -> None:
    detail = client.get(f"/api/v1/reviews/{world.an_review.id}", headers=as_user(world.an)).json()
    assert detail["author"]["id"] == world.an.id
    assert detail["author"]["full_name"] == world.an.full_name
    assert detail["author_account"] is None


def test_an_author_with_no_person_shows_the_git_identity(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    world.an_review.author_user_id = None
    db_session.commit()
    detail = client.get(
        f"/api/v1/reviews/{world.an_review.id}", headers=as_user(world.admin)
    ).json()
    assert detail["author"] is None
    # The change's synced author account, when there is one; never an error without it.
    account = detail["author_account"]
    assert account is None or account["can_link"] is False
