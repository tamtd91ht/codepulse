from fastapi.testclient import TestClient

from app.api.main import app
from app.scheduler.__main__ import build_scheduler


def test_api_app_serves_openapi() -> None:
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "CodePulse"


def test_scheduler_builds_without_jobs() -> None:
    assert build_scheduler().get_jobs() == []
