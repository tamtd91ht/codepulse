"""Deterministic provider for tests (docs/specs/12: FakeLLMProvider)."""

from collections.abc import Callable, Sequence
from typing import Any

from app.review.providers.base import ReviewPrompt, ReviewResult

Responder = Callable[[ReviewPrompt], Any]


def no_findings(prompt: ReviewPrompt) -> dict[str, Any]:
    return {"summary": "No issues found.", "score": 100, "score_breakdown": {}, "findings": []}


class FakeLLMProvider:
    name = "fake"

    def __init__(self, responder: Responder = no_findings, cost_usd: float = 0.01) -> None:
        self._responder = responder
        self._cost = cost_usd
        self.prompts: list[ReviewPrompt] = []
        self.repo_calls: list[dict[str, Any]] = []  # deep review calls (prompt, workdir, ...)

    def review_in_repo(
        self,
        prompt: ReviewPrompt,
        workdir: str,
        *,
        tools: Sequence[str],
        max_turns: int,
        timeout_s: float,
    ) -> ReviewResult:
        self.repo_calls.append(
            {"prompt": prompt, "workdir": workdir, "tools": list(tools), "max_turns": max_turns}
        )
        return ReviewResult(
            output=self._responder(prompt),
            model="fake-model",
            prompt_tokens=len(prompt.user) // 4,
            completion_tokens=200,
            cost_usd=self._cost,
            raw={"num_turns": 3},
        )

    def review(self, prompt: ReviewPrompt) -> ReviewResult:
        self.prompts.append(prompt)
        return ReviewResult(
            output=self._responder(prompt),
            model="fake-model",
            prompt_tokens=len(prompt.system + prompt.user) // 4,
            completion_tokens=100,
            cost_usd=self._cost,
        )
