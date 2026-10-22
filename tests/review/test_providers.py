import json
import subprocess
from typing import Any

import pytest

from app.review.providers import ClaudeCodeProvider, FakeLLMProvider, LLMError, ReviewPrompt

PROMPT = ReviewPrompt(system="sys", user="diff --git a/x", json_schema={"type": "object"})

SUCCESS = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "structured_output": {"summary": "ok", "score": 90, "findings": []},
    "result": "{}",
    "total_cost_usd": 0.0421,
    "usage": {
        "input_tokens": 10,
        "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": 200,
        "output_tokens": 300,
    },
    "modelUsage": {"claude-sonnet-5-5": {}},
}


class FakeRunner:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"cmd": cmd, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        code, stdout, stderr = outcome
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)


def provider(runner: FakeRunner, **kwargs: Any) -> ClaudeCodeProvider:
    return ClaudeCodeProvider(runner=runner, sleep=lambda s: None, max_per_minute=1e6, **kwargs)


def test_cli_runs_without_tools_settings_or_session() -> None:
    runner = FakeRunner((0, json.dumps(SUCCESS), ""))
    provider(runner, model="claude-sonnet-5-5").review(PROMPT)
    call = runner.calls[0]
    cmd = call["cmd"]
    assert cmd[:4] == ["claude", "-p", "--output-format", "json"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == {"type": "object"}
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"
    assert call["input"] == "diff --git a/x"  # the diff goes through stdin, not argv
    assert call["timeout"] == 120.0
    assert "codepulse-review-" in call["cwd"]


def test_parses_structured_output_tokens_and_cost() -> None:
    result = provider(FakeRunner((0, json.dumps(SUCCESS), ""))).review(PROMPT)
    assert result.output == SUCCESS["structured_output"]
    assert result.model == "claude-sonnet-5-5"
    assert (result.prompt_tokens, result.completion_tokens) == (1210, 300)
    assert result.cost_usd == pytest.approx(0.0421)


def test_retries_then_succeeds() -> None:
    error = {**SUCCESS, "is_error": True, "subtype": "error_during_execution"}
    runner = FakeRunner(
        subprocess.TimeoutExpired("claude", 120),
        (0, json.dumps(error), ""),
        (1, "", "overloaded"),
        (0, json.dumps(SUCCESS), ""),
    )
    assert provider(runner).review(PROMPT).cost_usd > 0
    assert len(runner.calls) == 4


def test_gives_up_after_retries() -> None:
    runner = FakeRunner(*[(1, "", "boom")] * 4)
    with pytest.raises(LLMError, match="after 4 attempts"):
        provider(runner).review(PROMPT)


@pytest.mark.parametrize(
    ("token", "variable"),
    [("sk-ant-oat01-abc", "CLAUDE_CODE_OAUTH_TOKEN"), ("sk-ant-api03-abc", "ANTHROPIC_API_KEY")],
)
def test_auth_token_goes_to_the_right_variable_and_never_into_errors(
    token: str, variable: str
) -> None:
    runner = FakeRunner(*[(1, "", "auth failed")] * 4)
    with pytest.raises(LLMError) as excinfo:
        provider(runner, auth_token=token).review(PROMPT)
    assert runner.calls[0]["env"][variable] == token
    assert token not in str(excinfo.value)
    assert token not in " ".join(runner.calls[0]["cmd"])


def test_fake_provider_records_prompts() -> None:
    fake = FakeLLMProvider()
    result = fake.review(PROMPT)
    assert result.output["findings"] == []
    assert fake.prompts == [PROMPT]
