"""Worker service: consumes the RQ `default` queue with JSON logs tagged by job_id."""

from typing import Any

from rq import Queue, Worker
from rq.job import Job

from app.db import get_redis
from app.logging import configure_logging, job_id_var

QUEUES = ["default"]


class JobContextWorker(Worker):
    def perform_job(self, job: Job, queue: Queue) -> Any:
        token = job_id_var.set(job.id)
        try:
            return super().perform_job(job, queue)
        finally:
            job_id_var.reset(token)


def main() -> None:
    configure_logging()
    redis = get_redis()
    worker = JobContextWorker([Queue(name, connection=redis) for name in QUEUES], connection=redis)
    # with_scheduler: delayed retries (REVIEW_RETRY waits 60/120/300 s) live in RQ's scheduled
    # registry and only run when a worker also acts as scheduler; without it they never run.
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
