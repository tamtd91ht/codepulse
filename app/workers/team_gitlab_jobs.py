"""Teams kept in sync with their GitLab group, every hour (docs/specs/31 §20.7, R2)."""

import logging
from typing import Any

log = logging.getLogger(__name__)

SYNC_INTERVAL_MIN = 60
JOB_ID = "team-gitlab-sync"
JOB_TIMEOUT = 15 * 60


def enqueue_team_gitlab_sync_job() -> None:
    """Scheduler entry point: the GitLab calls run on a worker, not in the scheduler. The fixed
    job id keeps one queued run at a time."""
    from rq import Queue

    from app.db import get_redis

    Queue("default", connection=get_redis()).enqueue(
        "app.workers.team_gitlab_jobs.team_gitlab_sync_job",
        job_id=JOB_ID,
        job_timeout=JOB_TIMEOUT,
    )


def team_gitlab_sync_job() -> dict[str, Any]:
    """RQ entry point: each linked team in its own transaction, so one failing team (GitLab
    down, a group gone) does not hold the others back."""
    from app.collector.sync import client_factory
    from app.crypto import secret_box
    from app.db import get_sessionmaker
    from app.teams.gitlab_sync import linked_team_ids, sync_team

    make_client = client_factory(secret_box())
    totals: dict[str, Any] = {"teams": 0, "added": 0, "ended": 0, "unmapped": 0, "failed": 0}
    with get_sessionmaker()() as session:
        team_ids = linked_team_ids(session)
    for team_id in team_ids:
        try:
            with get_sessionmaker()() as session:
                outcome = sync_team(session, team_id, make_client)
                session.commit()
        except Exception:
            log.exception("team gitlab sync crashed", extra={"team_id": team_id})
            totals["failed"] += 1
            continue
        if outcome is None:
            continue
        totals["teams"] += 1
        totals["added"] += len(outcome.added)
        totals["ended"] += len(outcome.ended)
        totals["unmapped"] += len(outcome.unmapped)
        totals["failed"] += outcome.error is not None
    return totals
