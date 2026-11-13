from typing import Any

import pytest
from rq import Worker

from app.logging import job_id_var
from app.workers.__main__ import JobContextWorker


def test_perform_job_passes_whatever_rq_hands_it_and_tags_the_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RQ 3 calls `perform_job(job, queue, execution)` in the work horse; an override that
    took only (job, queue) raised TypeError there, and RQ turns any error in the horse into a
    silent `os._exit(1)`: every job died ("Work-horse terminated unexpectedly")."""
    seen: list[Any] = []

    def base(self: Worker, job: Any, queue: Any, *rest: Any, **named: Any) -> bool:
        seen.append((job.id, queue, rest, named, job_id_var.get()))
        return True

    monkeypatch.setattr(Worker, "perform_job", base)
    job = type("FakeJob", (), {"id": "knowledge-12"})()
    worker = JobContextWorker.__new__(JobContextWorker)
    assert JobContextWorker.perform_job(worker, job, "q", "execution") is True  # type: ignore[arg-type]
    assert seen == [("knowledge-12", "q", ("execution",), {}, "knowledge-12")]
    assert job_id_var.get() != "knowledge-12"  # the tag does not leak past the job


def test_reorder_takes_the_du_an_lanes_in_turn() -> None:
    """User 2026-11-13: after a job of one dự án, the worker looks at the others first."""
    from rq import Queue

    sentinel: Any = object()
    names = ["default", "reviews_asked:1", "reviews_asked:2", "reviews:1", "reviews:2"]
    queues = [Queue(n, connection=sentinel) for n in names]
    worker = JobContextWorker.__new__(JobContextWorker)
    worker._ordered_queues = queues[:]
    worker.reorder_queues(queues[3])
    assert [q.name for q in worker._ordered_queues] == [
        "default",
        "reviews_asked:1",
        "reviews_asked:2",
        "reviews:2",
        "reviews:1",
    ]
