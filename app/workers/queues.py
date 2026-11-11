"""Which RQ queue a job goes to (docs/specs/13 "Hàng đợi review").

Reviews have queues of their own, served by the review workers (`review-worker`,
`REVIEW_WORKERS` of them, each running one review at a time):

- `reviews_asked`: what one member asked for and waits on (user 2026-11-09): `/review` from
  Telegram, "Chạy lại" of a review, the AI check of a close request (web or Claude Code through
  MCP) and of a dispute; a worker always takes these first;
- `reviews`: the rest: GitLab webhooks, backfills, criteria re-scoring, and the batches the
  "Yêu cầu review" page sends after scanning commit lists (not a member's own request).

Within a queue it is first come first served: a review asked for later never jumps ahead of one
asked for earlier (the old `at_front` did: the last request ran first). Syncs, knowledge builds,
notifications and other short jobs stay on `default`, whose worker also takes reviews when it
has nothing else to do, so a review never waits behind a sync.
"""

from typing import Any

from rq import Queue, Worker

from app.collector.sync import Enqueuer

DEFAULT_QUEUE = "default"
REVIEW_QUEUE = "reviews"
ASKED_REVIEW_QUEUE = "reviews_asked"
# Review triggers of a member's own request: their reviews go to ASKED_REVIEW_QUEUE ("dashboard"
# is the scanning "Yêu cầu review" page: not one of them).
ASKED_TRIGGERS = frozenset({"telegram", "rerun", "mcp"})


def review_queue(queue: Enqueuer, *, asked: bool = False) -> Enqueuer:
    """The reviews queue (`asked`: the one served first) on the same Redis as `queue` (tests
    pass a fake: it is used as is)."""
    if isinstance(queue, Queue):
        name = ASKED_REVIEW_QUEUE if asked else REVIEW_QUEUE
        return Queue(name, connection=queue.connection)
    return queue


def worker_queues(names: list[str]) -> list[str]:
    """The queues a worker listens on, in the order it serves them: whoever serves `reviews`
    serves the asked-for reviews first."""
    out: list[str] = []
    for name in names:
        if name == REVIEW_QUEUE and ASKED_REVIEW_QUEUE not in out:
            out.append(ASKED_REVIEW_QUEUE)
        if name not in out:
            out.append(name)
    return out


def review_backlog(queue: Enqueuer, *, asked: bool = False) -> tuple[int, int] | None:
    """(reviews waiting ahead of a new one, workers that take reviews), or None when it cannot
    be told (a fake queue, Redis trouble): a caller then says nothing rather than something
    wrong. An asked-for review only waits behind the other asked-for ones."""
    target = review_queue(queue)
    if not isinstance(target, Queue):
        return None
    try:
        workers = Worker.count(queue=target)
        first = Queue(ASKED_REVIEW_QUEUE, connection=target.connection)
        waiting = len(first) + (0 if asked else len(target))
        return waiting, workers
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
    "ASKED_REVIEW_QUEUE",
    "ASKED_TRIGGERS",
    "DEFAULT_QUEUE",
    "REVIEW_QUEUE",
    "backlog_note",
    "review_backlog",
    "review_queue",
    "worker_queues",
]
