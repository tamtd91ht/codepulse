from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ReviewPrompt:
    system: str
    user: str
    json_schema: dict[str, Any]


@dataclass
class ReviewResult:
    """What one LLM call returned, plus what it cost (docs/specs/06: store model, tokens, cost)."""

    output: Any  # parsed JSON when the provider returns structured output, else raw text
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class LLMError(Exception):
    """The provider failed after its retries; the review is marked failed, never half-stored."""


class LLMProvider(Protocol):
    name: str

    def review(self, prompt: ReviewPrompt) -> ReviewResult: ...


class AgenticProvider(Protocol):
    """A provider that can answer with read-only tools inside a directory (docs/specs/18)."""

    name: str

    def review_in_repo(
        self,
        prompt: ReviewPrompt,
        workdir: str,
        *,
        tools: Sequence[str],
        max_turns: int,
        timeout_s: float,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> ReviewResult: ...
