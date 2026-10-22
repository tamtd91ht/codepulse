from fastapi.testclient import TestClient

from app.api.main import app
from app.scheduler.__main__ import build_scheduler


def test_api_app_serves_openapi() -> None:
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "CodePulse"


def test_scheduler_enqueues_syncs_on_the_configured_interval() -> None:
    jobs = {job.id: job for job in build_scheduler(30, "Asia/Ho_Chi_Minh").get_jobs()}
    assert set(jobs) == {"enqueue-syncs", "aggregate-week", "aggregate-month"}
    assert jobs["enqueue-syncs"].trigger.interval.total_seconds() == 30 * 60
    assert str(jobs["aggregate-week"].trigger.timezone) == "Asia/Ho_Chi_Minh"
    assert jobs["aggregate-month"].args == ("month",)
