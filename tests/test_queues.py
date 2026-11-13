"""Reviews have a queue of their own (app/workers/queues.py)."""

from typing import Any

import pytest
from rq import Queue, Worker

from app.workers import queues
from app.workers.queues import (
    backlog_note,
    lane_names,
    lane_of,
    review_backlog,
    review_queue,
    review_queues,
    rotate,
    worker_queues,
)


class Fake:
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> None:
        return None


def test_review_queue_is_the_lane_of_the_du_an_on_the_same_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(queues, "lane_count", lambda: 4)
    sentinel: Any = object()
    q = Queue("default", connection=sentinel)
    target = review_queue(q, bp_id=2)
    assert isinstance(target, Queue) and target.name == "reviews:2"
    assert target.connection is sentinel
    asked = review_queue(q, asked=True, bp_id=2)
    assert isinstance(asked, Queue) and asked.name == "reviews_asked:2"
    none = review_queue(q)
    assert isinstance(none, Queue) and none.name == "reviews:0"  # no dự án: lane 0
    fake = Fake()
    assert review_queue(fake, bp_id=2) is fake  # tests keep their fake


def test_lanes_one_per_du_an_up_to_the_count() -> None:
    # Dự án 1..M each get a lane of their own; M+1 shares lane 1 again; none -> lane 0.
    assert [lane_of(bp, 4) for bp in (1, 2, 3, 4)] == [1, 2, 3, 4]
    assert lane_of(5, 4) == 1 and lane_of(None, 4) == 0 and lane_of(0, 4) == 0
    assert lane_names("reviews", 2) == ["reviews:0", "reviews:1", "reviews:2", "reviews"]


def test_worker_serves_every_asked_lane_first_then_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(queues, "lane_count", lambda: 2)
    assert worker_queues(["default", "reviews"]) == [
        "default",
        "reviews_asked:0",
        "reviews_asked:1",
        "reviews_asked:2",
        "reviews_asked",  # queued before the split: still served
        "reviews:0",
        "reviews:1",
        "reviews:2",
        "reviews",
    ]
    assert worker_queues(["commands"]) == ["commands"]


def test_rotate_takes_the_lanes_in_turn_within_a_tier() -> None:
    order = ["default", "reviews_asked:1", "reviews_asked:2", "reviews:1", "reviews:2", "reviews"]
    same = lambda q: q  # noqa: E731
    # After a job of lane 1, the other lanes of its tier come first; tiers keep their place.
    assert rotate(order, "reviews:1", same) == [
        "default",
        "reviews_asked:1",
        "reviews_asked:2",
        "reviews:2",
        "reviews",
        "reviews:1",
    ]
    assert rotate(order, "reviews_asked:1", same)[1:3] == ["reviews_asked:2", "reviews_asked:1"]
    assert rotate(order, "default", same) == order  # a tier of one: unchanged


def test_a_bulk_du_an_does_not_hold_up_another() -> None:
    """User 2026-11-13: dự án A queues 200 reviews, dự án B one; B's runs next, not 201st."""
    from collections import deque

    lanes = {"reviews:1": deque(f"A{i}" for i in range(200)), "reviews:2": deque()}
    order = ["reviews:1", "reviews:2"]
    same = lambda q: q  # noqa: E731
    taken: list[str] = []

    def take() -> None:
        nonlocal order
        for name in order:
            if lanes[name]:
                taken.append(lanes[name].popleft())
                order = rotate(order, name, same)
                return

    take()
    take()  # two of A's run
    lanes["reviews:2"].append("B0")  # then B asks once
    take()
    assert taken == ["A0", "A1", "B0"]


def test_backlog_counts_the_lane_of_the_du_an(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queues, "lane_count", lambda: 4)
    assert review_backlog(Fake()) is None
    sentinel: Any = object()
    monkeypatch.setattr(Worker, "count", staticmethod(lambda queue: 3))
    sizes = {"reviews_asked:2": 2, "reviews:2": 7, "reviews_asked:1": 50, "reviews:1": 500}
    monkeypatch.setattr(Queue, "__len__", lambda self: sizes.get(self.name, 0))
    # Another dự án's backlog (lane 1) is not ahead: lanes are taken in turn. An automatic
    # review waits behind both tiers of its lane; a member's request only behind the asked one.
    assert review_backlog(Queue("default", connection=sentinel), bp_id=2) == (9, 3)
    assert review_backlog(Queue("default", connection=sentinel), asked=True, bp_id=2) == (2, 3)

    def broken(queue: Any) -> int:
        raise ConnectionError("redis down")

    monkeypatch.setattr(Worker, "count", staticmethod(broken))
    assert review_backlog(Queue("default", connection=sentinel)) is None


def test_readers_see_every_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queues, "lane_count", lambda: 1)
    sentinel: Any = object()
    names = [q.name for q in review_queues(Queue("default", connection=sentinel), asked=False)]  # type: ignore[attr-defined]
    assert names == ["reviews:0", "reviews:1", "reviews"]
    fake = Fake()
    assert review_queues(fake, asked=True) == [fake]


def test_backlog_note() -> None:
    assert backlog_note(None, 2) == ""
    assert "Chưa có worker review" in backlog_note((5, 0), 2)
    assert backlog_note((2, 3), 2).startswith(
        "Không có review nào của dự án này chờ trước; 3 worker"
    )
    assert backlog_note((9, 2), 3) == (
        "Có 6 review của dự án này đang chờ trước; 2 worker review chạy song song, xen kẽ giữa "
        "các dự án."
    )


def test_lanes_already_in_redis_are_served_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 2026-11-13: REVIEW_LANES lowered, or differing between API and worker, must not
    strand jobs in lanes beyond it."""
    monkeypatch.setattr(queues, "lane_count", lambda: 1)
    sentinel: Any = object()
    existing = [
        Queue(n, connection=sentinel) for n in ("reviews:9", "reviews:x", "default", "reviews:1")
    ]
    monkeypatch.setattr(Queue, "all", classmethod(lambda cls, connection=None: existing))
    assert lane_names("reviews", connection=sentinel) == [
        "reviews:0",
        "reviews:1",
        "reviews:9",
        "reviews",
    ]
    assert "reviews_asked:0" in worker_queues(["reviews"], connection=sentinel)
    assert "reviews:9" in worker_queues(["reviews"], connection=sentinel)

    def down(cls: Any, connection: Any = None) -> list[Queue]:
        raise ConnectionError("redis down")

    monkeypatch.setattr(Queue, "all", classmethod(down))
    assert lane_names("reviews", connection=sentinel) == ["reviews:0", "reviews:1", "reviews"]
