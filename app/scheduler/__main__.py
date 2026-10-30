"""Scheduler service: runs periodic jobs (sync, aggregation, reports)."""

from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from app.collector.sync import enqueue_syncs_job
from app.commands.store import enqueue_cleanup_job
from app.config import get_settings
from app.logging import configure_logging
from app.reporting.jobs import send_reports_job
from app.retention import purge_job
from app.scoring.aggregate import aggregate_job
from app.telegram.poller import telegram_tick_job


def build_scheduler(
    sync_interval_min: int, report_tz: str = "UTC", telegram_poll_interval_s: int = 5
) -> BlockingScheduler:
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
    # docs/specs/11: data retention, daily at night.
    scheduler.add_job(purge_job, "cron", hour=3, minute=0, id="retention-purge", **common)
    # Command runs (app/commands): files nobody downloaded, runs whose worker died. Runs on the
    # command worker, the one service besides the api that mounts COMMAND_RUNS_DIR.
    scheduler.add_job(
        enqueue_cleanup_job,
        "interval",
        hours=1,
        id="command-cleanup",
        max_instances=1,
        coalesce=True,
    )
    # Telegram review bots: short polling; the job returns at once when no bot is enabled.
    scheduler.add_job(
        telegram_tick_job,
        "interval",
        seconds=telegram_poll_interval_s,
        id="telegram-tick",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def main() -> None:
    configure_logging()
    settings = get_settings()
    build_scheduler(
        settings.sync_interval_min, settings.report_tz, settings.telegram_poll_interval_s
    ).start()


if __name__ == "__main__":
    main()
