"""Scheduler service: runs periodic jobs (sync, aggregation, reports)."""

from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from app.collector.sync import enqueue_syncs_job
from app.config import get_settings
from app.logging import configure_logging
from app.reporting.jobs import send_reports_job
from app.scoring.aggregate import aggregate_job


def build_scheduler(sync_interval_min: int, report_tz: str = "UTC") -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        enqueue_syncs_job,
        "interval",
        minutes=sync_interval_min,
        id="enqueue-syncs",
        next_run_time=datetime.now(UTC),  # sync once at start-up, then on the interval
        max_instances=1,
        coalesce=True,
    )
    # docs/specs/08 §5: last week every Monday 00:30, last month on the 1st at 00:30 (REPORT_TZ).
    common = {
        "timezone": report_tz,
        "max_instances": 1,
        "coalesce": True,
        "misfire_grace_time": 3600,
    }
    scheduler.add_job(
        aggregate_job,
        "cron",
        args=["week"],
        day_of_week="mon",
        hour=0,
        minute=30,
        id="aggregate-week",
        **common,
    )
    scheduler.add_job(
        aggregate_job,
        "cron",
        args=["month"],
        day=1,
        hour=0,
        minute=30,
        id="aggregate-month",
        **common,
    )
    # docs/specs/10: weekly report Monday 08:00, monthly report on the 1st (after aggregation).
    scheduler.add_job(
        send_reports_job,
        "cron",
        args=["week"],
        day_of_week="mon",
        hour=8,
        minute=0,
        id="report-week",
        **common,
    )
    scheduler.add_job(
        send_reports_job,
        "cron",
        args=["month"],
        day=1,
        hour=8,
        minute=0,
        id="report-month",
        **common,
    )
    return scheduler


def main() -> None:
    configure_logging()
    settings = get_settings()
    build_scheduler(settings.sync_interval_min, settings.report_tz).start()


if __name__ == "__main__":
    main()
