"""Client-side rate limiting shared by every outbound API client (docs/specs/AGENTS.md).

`RateLimiter` paces one process; `RedisRateLimiter` paces every worker together through a
per-minute window in Redis (docs/specs/19 §4.6: key `cp:llm:rate`). `shared_limiter` picks one.
"""

import logging
import time
from collections.abc import Callable
from typing import Protocol

import httpx
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

LLM_RATE_KEY = "cp:llm:rate"
WINDOW_S = 60


class Limiter(Protocol):
    def acquire(self) -> None: ...


class RateLimiter:
    """Client-side cap on requests per second, plus backing off when GitLab says we are close."""

    def __init__(
        self,
        max_per_second: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        min_remaining: int = 5,
    ) -> None:
        self._interval = 1.0 / max_per_second
        self._sleep = sleep
        self._clock = clock
        self._next_at = 0.0
        self._min_remaining = min_remaining

    def acquire(self) -> None:
        now = self._clock()
        if self._next_at > now:
            self._sleep(self._next_at - now)
            now = self._next_at
        self._next_at = now + self._interval

    def observe(self, headers: httpx.Headers, wall_clock: Callable[[], float] = time.time) -> None:
        """Honour RateLimit-Remaining / RateLimit-Reset (unix seconds) from GitLab."""
        remaining, reset = headers.get("RateLimit-Remaining"), headers.get("RateLimit-Reset")
        if remaining is None or reset is None:
            return
        try:
            if int(remaining) > self._min_remaining:
                return
            wait = float(reset) - wall_clock()
        except ValueError:
            return
        if wait > 0:
            log.warning("gitlab rate limit nearly exhausted", extra={"sleep_s": round(wait, 1)})
            self._sleep(min(wait, 60.0))


class RedisCommands(Protocol):
    """The slice of `redis.Redis` the shared limiter uses (a stub in tests)."""

    def incr(self, name: str) -> int: ...
    def expire(self, name: str, time: int) -> bool: ...
    def pttl(self, name: str) -> int: ...


class RedisRateLimiter:
    """At most `max_per_minute` acquisitions per fixed 60 s window, counted in Redis.

    INCR + EXPIRE: the first caller of a window sets its TTL; a caller past the cap sleeps until
    the window expires and retries. When Redis is unreachable the limiter degrades to pacing this
    process alone (`RateLimiter`) and warns once, so a Redis outage never stops reviews.
    """

    def __init__(
        self,
        max_per_minute: int,
        redis: RedisCommands,
        *,
        key: str = LLM_RATE_KEY,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_per_minute < 1:
            raise ValueError("max_per_minute must be >= 1")
        self._max = max_per_minute
        self._redis = redis
        self._key = key
        self._sleep = sleep
        self._fallback = RateLimiter(max_per_minute / WINDOW_S, sleep=sleep, clock=clock)
        self._warned = False

    def acquire(self) -> None:
        while True:
            try:
                count = self._redis.incr(self._key)
                if count == 1 or self._redis.pttl(self._key) < 0:
                    self._redis.expire(self._key, WINDOW_S)  # also heals a key left without TTL
                if count <= self._max:
                    return
                wait_ms = self._redis.pttl(self._key)
            except RedisError as exc:
                if not self._warned:
                    log.warning(
                        "redis rate limiter unavailable, pacing locally", extra={"error": str(exc)}
                    )
                    self._warned = True
                self._fallback.acquire()
                return
            self._sleep(wait_ms / 1000.0 + 0.001 if wait_ms > 0 else 0.05)


class CounterStore(Protocol):
    """The slice of `redis.Redis` a `WindowCounter` uses."""

    def incr(self, name: str) -> int: ...
    def expire(self, name: str, time: int) -> bool: ...


class WindowCounter:
    """Server-side cap: at most `limit` hits of one key per minute window; past it the caller is
    refused (HTTP 429), not paced. Counted in Redis so every API worker shares the window; when
    Redis is missing or down, counted in this process (and Redis is retried a minute later)."""

    def __init__(
        self,
        redis: Callable[[], CounterStore] | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._clock = clock
        self._local: dict[str, int] = {}
        self._local_minute = -1
        self._redis_down_until = 0.0

    def hit(self, key: str, limit: int) -> int | None:
        """Count one hit; seconds until the window ends when over `limit`, else None."""
        now = self._clock()
        minute = int(now // WINDOW_S)
        count: int | None = None
        if self._redis is not None and now >= self._redis_down_until:
            name = f"{key}:{minute}"
            try:
                store = self._redis()
                count = store.incr(name)
                if count == 1:
                    store.expire(name, 2 * WINDOW_S)
            except (RedisError, OSError) as exc:
                log.warning("rate counter unavailable, counting locally", extra={"error": str(exc)})
                self._redis_down_until = now + WINDOW_S
                count = None
        if count is None:
            if minute != self._local_minute:
                self._local, self._local_minute = {}, minute
            count = self._local.get(key, 0) + 1
            self._local[key] = count
        if count <= limit:
            return None
        return max(1, WINDOW_S - int(now % WINDOW_S))


def shared_limiter(
    max_per_minute: int,
    redis: RedisCommands | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Limiter:
    """The cross-worker limiter when a Redis client is given, else the in-process one."""
    if redis is None:
        return RateLimiter(max_per_minute / WINDOW_S, sleep=sleep, clock=clock)
    return RedisRateLimiter(max_per_minute, redis, sleep=sleep, clock=clock)
