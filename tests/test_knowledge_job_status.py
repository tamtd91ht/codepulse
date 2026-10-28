"""Whether knowledge builds are queued, for many repos at once (the dự án page's summary)."""

from typing import Any

import pytest
from rq.job import Job

from app.workers.knowledge_jobs import jobs_active


class StubJob:
    def __init__(self, job_id: str, status: str) -> None:
        self.id = job_id
        self.status = status

    def get_status(self, refresh: bool = False) -> str:
        return self.status


class OneByOne:
    """A queue without a Redis connection (as in the API tests): asked one id at a time."""

    def __init__(self, active: set[str]) -> None:
        self.active = active

    def fetch_job(self, job_id: str) -> StubJob | None:
        return StubJob(job_id, "queued") if job_id in self.active else None


class WithRedis:
    connection = object()


def test_no_ids_asks_nothing() -> None:
    assert jobs_active(WithRedis(), []) == set()


def test_queue_without_connection_falls_back_to_one_lookup_each() -> None:
    assert jobs_active(OneByOne({"kb-2"}), ["kb-1", "kb-2"]) == {"kb-2"}


def test_one_round_trip_keeps_only_waiting_or_running_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[list[str]] = []

    def fetch_many(ids: list[str], connection: Any) -> list[StubJob | None]:
        asked.append(ids)
        return [StubJob("kb-1", "started"), None, StubJob("kb-3", "finished")]

    monkeypatch.setattr(Job, "fetch_many", fetch_many)
    assert jobs_active(WithRedis(), ["kb-1", "kb-2", "kb-3"]) == {"kb-1"}
    assert asked == [["kb-1", "kb-2", "kb-3"]]


def test_redis_trouble_means_nothing_is_building(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(ids: list[str], connection: Any) -> list[StubJob | None]:
        raise ConnectionError("redis down")

    monkeypatch.setattr(Job, "fetch_many", broken)
    assert jobs_active(WithRedis(), ["kb-1"]) == set()
