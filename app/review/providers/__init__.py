from app.config import Settings
from app.review.providers.base import LLMError, LLMProvider, ReviewPrompt, ReviewResult
from app.review.providers.claude_code import ClaudeCodeProvider
from app.review.providers.fake import FakeLLMProvider

__all__ = [
    "ClaudeCodeProvider",
    "FakeLLMProvider",
    "LLMError",
    "LLMProvider",
    "ReviewPrompt",
    "ReviewResult",
    "build_provider",
]


def build_provider(settings: Settings) -> LLMProvider:
    """Only the Phase 0 provider exists; others raise until they are implemented."""
    if settings.llm_provider == "claude_code":
        return ClaudeCodeProvider(
            binary=settings.claude_code_bin,
            model=settings.llm_model,
            auth_token=settings.llm_api_key.get_secret_value(),
        )
    raise NotImplementedError(f"LLM_PROVIDER={settings.llm_provider} is not implemented yet")
