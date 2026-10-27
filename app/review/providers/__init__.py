import logging
import os
from collections.abc import Callable
from typing import Final

from app.config import Settings
from app.ratelimit import RedisCommands, shared_limiter
from app.review.credentials import load_stored_claude_token
from app.review.providers.base import (
    AgenticProvider,
    LLMError,
    LLMProvider,
    ReviewPrompt,
    ReviewResult,
)
from app.review.providers.claude_code import LLM_MAX_PER_MINUTE, ClaudeCodeProvider
from app.review.providers.fake import FakeLLMProvider

log = logging.getLogger(__name__)

__all__ = [
    "AgenticProvider",
    "ClaudeCodeProvider",
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
    stored_token: Callable[[], str] = load_stored_claude_token,
    redis: RedisCommands | _Auto | None = AUTO,
) -> LLMProvider:
    """Only the Phase 0 provider exists; others raise until they are implemented.

    `timeout_s` / `max_retries` override the defaults for short interactive calls (the
    Telegram resolver), where waiting minutes would stall the poller. The token an admin set
    from the dashboard (`stored_token`) wins over the environment. `redis` is the client of
    the shared rate limiter: AUTO resolves it lazily (`default_redis`), None paces this
    process alone."""
    if settings.llm_provider == "claude_code":
        extra: dict[str, float | int] = {}
        if timeout_s is not None:
            extra["timeout_s"] = timeout_s
        if max_retries is not None:
            extra["max_retries"] = max_retries
        client = default_redis() if isinstance(redis, _Auto) else redis
        return ClaudeCodeProvider(
            binary=settings.claude_code_bin,
            model=settings.llm_model,
            auth_token=(
                stored_token()
                or settings.claude_code_oauth_token.get_secret_value()
                or settings.llm_api_key.get_secret_value()
            ),
            limiter=shared_limiter(int(LLM_MAX_PER_MINUTE), client),
            **extra,  # type: ignore[arg-type]
        )
    raise NotImplementedError(f"LLM_PROVIDER={settings.llm_provider} is not implemented yet")


def build_agentic_provider(
    settings: Settings,
    *,
    stored_token: Callable[[], str] = load_stored_claude_token,
    redis: RedisCommands | _Auto | None = AUTO,
) -> AgenticProvider | None:
    """The agentic pass and the verification need a provider with read-only tools; only Claude
    Code has one (docs/specs/19 §4.2-4.3)."""
    if not (settings.review_agent or settings.verify):
        return None
    provider = build_provider(settings, stored_token=stored_token, redis=redis)
    return provider if isinstance(provider, ClaudeCodeProvider) else None
