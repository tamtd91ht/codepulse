"""Live Board / task detail (docs/specs/31 §21.7): what changed in a task space, pushed to the
browsers that watch it.

Nothing calls this module: a flush listener notes every task that is created, changed, moved or
deleted, and every comment, git link, worklog, attachment, link or watcher added to / removed
from one, whoever does it (a person, a git rule, automation, the importer). Once the outermost
transaction commits, one `{type: "task", key, changed}` message per task goes out on Redis
(CHANNEL); every API process fans it out to the open streams of that dự án
(app/api/routers/task_live.py). A rolled-back transaction publishes nothing. Redis being down
loses the live push only: the browser still reloads on its own actions and on reconnect.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, SessionTransaction, UOWTransaction

from app.models import (
    Task,
    TaskAttachment,
    TaskComment,
    TaskGitLink,
    TaskLink,
    TaskWatcher,
    TaskWorklog,
)

log = logging.getLogger(__name__)

CHANNEL = "codepulse:tasks"
PENDING = "task_live_pending"
SAVED = "task_live_saved"
ENDED = "task_live_ended"
MANY = 50
QUEUE_SIZE = 200
RETRY_S = 3.0
# After a failed publish, wait this long before trying Redis again: a Redis that is down must
# not add a connect timeout to every request that changes a task.
PAUSE_S = 30.0
# Bookkeeping columns: a change of only these is not news to a Board.
QUIET = frozenset({"updated_at", "created_at"})
# Rows hanging off a task, and the word the browser gets for them.
CHILDREN: dict[type[Any], str] = {
    TaskComment: "comment",
    TaskGitLink: "git",
    TaskWorklog: "worklog",
    TaskAttachment: "attachment",
    TaskWatcher: "watchers",
}

# -- noting and publishing ------------------------------------------------------------------------


def _pending(session: Session) -> dict[tuple[int, str], set[str]]:
    found: dict[tuple[int, str], set[str]] = session.info.setdefault(PENDING, {})
    return found


def _task_changes(task: Task, state: str) -> set[str]:
    if state != "dirty":
        return {state}
    insp = inspect(task)
    changed = {
        attr.key
        for attr in insp.mapper.column_attrs
        if attr.key not in QUIET and insp.attrs[attr.key].history.has_changes()
    }
    return changed


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _flush: UOWTransaction) -> None:
    """Runs with the flush's new / dirty / deleted lists and attribute histories still there."""
    tasks: list[tuple[Task, set[str]]] = []
    children: dict[int, set[str]] = {}
    for state, objects in (
        ("created", session.new),
        ("dirty", session.dirty),
        ("deleted", session.deleted),
    ):
        for obj in objects:
            if isinstance(obj, Task):
                changed = _task_changes(obj, state)
                if changed:
                    tasks.append((obj, changed))
            elif isinstance(obj, TaskLink):
                for tid in (obj.from_task_id, obj.to_task_id):
                    if tid is not None:
                        children.setdefault(tid, set()).add("links")
            else:
                word = CHILDREN.get(type(obj))
                task_id = getattr(obj, "task_id", None)
                if word is not None and task_id is not None:
                    children.setdefault(task_id, set()).add(word)
    if not tasks and not children:
        return
    found = _pending(session)
    known: dict[int, tuple[int, str]] = {}
    for task, changed in tasks:
        if task.key is None or task.business_project_id is None:
            continue
        where = (task.business_project_id, task.key)
        found.setdefault(where, set()).update(changed)
        if task.id is not None:
            known[task.id] = where
    missing = [tid for tid in children if tid not in known]
    if missing:
        # One small read inside the flush (autoflush is off while flushing).
        for tid, bp_id, key in session.execute(
            select(Task.id, Task.business_project_id, Task.key).where(Task.id.in_(missing))
        ):
            known[tid] = (bp_id, key)
    for tid, words in children.items():
        if tid in known:
            found.setdefault(known[tid], set()).update(words)


def as_messages(found: dict[tuple[int, str], set[str]]) -> list[dict[str, Any]]:
    """One message per task; more than MANY tasks of one dự án in one commit (a bulk edit, the
    rank respread of a drag, an import) become one `{type: "space"}`: the Board reloads once."""
    per_space: dict[int, list[tuple[str, set[str]]]] = {}
    for (bp_id, key), changed in sorted(found.items()):
        per_space.setdefault(bp_id, []).append((key, changed))
    out: list[dict[str, Any]] = []
    for bp_id, items in per_space.items():
        if len(items) > MANY:
            words = sorted({w for _, changed in items for w in changed})
            out.append({"type": "space", "bp": bp_id, "key": "*", "changed": words})
            continue
        out += [
            {"type": "task", "bp": bp_id, "key": key, "changed": sorted(changed)}
            for key, changed in items
        ]
    return out


_client: Any = None
_paused_until = 0.0


def _publish_redis(messages: list[dict[str, Any]]) -> None:
    """A client of its own with short timeouts (as app/notifications/inbox.py): the work has
    committed already, a Redis that does not answer must not hold the request."""
    global _client
    if _client is None:
        from redis import Redis

        from app.config import get_settings

        _client = Redis.from_url(
            str(get_settings().redis_url), socket_connect_timeout=2, socket_timeout=2
        )
    pipe = _client.pipeline(transaction=False)
    for message in messages:
        pipe.publish(CHANNEL, json.dumps(message, default=str))
    pipe.execute()


# Swapped in tests; never raises into the caller (the work is already committed).
publisher: Callable[[list[dict[str, Any]]], None] = _publish_redis


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    global _paused_until
    # Also fired when a SAVEPOINT is released: only the outermost commit publishes.
    if session.in_nested_transaction():
        return
    found: dict[tuple[int, str], set[str]] = session.info.pop(PENDING, {})
    if not found or time.monotonic() < _paused_until:
        return
    try:
        publisher(as_messages(found))
    except Exception:
        _paused_until = time.monotonic() + PAUSE_S
        log.warning("live task change not published", exc_info=True)


def _copy(found: dict[tuple[int, str], set[str]] | None) -> dict[tuple[int, str], set[str]]:
    return {k: set(v) for k, v in (found or {}).items()}


@event.listens_for(Session, "after_transaction_create")
def _after_transaction_create(session: Session, transaction: SessionTransaction) -> None:
    """A SAVEPOINT begins: what is noted now is what its rollback goes back to (as
    app/audit_trail.py)."""
    if transaction.nested:
        saved: dict[SessionTransaction, Any] = session.info.setdefault(SAVED, {})
        saved[transaction] = _copy(session.info.get(PENDING))


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    """The outermost transaction ended without a commit (rolled back): nothing to say. A SAVEPOINT
    ended: its snapshot is kept aside for the `after_soft_rollback` that follows a rollback."""
    if transaction.parent is None and not transaction.nested:
        session.info.pop(PENDING, None)
        session.info.pop(SAVED, None)
        session.info.pop(ENDED, None)
        return
    saved = session.info.get(SAVED)
    snapshot = saved.pop(transaction, None) if saved else None
    if snapshot is not None:
        session.info[ENDED] = (transaction, snapshot)
    else:
        session.info.pop(ENDED, None)


@event.listens_for(Session, "after_soft_rollback")
def _after_soft_rollback(session: Session, previous: SessionTransaction) -> None:
    """A SAVEPOINT rolled back: what was noted inside it did not happen."""
    ended = session.info.pop(ENDED, None)
    if previous.nested and ended is not None and ended[0] is previous:
        session.info[PENDING] = ended[1]


# -- fan-out to the open streams of one API process -----------------------------------------------


class Hub:
    """One Redis subscription per process, fanned out per dự án. A stream is a bounded queue: a
    tab that stops reading loses live messages (it reloads everything on reconnect)."""

    def __init__(self) -> None:
        self._streams: dict[int, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._task: asyncio.Task[None] | None = None

    def open(self, bp_id: int) -> "asyncio.Queue[dict[str, Any]]":
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(QUEUE_SIZE)
        self._streams.setdefault(bp_id, set()).add(queue)
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._listen())
        return queue

    def close(self, bp_id: int, queue: "asyncio.Queue[dict[str, Any]]") -> None:
        streams = self._streams.get(bp_id)
        if streams is None:
            return
        streams.discard(queue)
        if not streams:
            del self._streams[bp_id]

    def deliver(self, message: dict[str, Any]) -> None:
        bp_id = message.get("bp")
        for queue in list(self._streams.get(bp_id, ())) if isinstance(bp_id, int) else []:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(message)

    async def _listen(self) -> None:
        from redis.asyncio import Redis

        from app.config import get_settings

        while self._streams:
            client = Redis.from_url(str(get_settings().redis_url))
            try:
                async with client.pubsub() as pubsub:
                    await pubsub.subscribe(CHANNEL)
                    while self._streams:
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=5.0
                        )
                        if message and message.get("type") == "message":
                            try:
                                self.deliver(json.loads(message["data"]))
                            except (ValueError, TypeError):
                                log.warning("bad task live message")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("task live subscription lost; retrying", exc_info=True)
                await asyncio.sleep(RETRY_S)
            finally:
                await client.aclose()


hub = Hub()


def sse(message: dict[str, Any]) -> str:
    """One Server-Sent Event, without the dự án id (the stream is already one dự án's)."""
    data = json.dumps(
        {k: v for k, v in message.items() if k != "bp"}, ensure_ascii=False, default=str
    )
    return f"event: task\ndata: {data}\n\n"
