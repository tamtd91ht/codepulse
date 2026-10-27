"""The review worker's settings-derived limits (docs/specs/19 §4.6)."""

from datetime import timedelta

from app.config import Settings
from app.review.service import STALE_RUNNING, VERIFY_TIMEOUT_S
from app.webhook.jobs import review_job_timeout as enqueued_with
from app.workers.review_jobs import STALE_GRACE_S, review_job_timeout, review_settings


def test_job_timeout_follows_the_review_and_verification_settings() -> None:
    defaults = Settings()
    assert review_job_timeout(defaults) == 480 * 3 + 12 * VERIFY_TIMEOUT_S + 600 == 3480
    assert timedelta(seconds=3480 + STALE_GRACE_S) == STALE_RUNNING  # the module default
    patient = Settings(review_timeout_s=900, verify_max_findings=20)
    assert review_job_timeout(patient) == 900 * 3 + 20 * 120 + 600
    assert enqueued_with is review_job_timeout  # the enqueue sites use the same function


def test_stale_takeover_waits_past_the_job_timeout() -> None:
    for settings in (Settings(), Settings(review_timeout_s=3600, verify_max_findings=100)):
        stale = review_settings(settings).stale_running
        assert stale == timedelta(seconds=review_job_timeout(settings) + STALE_GRACE_S)
        assert stale >= timedelta(seconds=review_job_timeout(settings)) + timedelta(minutes=5)
