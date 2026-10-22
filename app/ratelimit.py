"""Client-side rate limiting shared by every outbound API client (docs/specs/AGENTS.md)."""

import logging
import time
from collections.abc import Callable

import httpx

log = logging.getLogger(__name__)


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
