"""Scheduler service: runs periodic jobs (sync, aggregation, reports)."""

from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from app.collector.sync import enqueue_syncs_job
from app.config import get_settings
from app.logging import configure_logging


def build_scheduler(sync_interval_min: int) -> BlockingScheduler:
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
    return scheduler


def main() -> None:
    configure_logging()
    build_scheduler(get_settings().sync_interval_min).start()


if __name__ == "__main__":
    main()
