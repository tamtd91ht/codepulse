"""Worker service: consumes the RQ queues named on the command line (default: `default`) with
JSON logs tagged by job_id. The command worker runs `python -m app.workers commands`
(app/commands): a long command must not hold up reviews."""

import sys
from typing import Any

from rq import Queue, Worker
from rq.job import Job

from app.db import get_redis
from app.logging import configure_logging, job_id_var
from app.workers.queues import rotate, worker_queues

# The default worker also takes reviews when it has nothing else (app/workers/queues.py); a
# worker of "reviews" serves every lane of the asked-for tier ("reviews_asked:*") first, then
# every lane of "reviews:*", taking the lanes (one per dự án) in turn.
QUEUES = ["default", "reviews"]


class JobContextWorker(Worker):
    # Pass through whatever RQ hands the work horse: RQ 3 added an `execution` argument, and a
    # mismatch here is a TypeError that RQ turns into a silent os._exit(1) for every job.
    def perform_job(self, job: Job, queue: Queue, *args: Any, **kwargs: Any) -> Any:
        token = job_id_var.set(job.id)
        try:
            return super().perform_job(job, queue, *args, **kwargs)
        finally:
            job_id_var.reset(token)

    def reorder_queues(self, reference_queue: Queue) -> None:
        """Round-robin over the dự án lanes of a tier (app/workers/queues.py `rotate`): after a
        job of one dự án, the other dự án's lanes come first. The tiers keep their order."""
        self._ordered_queues = rotate(self._ordered_queues, reference_queue)


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    redis = get_redis()
    names = worker_queues((sys.argv[1:] if argv is None else argv) or QUEUES, connection=redis)
    worker = JobContextWorker([Queue(name, connection=redis) for name in names], connection=redis)
    # with_scheduler: delayed retries (REVIEW_RETRY waits 60/120/300 s) live in RQ's scheduled
    # registry and only run when a worker also acts as scheduler; without it they never run.
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
