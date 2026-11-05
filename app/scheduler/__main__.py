"""Scheduler service: runs periodic jobs (sync, aggregation, reports)."""

from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from app.collector.sync import enqueue_syncs_job
from app.commands.store import enqueue_cleanup_job
from app.config import get_settings
from app.dpi.jobs import dpi_lock_job
from app.evidence.jobs import create_drafts_job
from app.insights.backfill import backfill_once_job
from app.jira.settings import jira_configured
from app.logging import configure_logging
from app.reporting.jobs import send_reports_job
from app.retention import purge_job
from app.review.llm_recovery import TICK_MIN
from app.scoring.aggregate import aggregate_job
from app.telegram.poller import telegram_tick_job
from app.workers.llm_jobs import llm_watch_job


def enqueue_dpi_month_job() -> None:
    """The month's scorecards run on a worker (long: every person's facts)."""
    from rq import Queue

    from app.db import get_redis

    Queue("default", connection=get_redis()).enqueue(
        "app.dpi.jobs.dpi_month_job", job_timeout=60 * 60
    )


def enqueue_jira_sync_job() -> None:
    """docs/specs/27 §3: the Jira sync runs on a worker. The fixed job id does not stop a second
    run while one is slow (RQ enqueues it again); the per-project advisory lock in
    `app.jira.sync.sync_project` does: a project being synced is skipped."""
    from rq import Queue

    from app.db import get_redis
    from app.jira.sync import JOB_ID, JOB_TIMEOUT

    Queue("default", connection=get_redis()).enqueue(
        "app.jira.sync.sync_all_job", job_id=JOB_ID, job_timeout=JOB_TIMEOUT
    )


def enqueue_jira_keys_backfill_job() -> None:
    """docs/specs/27 §8: after the upgrade, the Jira keys of MRs converted before it, once (the
    job itself returns at once when it already ran)."""
    from rq import Queue

    from app.db import get_redis
    from app.jira.backfill import JOB_ID, JOB_TIMEOUT

    Queue("default", connection=get_redis()).enqueue(
        "app.jira.backfill.backfill_keys_once_job", job_id=JOB_ID, job_timeout=JOB_TIMEOUT
    )


def build_scheduler(
    sync_interval_min: int,
    report_tz: str = "UTC",
    telegram_poll_interval_s: int = 5,
    jira_sync_interval_min: int | None = None,
) -> BlockingScheduler:
    """`jira_sync_interval_min`: None when Jira is not configured (no Jira job)."""
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
    # docs/specs/20 §6.3: last month's evidence-pack drafts on the 1st, after the night's syncs.
    scheduler.add_job(
        create_drafts_job, "cron", day="1-3", hour=1, minute=30, id="evidence-drafts", **common
    )
    # docs/specs/25 §5: last month's DPI scorecards on the 1st (after the syncs and the
    # evidence drafts), and finalized months locking a week later.
    scheduler.add_job(
        enqueue_dpi_month_job, "cron", day=1, hour=2, minute=30, id="dpi-month", **common
    )
    scheduler.add_job(dpi_lock_job, "cron", hour=4, minute=0, id="dpi-lock", **common)
    # A down LLM is probed when its probe is due (LLM_PROBE_INTERVAL_MIN); the tick is cheap.
    scheduler.add_job(
        llm_watch_job,
        "interval",
        minutes=TICK_MIN,
        id="llm-watch",
        max_instances=1,
        coalesce=True,
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
    if jira_sync_interval_min is not None:
        scheduler.add_job(
            enqueue_jira_sync_job,
            "interval",
            minutes=jira_sync_interval_min,
            id="jira-sync",
            max_instances=1,
            coalesce=True,
        )
    # docs/specs/20 (9.12): after the upgrade, fill the Insights data of existing repos once.
    scheduler.add_job(
        backfill_once_job,
        "date",
        run_date=datetime.now(UTC) + timedelta(minutes=1),
        id="insights-backfill-once",
        misfire_grace_time=3600,
    )
    # docs/specs/27 §8: the Jira keys of MRs converted before the Jira integration, once.
    scheduler.add_job(
        enqueue_jira_keys_backfill_job,
        "date",
        run_date=datetime.now(UTC) + timedelta(minutes=2),
        id="jira-keys-backfill-once",
        misfire_grace_time=3600,
    )
    return scheduler


def main() -> None:
    configure_logging()
    settings = get_settings()
    build_scheduler(
        settings.sync_interval_min,
        settings.report_tz,
        settings.telegram_poll_interval_s,
        settings.jira_sync_interval_min if jira_configured(settings) else None,
    ).start()


if __name__ == "__main__":
    main()
