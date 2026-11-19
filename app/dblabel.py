"""Who each database connection works for, as Postgres shows it (`pg_stat_activity.
application_name`): "cp-<process> <what>", e.g. "cp-worker sync.sync_project_job #12" or
"cp-api POST /api/v1/reviews/5/rerun". The long-transaction watch (app/dbwatch.py) and anyone
running `SELECT application_name, ... FROM pg_stat_activity` then see which flow holds a
transaction (user 2026-11-19).

Set at connection checkout from a context variable: the API middleware labels each request, the
worker each job; the process name (api, worker, scheduler) is set once at start. A label that
cannot be set never fails the checkout.
"""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import Any

from sqlalchemy import Engine, event

log = logging.getLogger(__name__)

PREFIX = "cp-"
MAX_LEN = 63  # Postgres truncates application_name to NAMEDATALEN - 1
_process = ["app"]
_label: ContextVar[str | None] = ContextVar("db_label", default=None)


def set_process(name: str) -> None:
    """The process kind: "api", "worker", "scheduler", "cli"."""
    _process[0] = name


@contextmanager
def labelled(label: str | None) -> Iterator[None]:
    """Connections checked out inside this block carry `label`."""
    token = _label.set(label)
    try:
        yield
    finally:
        _label.reset(token)


def set_label(label: str | None) -> Any:
    """For callers that cannot use a `with` block; returns the token for `reset_label`."""
    return _label.set(label)


def reset_label(token: Any) -> None:
    _label.reset(token)


def current_name() -> str:
    label = _label.get()
    name = f"{PREFIX}{_process[0]}" + (f" {label}" if label else "")
    return "".join(ch if ch.isprintable() and ord(ch) < 128 else "?" for ch in name)[:MAX_LEN]


_ID_SEGMENT = re.compile(r"/\d+(?=/|$)")


def route_label(method: str, path: str) -> str:
    """A request as a label: ids in the path become ":id", so every call of one route reads
    the same (and a pooled connection keeps its label from one call to the next)."""
    return f"{method} {_ID_SEGMENT.sub('/:id', path)}"


def job_label(func_name: str | None, args: tuple[Any, ...] = ()) -> str:
    """An RQ job as a short label: the last two parts of its function, and its first argument
    when that is an id (`sync.sync_project_job #12`)."""
    parts = (func_name or "job").split(".")
    label = ".".join(parts[-2:])
    if args and isinstance(args[0], int) and not isinstance(args[0], bool):
        label += f" #{args[0]}"
    return label


def _on_checkout(dbapi_connection: Any, record: Any, proxy: Any) -> None:
    name = current_name()
    if record.info.get("application_name") == name:
        return
    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT set_config('application_name', %s, false)", (name,))
        finally:
            cursor.close()
        dbapi_connection.commit()  # end the implicit transaction: the setting stays
        record.info["application_name"] = name
    except Exception:  # pragma: no cover - a label is never worth a failed checkout
        log.debug("application_name not set", exc_info=True)
        with suppress(Exception):  # the pool's reset handles a connection still broken
            dbapi_connection.rollback()


def install(engine: Engine) -> None:
    if not event.contains(engine, "checkout", _on_checkout):
        event.listen(engine, "checkout", _on_checkout)
