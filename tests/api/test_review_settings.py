from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditLog
from app.workers.review_jobs import review_settings
from tests.api.conftest import World


def _loc(body: list[dict[str, Any]]) -> dict[str, Any]:
    return next(item for item in body if item["key"] == "max_review_loc")


@pytest.mark.parametrize("who", ["lead", "manager", "an"])
def test_review_settings_are_admin_only(
    client: TestClient, world: World, as_user: Any, who: str
) -> None:
    headers = as_user(getattr(world, who))
    assert client.get("/api/v1/admin/review-settings", headers=headers).status_code == 403
    response = client.put(
        "/api/v1/admin/review-settings", headers=headers, json={"max_review_loc": 5000}
    )
    assert response.status_code == 403


def test_max_review_loc_overrides_the_environment_and_reaches_the_worker(
    client: TestClient, world: World, as_user: Any, settings: Settings, db_session: Session
) -> None:
    admin = as_user(world.admin)
    initial = _loc(client.get("/api/v1/admin/review-settings", headers=admin).json())
    assert initial == {
        "key": "max_review_loc",
        "env": "MAX_REVIEW_LOC",
        "value": settings.max_review_loc,
        "default": settings.max_review_loc,
        "overridden": False,
        "minimum": 1,
        "maximum": 1_000_000,
    }

    response = client.put(
        "/api/v1/admin/review-settings", headers=admin, json={"max_review_loc": 5000}
    )
    assert response.status_code == 200
    assert _loc(response.json())["value"] == 5000 and _loc(response.json())["overridden"]
    assert review_settings(settings, db_session).max_review_loc == 5000

    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "settings.update")).one()
    assert audit.target == "setting:max_review_loc"
    assert audit.details == {"old": settings.max_review_loc, "new": 5000}

    # null restores the environment default.
    response = client.put(
        "/api/v1/admin/review-settings", headers=admin, json={"max_review_loc": None}
    )
    assert _loc(response.json())["overridden"] is False
    assert review_settings(settings, db_session).max_review_loc == settings.max_review_loc


@pytest.mark.parametrize("body", [{"max_review_loc": 0}, {"max_review_loc": "x"}, {"other": 1}])
def test_bad_values_are_refused(
    client: TestClient, world: World, as_user: Any, body: dict[str, Any]
) -> None:
    response = client.put("/api/v1/admin/review-settings", headers=as_user(world.admin), json=body)
    assert response.status_code == 422
