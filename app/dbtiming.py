"""Every database transaction of the app timed where it runs (user 2026-11-19): a SQLAlchemy hook
notes when a transaction begins and how long each statement takes (only the slowest is kept); at
commit or rollback, one longer than the threshold (LONG_TX_ALERT_S, Admin > Hệ thống) is logged
and handed to a background thread that queues it for the admins' bell (app/dbwatch.py
`report_slow_job`) with the flow (app/dblabel.py), the line of app code that ended it and its
slowest statement.

Rules, so the hook can never hurt a query:
- the hooks touch no network and no database: the threshold is a cached number the background
  thread refreshes from Redis, and a slow transaction is only put on an in-process queue (the
  commit / rollback events fire before the DBAPI commit, with the transaction's locks held);
- the timing lives in a weak map keyed by the `Connection` object, never in `Connection.info`
  (which raises on an invalidated connection);
- every hook swallows its own errors.
A fast transaction costs two clock reads and a comparison per statement. Transactions that never
end (hung, a worker killed) are the minute watch's (app/dbwatch.py `check`). `how` is how the app
ended the transaction (a commit the server then refused is still "commit").
"""

import logging
import os
import queue
import sys
import threading
import time
import weakref
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, event

log = logging.getLogger(__name__)

SQL_CHARS = 300
THRESHOLD_KEY = "codepulse:dbwatch:threshold"
THRESHOLD_REFRESH_S = 60.0
REDIS_TIMEOUT_S = 1.0
QUEUE_MAX = 200  # a burst beyond this is logged, not queued
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_SKIP = (os.path.join(_APP_DIR, "dbtiming.py"), os.path.join(_APP_DIR, "db.py"))

_timing: "weakref.WeakKeyDictionary[Any, dict[str, Any]]" = weakref.WeakKeyDictionary()

# Where a slow transaction goes; default: the background sender. Tests swap it for a list.
Reporter = Callable[[dict[str, Any]], None]


def _redis() -> Any:
    """A Redis client of its own with short timeouts: a Redis that does not answer must not
    hold the sender (nor anything else) for long."""
    from redis import Redis

    from app.config import get_settings

    return Redis.from_url(
        str(get_settings().redis_url),
        socket_timeout=REDIS_TIMEOUT_S,
        socket_connect_timeout=REDIS_TIMEOUT_S,
    )


def _env_threshold() -> float:
    try:
        from app.config import get_settings

        return float(get_settings().long_tx_alert_s)
    except Exception:
        return 0.0


class _Threshold:
    """LONG_TX_ALERT_S as the minute watch last published it in Redis (an admin may change it on
    the web), else the environment value. Read by the hooks from memory only; refreshed by the
    sender thread."""

    def __init__(self) -> None:
        self.value: float | None = None

    def get(self) -> float:
        if self.value is None:
            self.value = _env_threshold()
        return self.value

    def refresh(self, client: Any) -> None:
        try:
            raw = client.get(THRESHOLD_KEY)
            self.value = float(raw) if raw is not None else _env_threshold()
        except Exception:
            log.debug("threshold not refreshed", exc_info=True)


threshold = _Threshold()


class _Sender:
    """One daemon thread per process (started on first use, again after a fork: an RQ work horse
    is a fork) that queues the slow transactions as RQ jobs and refreshes the threshold."""

    def __init__(self) -> None:
        self._pid: int | None = None
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=QUEUE_MAX)
        self._lock = threading.Lock()

    def ensure(self) -> None:
        if self._pid == os.getpid():
            return
        with self._lock:
            if self._pid == os.getpid():
                return
            self._queue = queue.Queue(maxsize=QUEUE_MAX)
            threading.Thread(target=self._run, name="dbtiming", daemon=True).start()
            self._pid = os.getpid()

    def put(self, payload: dict[str, Any]) -> None:
        self.ensure()
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            log.warning("slow transaction not queued: sender busy", extra=payload)

    def _run(self) -> None:
        client: Any = None
        refreshed = 0.0
        while True:
            try:
                payload: dict[str, Any] | None = self._queue.get(timeout=THRESHOLD_REFRESH_S)
            except queue.Empty:
                payload = None
            try:
                if client is None:
                    client = _redis()
                if time.monotonic() - refreshed >= THRESHOLD_REFRESH_S:
                    threshold.refresh(client)
                    refreshed = time.monotonic()
                if payload is not None:
                    from rq import Queue

                    Queue("default", connection=client).enqueue(
                        "app.dbwatch.report_slow_job", payload, job_timeout=60, result_ttl=0
                    )
            except Exception:
                log.warning("slow transaction not reported", exc_info=True)
                client = None


_sender = _Sender()
reporter: Reporter = _sender.put


def _caller() -> str | None:
    """The innermost frame of app code outside this module and app/db.py: the line that
    committed or rolled back (a `with session.begin():` block ends on its own line)."""
    frame: Any = sys._getframe(1)
    while frame is not None:
        path = frame.f_code.co_filename
        if path.startswith(_APP_DIR) and path not in _SKIP:
            rel = os.path.relpath(path, os.path.dirname(_APP_DIR)).replace(os.sep, "/")
            return f"{rel}:{frame.f_lineno} {frame.f_code.co_name}"
        frame = frame.f_back
    return None


def _pid(conn: Any) -> int | None:
    try:
        return int(conn.connection.dbapi_connection.info.backend_pid)
    except Exception:
        return None


def _on_begin(conn: Any) -> None:
    try:
        # An autocommit connection (the sync's repo lock) holds no database transaction, only
        # SQLAlchemy's logical one for as long as it is open: nothing to time.
        if conn.get_execution_options().get("isolation_level") == "AUTOCOMMIT":
            _timing.pop(conn, None)
            return
        _timing[conn] = {
            "t0": time.monotonic(),
            "wall": time.time(),
            "count": 0,
            "slowest": 0.0,
            "sql": None,
        }
    except Exception:
        log.debug("transaction not timed", exc_info=True)


def _before(conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool) -> None:
    try:
        state = _timing.get(conn)
        if state is not None:
            state["q0"] = time.monotonic()
    except Exception:
        log.debug("statement not timed", exc_info=True)


def _after(conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool) -> None:
    try:
        state = _timing.get(conn)
        if state is None or "q0" not in state:
            return
        spent = time.monotonic() - state.pop("q0")
        state["count"] += 1
        if spent > state["slowest"]:
            state["slowest"] = spent
            state["sql"] = statement
    except Exception:
        log.debug("statement not timed", exc_info=True)


def _end(conn: Any, how: str) -> None:
    try:
        state = _timing.pop(conn, None)
        if state is None:
            return
        seconds = time.monotonic() - state["t0"]
        limit = threshold.get()
        if limit <= 0 or seconds <= limit:
            return
        from app import dblabel

        sql = " ".join(str(state["sql"] or "").split())[:SQL_CHARS] or None
        payload = {
            "seconds": round(seconds, 1),
            "started": state["wall"],
            "ended": time.time(),
            "how": how,
            "flow": dblabel.current_name(),
            "where": _caller(),
            "pid": _pid(conn),
            "statements": state["count"],
            "slowest_s": round(state["slowest"], 1),
            "slowest_sql": sql,
        }
        log.warning("slow database transaction", extra=payload)
        reporter(payload)
    except Exception:
        log.debug("slow transaction not reported", exc_info=True)


def _on_commit(conn: Any) -> None:
    _end(conn, "commit")


def _on_rollback(conn: Any) -> None:
    _end(conn, "rollback")


def install(engine: Engine) -> None:
    for name, fn in (
        ("begin", _on_begin),
        ("commit", _on_commit),
        ("rollback", _on_rollback),
        ("before_cursor_execute", _before),
        ("after_cursor_execute", _after),
    ):
        if not event.contains(engine, name, fn):
            event.listen(engine, name, fn)
