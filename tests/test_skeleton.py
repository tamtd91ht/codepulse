from fastapi.testclient import TestClient

from app.api.main import app
from app.scheduler.__main__ import build_scheduler


def test_api_app_serves_openapi() -> None:
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "CodePulse"


def test_scheduler_enqueues_syncs_on_the_configured_interval() -> None:
    (job,) = build_scheduler(sync_interval_min=30).get_jobs()
    assert job.id == "enqueue-syncs"
    assert job.trigger.interval.total_seconds() == 30 * 60
