from app.config import Settings
from app.review.providers.base import (
    AgenticProvider,
    LLMError,
    LLMProvider,
    ReviewPrompt,
    ReviewResult,
)
from app.review.providers.claude_code import ClaudeCodeProvider
from app.review.providers.fake import FakeLLMProvider

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
]


def build_provider(
    settings: Settings, *, timeout_s: float | None = None, max_retries: int | None = None
) -> LLMProvider:
    """Only the Phase 0 provider exists; others raise until they are implemented.

    `timeout_s` / `max_retries` override the defaults for short interactive calls (the
    Telegram resolver), where waiting minutes would stall the poller."""
    if settings.llm_provider == "claude_code":
        extra: dict[str, float | int] = {}
        if timeout_s is not None:
            extra["timeout_s"] = timeout_s
        if max_retries is not None:
            extra["max_retries"] = max_retries
        return ClaudeCodeProvider(
            binary=settings.claude_code_bin,
            model=settings.llm_model,
            auth_token=(
                settings.claude_code_oauth_token.get_secret_value()
                or settings.llm_api_key.get_secret_value()
            ),
            **extra,  # type: ignore[arg-type]
        )
    raise NotImplementedError(f"LLM_PROVIDER={settings.llm_provider} is not implemented yet")


def build_agentic_provider(settings: Settings) -> AgenticProvider | None:
    """The deep review needs a provider with read-only tools; only Claude Code has one."""
    if not settings.deep_review:
        return None
    provider = build_provider(settings)
    return provider if isinstance(provider, ClaudeCodeProvider) else None
