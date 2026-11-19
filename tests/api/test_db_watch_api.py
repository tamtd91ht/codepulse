"""Admin > Hệ thống (app/dbwatch.py): the open transactions and the bell's threshold."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import World

pytestmark = pytest.mark.integration


def test_admins_see_open_transactions_and_set_the_threshold(
    client: TestClient, world: World, as_user: Any
) -> None:
    admin = as_user(world.admin)
    res = client.get("/api/v1/admin/db/transactions?min_s=0", headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["threshold_s"] == 120
    assert isinstance(res.json()["items"], list)
    assert (
        client.get("/api/v1/admin/db/transactions", headers=as_user(world.lead)).status_code == 403
    )

    saved = client.put(
        "/api/v1/admin/review-settings",
        json={"long_tx_alert_s": 30, "llm_account_retry_min": 20},
        headers=admin,
    )
    assert saved.status_code == 200, saved.text
    values = {s["key"]: s["value"] for s in saved.json()}
    assert values["long_tx_alert_s"] == 30 and values["llm_account_retry_min"] == 20
    assert client.get("/api/v1/admin/db/transactions", headers=admin).json()["threshold_s"] == 30
    too_big = client.put(
        "/api/v1/admin/review-settings", json={"long_tx_alert_s": 100_000}, headers=admin
    )
    assert too_big.status_code == 422
