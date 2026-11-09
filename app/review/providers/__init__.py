import logging
import os
from collections.abc import Callable
from typing import Final

from app.config import Settings
from app.ratelimit import RedisCommands, shared_limiter
from app.review.providers.base import (
    AgenticProvider,
    LLMError,
    LLMProvider,
    ReviewPrompt,
    ReviewResult,
)
from app.review.providers.claude_code import LLM_MAX_PER_MINUTE, ClaudeCodeProvider
from app.review.providers.failover import Account, FailoverProvider
from app.review.providers.fake import FakeLLMProvider

log = logging.getLogger(__name__)

__all__ = [
    "Account",
    "AgenticProvider",
    "ClaudeCodeProvider",
    "FailoverProvider",
    "FakeLLMProvider",
    "LLMError",
    "LLMProvider",
    "ReviewPrompt",
    "ReviewResult",
    "build_agentic_provider",
    "build_provider",
    "default_redis",
]


class _Auto:
    """`redis=AUTO`: resolve the worker's Redis client when the provider is built."""


AUTO: Final = _Auto()


def default_redis() -> RedisCommands | None:
    """The Redis every worker shares, for the cross-worker LLM rate limiter (docs/specs/19
    §4.6). None under pytest (tests never talk to a live Redis) and when the client cannot be
    built: the provider then paces this process alone, and reviews still run."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return None
    try:
        from app.db import get_redis

        return get_redis()
    except Exception as exc:
        log.warning("llm rate limiter: no redis, pacing locally", extra={"error": str(exc)[:200]})
        return None


def build_provider(
    settings: Settings,
    *,
    timeout_s: float | None = None,
    max_retries: int | None = None,
    accounts: Callable[[], list[Account]] | None = None,
    redis: RedisCommands | _Auto | None = AUTO,
) -> LLMProvider:
    """Only the Phase 0 provider exists; others raise until they are implemented.

    `timeout_s` / `max_retries` override the defaults for short interactive calls (the
    Telegram resolver), where waiting minutes would stall the poller. The calls run on the
    Claude accounts of Admin > LLM: the default one, backups only while it fails
    (app/review/llm_accounts.py; `accounts` replaces the stored ones in tests), always with the
    same pinned model. `redis` is the client of the shared rate limiter: AUTO resolves it
    lazily (`default_redis`), None paces this process alone."""
    if settings.llm_provider == "claude_code":
        from app.review import llm_accounts  # lazy: it imports this package

        extra: dict[str, float | int] = {}
        if timeout_s is not None:
            extra["timeout_s"] = timeout_s
        if max_retries is not None:
            extra["max_retries"] = max_retries
        client = default_redis() if isinstance(redis, _Auto) else redis
        limiter = shared_limiter(int(LLM_MAX_PER_MINUTE), client)
        model = llm_accounts.review_model(settings)

        def make(token: str) -> ClaudeCodeProvider:
            return ClaudeCodeProvider(
                binary=settings.claude_code_bin,
                model=model,
                auth_token=token,
                limiter=limiter,
                **extra,  # type: ignore[arg-type]
            )

        return FailoverProvider(
            accounts=accounts or (lambda: llm_accounts.load_accounts(settings)),
            make=make,
            model=model,
            limiter=limiter,
            on_failure=lambda account, kind, text: llm_accounts.record_failure(
                account, kind, text, settings, model
            ),
            on_success=llm_accounts.record_success,
        )
    raise NotImplementedError(f"LLM_PROVIDER={settings.llm_provider} is not implemented yet")


def build_agentic_provider(
    settings: Settings,
    *,
    accounts: Callable[[], list[Account]] | None = None,
    redis: RedisCommands | _Auto | None = AUTO,
) -> AgenticProvider | None:
    """The agentic pass and the verification need a provider with read-only tools; only Claude
    Code has one (docs/specs/19 §4.2-4.3)."""
    if not (settings.review_agent or settings.verify):
        return None
    provider = build_provider(settings, accounts=accounts, redis=redis)
    return provider if isinstance(provider, FailoverProvider) else None
