"""Report delivery: email (SMTP), Slack and Microsoft Teams webhooks (docs/specs/10).

Every call has a timeout and a few retries with backoff (docs/specs/AGENTS.md).
"""

import logging
import smtplib
import ssl
import time
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

import httpx

log = logging.getLogger(__name__)

TIMEOUT_S = 30
ATTEMPTS = 3


class DeliveryError(Exception):
    pass


def _retry(
    action: Callable[[], None], what: str, sleep: Callable[[float], None] = time.sleep
) -> None:
    for attempt in range(ATTEMPTS):
        try:
            action()
            return
        except (OSError, smtplib.SMTPException, httpx.HTTPError) as exc:
            log.warning(
                "delivery failed",
                extra={"channel": what, "attempt": attempt + 1, "error": str(exc)[:200]},
            )
            if attempt == ATTEMPTS - 1:
                raise DeliveryError(f"{what}: {exc.__class__.__name__}") from exc
            sleep(2.0 * 2**attempt)


class Sender(Protocol):
    def email(self, to: list[str], subject: str, html: str, text: str) -> None: ...
    def slack(self, webhook: str, text: str) -> None: ...
    def teams(self, webhook: str, title: str, text: str) -> None: ...


class Delivery:
    def __init__(self, smtp_url: str, sender: str, http: httpx.Client | None = None) -> None:
        self._smtp = urlparse(smtp_url) if smtp_url else None
        self._from = sender
        self._http = http or httpx.Client(timeout=TIMEOUT_S)

    def email(self, to: list[str], subject: str, html: str, text: str) -> None:
        if self._smtp is None or not to:
            return
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self._from, ", ".join(to), subject
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        url = self._smtp

        def send() -> None:
            host, user = url.hostname or "localhost", unquote(url.username or "")
            if url.scheme == "smtps":
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    host, url.port or 465, timeout=TIMEOUT_S, context=ssl.create_default_context()
                )
            else:
                client = smtplib.SMTP(host, url.port or 587, timeout=TIMEOUT_S)
                client.starttls(context=ssl.create_default_context())
            with client:
                if user:
                    client.login(user, unquote(url.password or ""))
                client.send_message(message)

        _retry(send, "email")

    def _post(self, webhook: str, payload: dict[str, Any], what: str) -> None:
        def post() -> None:
            self._http.post(webhook, json=payload).raise_for_status()

        _retry(post, what)

    def slack(self, webhook: str, text: str) -> None:
        self._post(webhook, {"text": text, "mrkdwn": True}, "slack")

    def teams(self, webhook: str, title: str, text: str) -> None:
        card = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": title,
            "title": title,
            "text": text,
        }
        self._post(webhook, card, "teams")
