"""Live push of in-app notifications to the browsers of one API process (docs/specs/24).

One Redis subscription per process (CHANNEL, published by app/notifications/inbox.py after the
event's transaction commits) fans each message out to the open streams of its recipient. A
stream is a bounded queue: a browser that stops reading loses live messages, never the rows.
"""

import asyncio
import contextlib
import json
import logging
from typing import Any

from app.notifications.inbox import CHANNEL

log = logging.getLogger(__name__)

QUEUE_SIZE = 100
RETRY_S = 3.0


class Hub:
    def __init__(self) -> None:
        self._streams: dict[int, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._task: asyncio.Task[None] | None = None

    def open(self, user_id: int) -> "asyncio.Queue[dict[str, Any]]":
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(QUEUE_SIZE)
        self._streams.setdefault(user_id, set()).add(queue)
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._listen())
        return queue

    def close(self, user_id: int, queue: "asyncio.Queue[dict[str, Any]]") -> None:
        streams = self._streams.get(user_id)
        if streams is None:
            return
        streams.discard(queue)
        if not streams:
            del self._streams[user_id]

    def deliver(self, message: dict[str, Any]) -> None:
        user_id = message.get("user_id")
        for queue in list(self._streams.get(user_id, ())) if isinstance(user_id, int) else []:
            with contextlib.suppress(asyncio.QueueFull):  # a stalled tab drops live messages
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
                                log.warning("bad notification message")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("notification subscription lost; retrying", exc_info=True)
                await asyncio.sleep(RETRY_S)
            finally:
                await client.aclose()


hub = Hub()


def sse(message: dict[str, Any]) -> str:
    """One Server-Sent Event: the id lets a reconnecting browser ask for what it missed."""
    data = json.dumps(message, ensure_ascii=False, default=str)
    return f"id: {message['id']}\nevent: notification\ndata: {data}\n\n"
