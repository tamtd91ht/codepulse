"""A review's page says whose change it is (user 2026-11-13: small, beside the commit id)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.conftest import World
from tests.api.test_attribution import unmapped_work

pytestmark = pytest.mark.integration


def test_detail_names_the_person(client: TestClient, world: World, as_user: Any) -> None:
    detail = client.get(f"/api/v1/reviews/{world.an_review.id}", headers=as_user(world.an)).json()
    assert detail["author"]["id"] == world.an.id
    assert detail["author"]["full_name"] == world.an.full_name
    assert detail["author_account"] is None


def test_detail_shows_the_git_identity_when_no_person_is_linked(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _, _, commit_review, mr_review = unmapped_work(db_session, world)
    admin = as_user(world.admin)
    commit = client.get(f"/api/v1/reviews/{commit_review.id}", headers=admin).json()
    assert commit["author"] is None
    account = commit["author_account"]
    assert (account["kind"], account["email"], account["can_link"]) == (
        "git_email",
        "an.home@gmail.com",
        False,  # linking stays on the list
    )
    mr = client.get(f"/api/v1/reviews/{mr_review.id}", headers=admin).json()
    assert (mr["author_account"]["kind"], mr["author_account"]["name"]) == (
        "gitlab_user",
        "an.home",
    )
