"""Dependency checks shared by the API /healthz endpoint and the worker container healthcheck."""

import socket
from datetime import UTC, datetime, timedelta

from redis import Redis
from rq import Worker
from rq.worker import BaseWorker
from sqlalchemy import Engine, text

WORKER_HEARTBEAT_MAX_AGE = timedelta(seconds=90)


def check_database(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


def check_redis(redis: Redis) -> bool:
    try:
        return bool(redis.ping())
    except Exception:
        return False


def live_workers(redis: Redis, now: datetime | None = None) -> list[BaseWorker]:
    """Workers whose RQ heartbeat is recent; a crashed worker stays registered until it expires."""
    cutoff = (now or datetime.now(UTC)) - WORKER_HEARTBEAT_MAX_AGE
    return [
        w
        for w in Worker.all(connection=redis)
        if w.last_heartbeat is not None and w.last_heartbeat.replace(tzinfo=UTC) >= cutoff
    ]


def local_worker_alive(redis: Redis, hostname: str | None = None) -> bool:
    host = hostname or socket.gethostname()
    return any(w.hostname == host for w in live_workers(redis))
