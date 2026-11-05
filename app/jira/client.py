"""Jira Data Center / Server REST v2 client, read only (docs/specs/27 §2): Bearer PAT (Jira
8.14+) or basic auth with a service account (older Jira), timeout, retry with backoff on 5xx
and network errors, Retry-After of a 429 (at most 30 s), at least 100 ms between two calls. A
refused login (401, or a 403 Seraph denies) is never retried: Jira Server locks an account behind
a CAPTCHA after a few failed logins. The token and the password are never logged."""

import base64
import logging
import random
import ssl
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import httpx

from app.jira.settings import AuthType, JiraConnection
from app.ratelimit import RateLimiter

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
PAGE_SIZE = 100
MAX_RETRY_AFTER_S = 30.0
MIN_INTERVAL_S = 0.1
# Seraph (Jira Server's login filter) says why a login was refused.
SERAPH_REASON = "X-Seraph-LoginReason"
DENIED_REASON = "X-Authentication-Denied-Reason"  # e.g. CAPTCHA_CHALLENGE
SERAPH_DENIED = ("AUTHENTICATION_DENIED", "AUTHENTICATED_FAILED")
LOGIN_DENIED = (
    "sai tài khoản/mật khẩu, hoặc Jira đã bật CAPTCHA sau nhiều lần đăng nhập sai: đăng nhập web"
    " bằng tài khoản dịch vụ để giải CAPTCHA rồi thử lại"
)
PAT_REFUSED = (
    "Jira từ chối token: Personal Access Token sai, hết hạn hoặc đã bị thu hồi (Jira cũ hơn"
    " 8.14 không có PAT: chọn Tài khoản + mật khẩu)"
)
BASIC_REFUSED = "Jira từ chối đăng nhập: " + LOGIN_DENIED


class JiraError(Exception):
    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class JiraAuthError(JiraError):
    """Jira refused the credentials: retrying would only count more failed logins."""


def auth_header(auth_type: AuthType, token: str, username: str = "") -> str:
    """`Bearer <PAT>`, or `Basic base64(username:password)` (UTF-8, so a non-ASCII password
    works)."""
    if auth_type == "basic":
        pair = f"{username}:{token}".encode()
        return "Basic " + base64.b64encode(pair).decode("ascii")
    return f"Bearer {token}"


class JiraClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 20.0,
        verify: ssl.SSLContext | str | bool = True,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        auth_type: AuthType = "pat",
        username: str = "",
    ) -> None:
        self._auth_type = auth_type
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/rest/api/2",
            headers={
                "Authorization": auth_header(auth_type, token, username),
                "Accept": "application/json",
            },
            timeout=timeout,
            verify=verify,
            transport=transport,
        )
        self._max_retries = max_retries
        self._sleep = sleep
        self._limiter = RateLimiter(1.0 / MIN_INTERVAL_S, sleep=sleep)

    @classmethod
    def from_connection(cls, connection: JiraConnection) -> "JiraClient":
        """The connection in effect (app/jira/settings.py): saved on the web, else JIRA_*."""
        return cls(
            connection.base_url,
            connection.token,
            timeout=connection.timeout_s,
            verify=connection.verify(),
            auth_type=connection.auth_type,
            username=connection.username,
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
            if refused := self._refused(response):
                raise JiraAuthError(
                    response.status_code,
                    f"{method} {path} -> HTTP {response.status_code}: {refused}",
                )
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

    def _refused(self, response: httpx.Response) -> str:
        """Why Jira refused the login ("" when it did not): any 401, or a 403 whose Seraph
        headers say the login was denied (wrong password, CAPTCHA)."""
        seraph = response.headers.get(SERAPH_REASON, "").upper()
        denied = any(r in seraph for r in SERAPH_DENIED) or bool(
            response.headers.get(DENIED_REASON)
        )
        if response.status_code not in (401, 403) or (response.status_code == 403 and not denied):
            return ""
        if denied:
            return "Jira từ chối đăng nhập: " + LOGIN_DENIED
        return BASIC_REFUSED if self._auth_type == "basic" else PAT_REFUSED

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

    def myself(self) -> dict[str, Any]:
        """The account the credentials log in as (serverInfo answers anonymous callers too)."""
        return dict(self.request("GET", "/myself") or {})

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
