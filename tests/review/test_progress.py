"""Live progress records of running reviews (docs/specs/17 §Tiến trình)."""

import time
from types import SimpleNamespace
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError

from app.review.progress import (
    INDEX_KEY,
    ITEM_KEY,
    LIVE_KEY,
    LIVE_MAX,
    LIVE_TTL_S,
    RECENT_KEY,
    RECENT_MAX,
    RedisProgress,
    queued_reviews,
)


class FakeRedis:
    """The handful of Redis commands RedisProgress uses."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.zset: dict[str, dict[str, float]] = {}
        self.lists: dict[str, list[str]] = {}
        self.ttl: dict[str, int] = {}

    def pipeline(self) -> "FakeRedis":
        return self

    def execute(self) -> list[Any]:
        return []

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.kv[key] = value
        if ex:
            self.ttl[key] = ex

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def mget(self, keys: list[str]) -> list[str | None]:
        return [self.kv.get(k) for k in keys]

    def delete(self, key: str) -> None:
        self.kv.pop(key, None)

    def expire(self, key: str, seconds: int) -> None:
        self.ttl[key] = seconds

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.zset.setdefault(key, {}).update(mapping)

    def zrem(self, key: str, *members: str) -> None:
        for m in members:
            self.zset.get(key, {}).pop(m, None)

    def zremrangebyscore(self, key: str, low: float, high: float) -> None:
        z = self.zset.get(key, {})
        for m in [m for m, s in z.items() if low <= s <= high]:
            z.pop(m)

    def zrange(self, key: str, start: int, end: int) -> list[bytes]:
        items = sorted(self.zset.get(key, {}).items(), key=lambda kv: kv[1])
        return [k.encode() for k, _ in items[start : end + 1]]

    def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    def rpush(self, key: str, *values: str) -> None:
        self.lists.setdefault(key, []).extend(values)

    def ltrim(self, key: str, start: int, end: int) -> None:
        self.lists[key] = self.lrange(key, start, end)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        """Redis ranges: both ends inclusive, -1 is the last element."""
        items = self.lists.get(key, [])
        return items[start : (end + 1) or None]


class BrokenRedis:
    def __getattr__(self, name: str) -> Any:
        def fail(*args: Any, **kwargs: Any) -> Any:
            raise RedisConnectionError("down")

        return fail


def test_a_running_review_is_listed_with_its_stage_until_it_ends_with_its_outcome() -> None:
    redis = FakeRedis()
    progress = RedisProgress(redis)  # type: ignore[arg-type]
    progress.start(41, {"kind": "mr", "project_id": 1, "ref": "7", "started_at": "t0"})
    progress.stage(41, "llm", "phần 2/3", loc=120)
    (item,) = progress.active()
    assert (item["review_id"], item["stage"], item["detail"], item["loc"]) == (
        41,
        "llm",
        "phần 2/3",
        120,
    )
    assert redis.ttl[ITEM_KEY.format(41)] == 50 * 60  # a crashed worker leaves nothing for long
    progress.end(41, "done")
    assert progress.active() == []
    (ended,) = progress.recent()
    assert (ended["review_id"], ended["status"], ended["ref"]) == (41, "done", "7")
    assert "stage" not in ended and ended["ended_at"]


def test_the_recent_list_is_capped() -> None:
    redis = FakeRedis()
    progress = RedisProgress(redis)  # type: ignore[arg-type]
    for i in range(RECENT_MAX + 5):
        progress.start(i, {"kind": "commit"})
        progress.end(i, "failed")
    assert len(progress.recent()) == RECENT_MAX
    assert progress.recent()[0]["review_id"] == RECENT_MAX + 4  # newest first
    redis.lists[RECENT_KEY].insert(0, "{broken")
    assert len(progress.recent()) == RECENT_MAX - 1  # a broken entry is skipped, not an error


def test_expired_and_abandoned_records_drop_out_of_the_index() -> None:
    redis = FakeRedis()
    progress = RedisProgress(redis)  # type: ignore[arg-type]
    progress.start(1, {"kind": "commit"})
    progress.start(2, {"kind": "commit"})
    progress.start(3, {"kind": "commit"})
    del redis.kv[ITEM_KEY.format(1)]  # TTL ran out
    redis.zset[INDEX_KEY]["3"] = time.time() - 2 * 60 * 60  # a worker died long ago
    assert [i["review_id"] for i in progress.active()] == [2]
    assert set(redis.zset[INDEX_KEY]) == {"2"}
    redis.kv[ITEM_KEY.format(2)] = "{not json"
    assert progress.active() == []


def test_redis_trouble_never_breaks_a_review_or_the_page() -> None:
    progress = RedisProgress(BrokenRedis())  # type: ignore[arg-type]
    progress.start(1, {"kind": "mr"})
    progress.stage(1, "llm")
    progress.end(1, "done")
    assert progress.active() == [] and progress.recent() == []


def test_stage_of_an_unknown_review_is_ignored() -> None:
    redis = FakeRedis()
    RedisProgress(redis).stage(9, "llm")  # type: ignore[arg-type]
    assert redis.kv == {}


def test_queued_reviews_are_read_from_job_ids_only() -> None:
    sha = "a1b2c3d4e5f6"
    queue = SimpleNamespace(
        get_job_ids=lambda offset, length: [
            "sync-project-3",
            f"review-mr-3-7-{sha}",
            f"review-mr-3-7-{sha}-rerun-55-2".encode(),
            f"review-commit-{sha}",
            f"review-commit-{sha}-again-1",
            "1f0e9a7c-uuid-without-a-name",
        ]
    )
    items = queued_reviews(queue)
    assert [(i["kind"], i["project_id"], i["ref"], i["trigger"]) for i in items] == [
        ("mr", 3, "7", ""),
        ("mr", 3, "7", "rerun"),
        ("commit", None, sha, ""),
        ("commit", None, sha, "telegram"),
    ]


def test_queued_reviews_survives_an_unreachable_queue() -> None:
    def down(offset: int, length: int) -> Any:
        raise RedisConnectionError("down")

    assert queued_reviews(SimpleNamespace(get_job_ids=down)) == []


def test_live_entries_are_capped_and_expire() -> None:
    redis = FakeRedis()
    progress = RedisProgress(redis)  # type: ignore[arg-type]
    progress.event(41, [{"kind": "tool", "turn": n} for n in range(LIVE_MAX + 3)])
    progress.event(41, [])  # nothing to write
    kept = progress.live(41)
    assert len(kept) == LIVE_MAX and kept[0]["turn"] == 3 and kept[-1]["turn"] == LIVE_MAX + 2
    assert redis.ttl[LIVE_KEY.format(41)] == LIVE_TTL_S
    assert progress.live(42) == []


def test_live_view_survives_a_broken_redis() -> None:
    progress = RedisProgress(BrokenRedis())  # type: ignore[arg-type]
    progress.event(41, [{"kind": "tool"}])
    assert progress.live(41) == []
