"""One HTTP delivery: build the body for the webhook's format, sign it, POST with timeout and
retries (docs/specs/AGENTS.md: every outbound call has retry + timeout)."""

import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.notifications.payload import EVENT_LABEL, text

log = logging.getLogger(__name__)

TIMEOUT_S = 10.0
ATTEMPTS = 3
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
FORMATS = ("json", "slack", "teams")
USER_AGENT = "CodePulse-Webhook/1.0"


@dataclass
class DeliveryResult:
    ok: bool
    status_code: int | None
    error: str | None
    attempts: int
    delivery_id: str


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC over `<timestamp>.<body>` so a captured delivery cannot be replayed later unnoticed."""
    message = f"{timestamp}.".encode() + body
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def body_for(fmt: str, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    if fmt == "slack":
        return {"text": text(event, payload), "mrkdwn": True}
    if fmt == "teams":
        title = f"CodePulse: {EVENT_LABEL.get(event, event)} - {payload['review']['label']}"
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": title,
            "title": title,
            "text": text(event, payload).replace("\n", "\n\n"),
        }
    return {"event": event, **payload}


def deliver(
    url: str,
    fmt: str,
    secret: str | None,
    event: str,
    payload: dict[str, Any],
    http: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = ATTEMPTS,
    now: Callable[[], float] = time.time,
) -> DeliveryResult:
    """POST once per attempt; 4xx (other than 408/425/429) is final, the rest is retried."""
    delivery_id = uuid.uuid4().hex
    timestamp = str(int(now()))
    body = json.dumps(body_for(fmt, event, payload), ensure_ascii=False).encode()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": USER_AGENT,
        "X-CodePulse-Event": event,
        "X-CodePulse-Delivery": delivery_id,
        "X-CodePulse-Timestamp": timestamp,
    }
    if secret:
        headers["X-CodePulse-Signature"] = sign(secret, timestamp, body)
    client = http or httpx.Client(timeout=TIMEOUT_S, follow_redirects=False)
    status: int | None = None
    error: str | None = None
    attempt = 0
    try:
        for attempt in range(1, attempts + 1):
            try:
                response = client.post(url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                status, error = None, exc.__class__.__name__
            else:
                status = response.status_code
                if response.is_success:
                    return DeliveryResult(True, status, None, attempt, delivery_id)
                error = f"HTTP {status}"
                if status not in RETRY_STATUS:
                    break
            log.warning(
                "webhook delivery failed",
                extra={"event": event, "attempt": attempt, "status": status, "error": error},
            )
            if attempt < attempts:
                sleep(2.0 * 2 ** (attempt - 1))
        return DeliveryResult(False, status, error, attempt, delivery_id)
    finally:
        if http is None:
            client.close()
