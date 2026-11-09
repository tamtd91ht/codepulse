from fastapi.testclient import TestClient

from app.api.main import app
from app.scheduler.__main__ import build_scheduler


def test_api_app_serves_openapi() -> None:
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "CodePulse"


def test_scheduler_enqueues_syncs_on_the_configured_interval() -> None:
    jobs = {
        job.id: job
        for job in build_scheduler(30, "Asia/Ho_Chi_Minh", telegram_poll_interval_s=7).get_jobs()
    }
    assert set(jobs) == {
        "enqueue-syncs",
        "aggregate-week",
        "aggregate-month",
        "report-week",
        "report-month",
        "retention-purge",
        "telegram-tick",
        "command-cleanup",
        "evidence-drafts",
        "insights-backfill-once",
        "jira-keys-backfill-once",
        "dpi-month",
        "dpi-lock",
        "llm-watch",
        "jira-tick",
        "stetho-tag",
    }
    assert str(jobs["evidence-drafts"].trigger.timezone) == "Asia/Ho_Chi_Minh"
    assert jobs["command-cleanup"].trigger.interval.total_seconds() == 3600
    assert jobs["enqueue-syncs"].trigger.interval.total_seconds() == 30 * 60
    assert jobs["telegram-tick"].trigger.interval.total_seconds() == 7
    assert str(jobs["aggregate-week"].trigger.timezone) == "Asia/Ho_Chi_Minh"
    assert jobs["aggregate-month"].args == ("month",)


def test_scheduler_ticks_the_jira_sync_every_minute() -> None:
    """docs/specs/27 §3: the connection can change on the web, so a tick decides each minute."""
    jobs = {job.id: job for job in build_scheduler(30).get_jobs()}
    assert "jira-sync" not in jobs
    assert jobs["jira-tick"].trigger.interval.total_seconds() == 60
