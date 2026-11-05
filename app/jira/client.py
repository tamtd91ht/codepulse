"""Jira Data Center REST v2 client, read only (docs/specs/27 §2): Bearer PAT, timeout, retry
with backoff on 5xx and network errors, Retry-After of a 429 (at most 30 s), at least 100 ms
between two calls. The token is never logged."""

import logging
import random
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import httpx

from app.config import Settings
from app.ratelimit import RateLimiter

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
PAGE_SIZE = 100
MAX_RETRY_AFTER_S = 30.0
MIN_INTERVAL_S = 0.1


class JiraError(Exception):
    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class JiraClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 20.0,
        verify: str | bool = True,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/rest/api/2",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
            verify=verify,
            transport=transport,
        )
        self._max_retries = max_retries
        self._sleep = sleep
        self._limiter = RateLimiter(1.0 / MIN_INTERVAL_S, sleep=sleep)

    @classmethod
    def from_settings(cls, settings: Settings) -> "JiraClient":
        return cls(
            settings.jira_base_url,
            settings.jira_token.get_secret_value(),
            timeout=settings.jira_timeout_s,
            verify=settings.jira_ca_bundle or True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------------------------

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise JiraError(None, f"{method} {path}: {exc.__class__.__name__}") from exc
                delay = self._backoff(attempt)
                log.warning(
                    "jira request retried",
                    extra={"error": exc.__class__.__name__, "path": path, "delay_s": delay},
                )
                self._sleep(delay)
                continue
            if response.status_code in RETRY_STATUS and attempt < self._max_retries:
                delay = self._delay(attempt, response)
                log.warning(
                    "jira request retried",
                    extra={"status": response.status_code, "path": path, "delay_s": delay},
                )
                self._sleep(delay)
                continue
            if response.is_error:
                raise JiraError(
                    response.status_code,
                    f"{method} {path} -> HTTP {response.status_code}{_reason(response)}",
                )
            try:
                return response.json()
            except ValueError as exc:
                raise JiraError(response.status_code, f"{method} {path}: not JSON") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def _delay(self, attempt: int, response: httpx.Response) -> float:
        if response.status_code == 429:
            try:
                return min(
                    max(float(response.headers.get("Retry-After", "")), 0.0), MAX_RETRY_AFTER_S
                )
            except ValueError:
                pass
        return min(self._backoff(attempt), MAX_RETRY_AFTER_S)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0**attempt, 30.0) + random.uniform(0, 0.5)  # noqa: S311 - jitter, not crypto

    # -- endpoints (docs/specs/27 §2) --------------------------------------------------------

    def server_info(self) -> dict[str, Any]:
        return dict(self.request("GET", "/serverInfo") or {})

    def fields(self) -> list[dict[str, Any]]:
        return [f for f in self.request("GET", "/field") or [] if isinstance(f, dict)]

    def projects(self) -> list[dict[str, Any]]:
        return [p for p in self.request("GET", "/project") or [] if isinstance(p, dict)]

    def search(
        self, jql: str, fields: Sequence[str], start_at: int = 0, max_results: int = PAGE_SIZE
    ) -> dict[str, Any]:
        body = {
            "jql": jql,
            "fields": list(fields),
            "startAt": start_at,
            "maxResults": max_results,
            "validateQuery": True,
        }
        return dict(self.request("POST", "/search", json=body) or {})

    def search_all(
        self, jql: str, fields: Sequence[str], max_results: int = PAGE_SIZE
    ) -> Iterator[dict[str, Any]]:
        """Every issue matching `jql`, page after page."""
        start = 0
        while True:
            page = self.search(jql, fields, start, max_results)
            issues = [i for i in page.get("issues") or [] if isinstance(i, dict)]
            yield from issues
            start += len(issues)
            total = int(page.get("total") or 0)
            if not issues or start >= total:
                return


def _reason(response: httpx.Response) -> str:
    """Jira's own error text (errorMessages), short; never anything from the request."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    messages = [str(m) for m in body.get("errorMessages") or []]
    messages += [f"{k}: {v}" for k, v in (body.get("errors") or {}).items()]
    return (": " + "; ".join(messages))[:300] if messages else ""
