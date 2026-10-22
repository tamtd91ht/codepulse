"""Scheduler service: runs periodic jobs (sync, aggregation, reports)."""

from apscheduler.schedulers.blocking import BlockingScheduler


def build_scheduler() -> BlockingScheduler:
    return BlockingScheduler(timezone="UTC")


def main() -> None:
    build_scheduler().start()


if __name__ == "__main__":
    main()
