"""Reviews have a queue of their own (app/workers/queues.py)."""

from typing import Any

import pytest
from rq import Queue

from app.workers import queues
from app.workers.queues import REVIEW_QUEUE, backlog_note, review_backlog, review_queue


class Fake:
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> None:
        return None


def test_review_queue_is_the_reviews_queue_on_the_same_redis() -> None:
    sentinel: Any = object()
    q = Queue("default", connection=sentinel)
    target = review_queue(q)
    assert isinstance(target, Queue) and target.name == REVIEW_QUEUE
    assert target.connection is sentinel
    fake = Fake()
    assert review_queue(fake) is fake  # tests keep their fake


def test_backlog(monkeypatch: pytest.MonkeyPatch) -> None:
    assert review_backlog(Fake()) is None
    sentinel: Any = object()
    monkeypatch.setattr(queues.Worker, "count", staticmethod(lambda queue: 3))
    sizes = {"reviews_asked": 2, "reviews": 7}
    monkeypatch.setattr(Queue, "__len__", lambda self: sizes[self.name])
    # An automatic review waits behind both queues; a member's request only behind the asked
    # ones, which workers take first (user 2026-11-09).
    assert review_backlog(Queue("default", connection=sentinel)) == (9, 3)
    assert review_backlog(Queue("default", connection=sentinel), asked=True) == (2, 3)

    def broken(queue: Any) -> int:
        raise ConnectionError("redis down")

    monkeypatch.setattr(queues.Worker, "count", staticmethod(broken))
    assert review_backlog(Queue("default", connection=sentinel)) is None


def test_backlog_note() -> None:
    assert backlog_note(None, 2) == ""
    assert "Chưa có worker review" in backlog_note((5, 0), 2)
    assert backlog_note((2, 3), 2).startswith("Không có review nào chờ trước; 3 worker")
    assert backlog_note((9, 2), 3) == "Có 6 review đang chờ trước; 2 worker review chạy song song."
