"""What a running command shows on the dashboard, kept in Redis, never in the database: its live
log, its final answer, and the "Huỷ" flag. Everything expires with the run's files."""

import json
import logging
from pathlib import Path
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.db import get_redis

log = logging.getLogger(__name__)

LOG_KEY = "cp:cmd:log:{}"
RESULT_KEY = "cp:cmd:result:{}"
CANCEL_KEY = "cp:cmd:cancel:{}"
LOG_MAX = 1000  # entries kept per run (the newest)
QUEUE = "commands"


def run_dir(root: str | Path, run_id: int) -> Path:
    return Path(root) / str(run_id)


class RunStore:
    def __init__(self, redis: Redis, ttl_s: int) -> None:
        self._redis, self._ttl = redis, ttl_s

    # -- worker side: never raises -------------------------------------------------------------

    def append(self, run_id: int, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        key = LOG_KEY.format(run_id)
        try:
            pipe = self._redis.pipeline()
            pipe.rpush(key, *(json.dumps(e, default=str) for e in entries))
            pipe.ltrim(key, -LOG_MAX, -1)
            pipe.expire(key, self._ttl)
            pipe.execute()
        except RedisError as exc:
            log.warning("command log not recorded", extra={"error": exc.__class__.__name__})

    def set_result(self, run_id: int, text: str) -> None:
        try:
            self._redis.set(RESULT_KEY.format(run_id), text, ex=self._ttl)
        except RedisError as exc:
            log.warning("command result not recorded", extra={"error": exc.__class__.__name__})

    def cancelled(self, run_id: int) -> bool:
        try:
            return bool(self._redis.exists(CANCEL_KEY.format(run_id)))
        except RedisError:
            return False

    # -- API side ------------------------------------------------------------------------------

    def cancel(self, run_id: int) -> None:
        self._redis.set(CANCEL_KEY.format(run_id), "1", ex=self._ttl)

    def entries(self, run_id: int, after: int = 0) -> list[dict[str, Any]]:
        """Log entries from index `after` on (the dashboard polls with what it already has)."""
        try:
            raws = self._redis.lrange(LOG_KEY.format(run_id), after, -1)
        except RedisError as exc:
            log.warning("command log not readable", extra={"error": exc.__class__.__name__})
            return []
        out: list[dict[str, Any]] = []
        for raw in raws:
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out

    def result(self, run_id: int) -> str | None:
        try:
            raw = self._redis.get(RESULT_KEY.format(run_id))
        except RedisError:
            return None
        if raw is None:
            return None
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


CLEANUP_JOB = "command-cleanup"


def enqueue_cleanup_job() -> None:
    """Scheduler: queue the hourly clean-up once (a fixed id, so a stopped command worker does
    not pile up copies)."""
    redis = get_redis()
    try:
        if Job.fetch(CLEANUP_JOB, connection=redis).get_status() in ("queued", "started"):
            return  # the last one has not run yet (no command worker?)
    except NoSuchJobError:
        pass
    Queue(QUEUE, connection=redis).enqueue(
        "app.commands.jobs.cleanup_runs_job", job_id=CLEANUP_JOB, job_timeout=600
    )
