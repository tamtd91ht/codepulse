"""Telegram Bot API client: timeout, retry with backoff, rate-limit replies (AGENTS.md).

The bot token is part of every URL, so errors never carry the URL or the response body.
"""

import logging
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_MESSAGE = 4096


class TelegramError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        api_base: str = "https://api.telegram.org",
        timeout: float = 30.0,
        max_retries: int = 3,
        max_retry_after: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url=f"{api_base.rstrip('/')}/bot{token}", timeout=timeout, transport=transport
        )
        self._max_retries = max_retries
        self._max_retry_after = max_retry_after
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "TelegramClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(self, method: str, **params: Any) -> Any:
        """POST one Bot API method; returns `result`. Retries 429/5xx and transport errors."""
        body = {k: v for k, v in params.items() if v is not None}
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.post(f"/{method}", json=body)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise TelegramError(f"{method}: {exc.__class__.__name__}") from None
                self._sleep(self._backoff(attempt, None))
                continue
            payload = self._payload(response)
            if response.status_code in RETRY_STATUS and attempt < self._max_retries:
                retry_after = (payload.get("parameters") or {}).get("retry_after")
                delay = self._backoff(attempt, retry_after)
                if delay > self._max_retry_after:
                    # Too long to hold the caller; the next poll tick is the retry.
                    description = str(payload.get("description") or "")[:200]
                    raise TelegramError(
                        f"{method} -> HTTP {response.status_code}: {description}",
                        response.status_code,
                    )
                log.warning(
                    "telegram request retried",
                    extra={"method": method, "status": response.status_code, "delay_s": delay},
                )
                self._sleep(delay)
                continue
            if response.is_error or not payload.get("ok"):
                description = str(payload.get("description") or "")[:200]
                raise TelegramError(
                    f"{method} -> HTTP {response.status_code}: {description}", response.status_code
                )
            return payload.get("result")
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _backoff(attempt: int, retry_after: Any) -> float:
        if retry_after is not None:
            try:
                return min(float(retry_after), 120.0)
            except (TypeError, ValueError):
                pass
        return min(2.0**attempt, 60.0) + random.uniform(0, 0.5)  # noqa: S311 - jitter, not crypto

    # -- methods ------------------------------------------------------------------------------

    def get_me(self) -> dict[str, Any]:
        result = self.call("getMe")
        return result if isinstance(result, dict) else {}

    def get_updates(self, offset: int | None, timeout: int = 0, limit: int = 50) -> list[Any]:
        result = self.call(
            "getUpdates",
            offset=offset,
            timeout=timeout,
            limit=limit,
            allowed_updates=["message", "callback_query"],
        )
        return list(result) if isinstance(result, list) else []

    def delete_webhook(self) -> None:
        """Remove a webhook registered on this token; getUpdates is refused while one exists."""
        self.call("deleteWebhook", drop_pending_updates=False)

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send HTML text; longer messages are split on line breaks under Telegram's limit.
        The keyboard, when given, goes on the last part."""
        last: dict[str, Any] = {}
        parts = split_message(text)
        for i, part in enumerate(parts):
            result = self.call(
                "sendMessage",
                chat_id=chat_id,
                text=part,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_to_message_id=reply_to_message_id if i == 0 else None,
                allow_sending_without_reply=True,
                reply_markup=reply_markup if i == len(parts) - 1 else None,
            )
            last = result if isinstance(result, dict) else {}
        return last

    def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        """Replace a message the bot sent (the picker list); no keyboard = buttons removed."""
        self.call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=split_message(text)[0],
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup or {"inline_keyboard": []},
        )

    def answer_callback(
        self, callback_id: str, text: str | None = None, alert: bool = False
    ) -> None:
        """Stop the button spinner; `alert` pops the text up instead of a toast."""
        self.call(
            "answerCallbackQuery",
            callback_query_id=callback_id,
            text=(text or "")[:200] or None,
            show_alert=alert or None,
        )


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single very long line
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts
