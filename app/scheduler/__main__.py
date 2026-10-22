"""Scheduler service: runs periodic jobs (sync, aggregation, reports)."""

from apscheduler.schedulers.blocking import BlockingScheduler

from app.logging import configure_logging


def build_scheduler() -> BlockingScheduler:
    return BlockingScheduler(timezone="UTC")


def main() -> None:
    configure_logging()
    build_scheduler().start()


if __name__ == "__main__":
    main()
