"""Which RQ queue a job goes to (docs/specs/13 "Hàng đợi review").

Reviews have a queue of their own, `reviews`, served first come first served by the review
workers (`review-worker`, `REVIEW_WORKERS` of them, each running one review at a time). Syncs,
knowledge builds, notifications and other short jobs stay on `default`, whose worker also takes
reviews when it has nothing else to do. A review therefore never waits behind a sync, and a
review asked for later never jumps ahead of one asked for earlier (the old `at_front` did: the
last request ran first).
"""

from typing import Any

from rq import Queue, Worker

from app.collector.sync import Enqueuer

DEFAULT_QUEUE = "default"
REVIEW_QUEUE = "reviews"


def review_queue(queue: Enqueuer) -> Enqueuer:
    """The reviews queue on the same Redis as `queue` (tests pass a fake: it is used as is)."""
    if isinstance(queue, Queue):
        return Queue(REVIEW_QUEUE, connection=queue.connection)
    return queue


def review_backlog(queue: Enqueuer) -> tuple[int, int] | None:
    """(reviews waiting, workers that take reviews), or None when it cannot be told (a fake
    queue, Redis trouble): a caller then says nothing rather than something wrong."""
    target = review_queue(queue)
    if not isinstance(target, Queue):
        return None
    try:
        workers = Worker.count(queue=target)
        return len(target), workers
    except Exception:
        return None


def backlog_note(backlog: tuple[int, int] | None, queued: int) -> str:
    """One sentence for the person who just asked: how many reviews are ahead and how many run
    at once. `queued`: how many of the waiting ones are theirs (at the end of the line)."""
    if backlog is None:
        return ""
    waiting, workers = backlog
    ahead = max(waiting - queued, 0)
    if workers <= 0:
        return "Chưa có worker review nào đang chạy; review sẽ bắt đầu khi worker lên lại."
    if ahead == 0:
        return f"Không có review nào chờ trước; {workers} worker review chạy song song."
    return f"Có {ahead} review đang chờ trước; {workers} worker review chạy song song."


__all__: list[Any] = [
    "DEFAULT_QUEUE",
    "REVIEW_QUEUE",
    "backlog_note",
    "review_backlog",
    "review_queue",
]
