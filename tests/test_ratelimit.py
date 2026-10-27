import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.ratelimit import LLM_RATE_KEY, RateLimiter, RedisRateLimiter, shared_limiter


class StubRedis:
    """INCR / EXPIRE / PTTL over a dict, expiring against an injected clock (seconds)."""

    def __init__(self, clock: list[float]) -> None:
        self._clock = clock
        self.values: dict[str, int] = {}
        self.expires_at: dict[str, float] = {}
        self.fail = False

    def _sweep(self, name: str) -> None:
        if name in self.expires_at and self.expires_at[name] <= self._clock[0]:
            self.values.pop(name, None)
            self.expires_at.pop(name, None)

    def incr(self, name: str) -> int:
        if self.fail:
            raise RedisConnectionError("down")
        self._sweep(name)
        self.values[name] = self.values.get(name, 0) + 1
        return self.values[name]

    def expire(self, name: str, time: int) -> bool:
        self.expires_at[name] = self._clock[0] + time
        return True

    def pttl(self, name: str) -> int:
        self._sweep(name)
        if name not in self.values:
            return -2
        if name not in self.expires_at:
            return -1
        return int((self.expires_at[name] - self._clock[0]) * 1000)


World = tuple[list[float], StubRedis, list[float]]


@pytest.fixture
def world() -> World:
    clock = [1000.0]
    return clock, StubRedis(clock), []


def test_under_the_cap_never_sleeps(world: World) -> None:
    clock, redis, slept = world
    limiter = RedisRateLimiter(3, redis, sleep=slept.append, clock=lambda: clock[0])
    for _ in range(3):
        limiter.acquire()
    assert slept == []
    assert redis.values[LLM_RATE_KEY] == 3
    assert redis.expires_at[LLM_RATE_KEY] == 1060.0


def test_over_the_cap_waits_for_the_window(world: World) -> None:
    clock, redis, slept = world

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    limiter = RedisRateLimiter(2, redis, sleep=sleep, clock=lambda: clock[0])
    limiter.acquire()
    limiter.acquire()
    clock[0] += 10
    limiter.acquire()  # third in the window: sleeps the remaining 50 s, then opens a new one
    assert len(slept) == 1 and 50.0 <= slept[0] <= 50.01
    assert redis.values[LLM_RATE_KEY] == 1


def test_the_window_is_shared_between_workers(world: World) -> None:
    clock, redis, slept = world

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    worker_a = RedisRateLimiter(2, redis, sleep=sleep, clock=lambda: clock[0])
    worker_b = RedisRateLimiter(2, redis, sleep=sleep, clock=lambda: clock[0])
    worker_a.acquire()
    worker_b.acquire()
    worker_b.acquire()  # the cap counts both workers: this one waits for the next window
    assert len(slept) == 1 and slept[0] >= 60.0


def test_a_key_left_without_ttl_is_healed(world: World) -> None:
    clock, redis, slept = world
    redis.values[LLM_RATE_KEY] = 5  # e.g. the process died between INCR and EXPIRE
    limiter = RedisRateLimiter(10, redis, sleep=slept.append, clock=lambda: clock[0])
    limiter.acquire()
    assert redis.pttl(LLM_RATE_KEY) == 60_000


def test_redis_failure_falls_back_to_local_pacing_and_warns_once(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    clock, redis, slept = world
    redis.fail = True
    limiter = RedisRateLimiter(60, redis, sleep=slept.append, clock=lambda: clock[0])
    with caplog.at_level(logging.WARNING, logger="app.ratelimit"):
        limiter.acquire()
        limiter.acquire()  # 1/s locally and the clock does not move: each later call sleeps
        limiter.acquire()
    assert len(slept) == 2 and all(0 < s <= 2.0 for s in slept)
    assert sum("pacing locally" in r.message for r in caplog.records) == 1


def test_rejects_a_zero_cap(world: World) -> None:
    with pytest.raises(ValueError):
        RedisRateLimiter(0, world[1])


def test_shared_limiter_picks_by_client(world: World) -> None:
    assert isinstance(shared_limiter(30), RateLimiter)
    assert isinstance(shared_limiter(30, world[1]), RedisRateLimiter)
