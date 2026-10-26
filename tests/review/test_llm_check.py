import json
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.review.llm_check import (
    ACCOUNT_NOTE_FROM_ENV,
    AUTH_API_KEY,
    AUTH_NONE,
    AUTH_OAUTH,
    AUTH_STATUS_TIMEOUT_S,
    PING,
    PROMPT_TIMEOUT_S,
    VERSION_TIMEOUT_S,
    check_llm,
    mask_token,
)
from app.review.providers import ClaudeCodeProvider, LLMError

TOKEN = "sk-ant-oat01-SECRETsecretSECRETsecret-WXYZ"

PING_OK = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "structured_output": {"ok": True},
    "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 700, "output_tokens": 4},
    "modelUsage": {"claude-sonnet-5-5": {}},
}

# What `claude auth status --json` prints for a token from the environment (CLI 2.1.x).
STATUS_ENV_TOKEN = {"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"}
# ... and after an interactive `claude auth login`.
STATUS_LOGIN = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "email": "ops@example.com",
    "orgId": "00000000-0000-0000-0000-000000000000",
    "orgName": "Example JSC",
    "subscriptionType": "team",
    "configDirectory": "/home/app/.claude",
}

Outcome = tuple[int, str, str] | BaseException


class FakeRunner:
    """Answers by command kind: `--version`, `auth status`, or the `-p` prompt."""

    def __init__(self, version: Outcome, status: Outcome, prompt: Outcome) -> None:
        self.outcomes = {"version": version, "status": status, "prompt": prompt}
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"cmd": cmd, **kwargs})
        kind = "version" if "--version" in cmd else "status" if "auth" in cmd else "prompt"
        outcome = self.outcomes[kind]
        if isinstance(outcome, BaseException):
            raise outcome
        code, stdout, stderr = outcome
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)


def ok_runner(**overrides: Outcome) -> FakeRunner:
    outcomes: dict[str, Outcome] = {
        "version": (0, "2.1.309 (Claude Code)\n", ""),
        "status": (0, json.dumps(STATUS_ENV_TOKEN), ""),
        "prompt": (0, json.dumps(PING_OK), ""),
        **overrides,
    }
    return FakeRunner(outcomes["version"], outcomes["status"], outcomes["prompt"])


def ticking_clock() -> Iterator[float]:
    t = 0.0
    while True:
        yield t
        t += 0.25


def settings(**kwargs: Any) -> Settings:
    return Settings(**{"claude_code_oauth_token": SecretStr(TOKEN), **kwargs})


def run(runner: FakeRunner, **kwargs: Any) -> dict[str, Any]:
    clock = ticking_clock()
    report = check_llm(settings(**kwargs), runner=runner, clock=lambda: next(clock))
    return report.to_dict()


def test_success_reports_config_cli_auth_and_live_call() -> None:
    runner = ok_runner()
    report = run(runner, llm_model="claude-sonnet-5-5")
    assert report["provider"] == "claude_code"
    assert report["model"] == "claude-sonnet-5-5"
    assert (report["cli_binary"], report["cli_version"]) == ("claude", "2.1.309 (Claude Code)")
    assert report["auth"] == {
        "method": AUTH_OAUTH,
        "token_hint": "sk-ant-oat01-…WXYZ",
        "cli_method": "oauth_token",
        "logged_in": True,
    }
    assert report["account"] is None and report["account_note"] == ACCOUNT_NOTE_FROM_ENV
    status = report["status"]
    assert status["ok"] is True and status["error"] is None
    assert status["model"] == "claude-sonnet-5-5"
    assert status["cost_usd"] == pytest.approx(0.0123)
    assert (status["prompt_tokens"], status["completion_tokens"]) == (702, 4)
    assert status["latency_ms"] == 250
    # Every CLI call is bounded, sandboxed, and gets the token only through the environment.
    for call in runner.calls:
        assert call["timeout"] <= PROMPT_TIMEOUT_S
        assert "codepulse-review-" in call["cwd"]
        assert call["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
        assert TOKEN not in " ".join(call["cmd"])
    prompt_call = runner.calls[-1]
    assert prompt_call["cmd"][:2] == ["claude", "-p"]
    assert "--model" in prompt_call["cmd"]
    assert len(runner.calls) == 3  # no retries for a check


def test_interactive_login_reveals_the_account() -> None:
    report = run(ok_runner(status=(0, json.dumps(STATUS_LOGIN), "")))
    assert report["account"] == {
        "email": "ops@example.com",
        "organization": "Example JSC",
        "subscription_type": "team",
        "api_provider": "firstParty",
    }
    assert report["account_note"] is None
    assert "configDirectory" not in json.dumps(report)
    shown = {k: v for k, v in STATUS_LOGIN.items() if k != "configDirectory"}
    assert report["cli_auth_status"] == shown


def test_cli_auth_status_is_shown_without_secrets() -> None:
    status = {**STATUS_ENV_TOKEN, "note": f"token {TOKEN}", "projectsDirectory": "/home/x"}
    report = run(ok_runner(status=(1, json.dumps(status), "")))
    shown = report["cli_auth_status"]
    assert shown["authMethod"] == "oauth_token" and "projectsDirectory" not in shown
    assert TOKEN not in json.dumps(report)


def test_default_model_and_api_key_auth() -> None:
    report = run(
        ok_runner(),
        claude_code_oauth_token=SecretStr(""),
        llm_api_key=SecretStr("sk-ant-api03-KEYkeyKEYkeyKEYkey-abcd"),
    )
    assert report["model"] == "default"
    assert report["auth"]["method"] == AUTH_API_KEY
    assert report["auth"]["token_hint"] == "sk-ant-api03-…abcd"


def test_no_token_configured() -> None:
    report = run(ok_runner(), claude_code_oauth_token=SecretStr(""))
    assert report["auth"]["method"] == AUTH_NONE and report["auth"]["token_hint"] is None


def test_cli_missing_skips_the_prompt() -> None:
    runner = ok_runner(version=FileNotFoundError("claude"))
    report = run(runner)
    assert report["cli_version"] is None
    assert report["status"] == {
        "ok": False,
        "latency_ms": None,
        "model": None,
        "cost_usd": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "error": "Claude Code CLI not found",
    }
    assert report["account"] is None and report["account_note"]
    assert len(runner.calls) == 1


def test_auth_failure_reports_the_api_error_without_the_token() -> None:
    # What CLI 2.1.x prints (exit 1) for a rejected OAuth token; the token is echoed here on
    # purpose to prove it is scrubbed.
    failed = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 401,
        "result": f"Failed to authenticate. API Error: 401 OAuth access token is invalid. {TOKEN}",
        "total_cost_usd": 0,
        "modelUsage": {},
    }
    report = run(
        ok_runner(
            status=(0, json.dumps({"loggedIn": True, "authMethod": "oauth_token"}), ""),
            prompt=(1, json.dumps(failed), ""),
        )
    )
    status = report["status"]
    assert status["ok"] is False
    assert "HTTP 401" in status["error"] and "OAuth access token is invalid" in status["error"]
    assert TOKEN not in json.dumps(report)
    assert "SECRET" not in json.dumps(report)


def test_stderr_failure_is_scrubbed() -> None:
    report = run(ok_runner(prompt=(1, "", f"boom ANTHROPIC_API_KEY=sk-ant-api03-zzzz {TOKEN}")))
    assert report["status"]["ok"] is False
    assert "sk-ant-api03-zzzz" not in report["status"]["error"]
    assert TOKEN not in json.dumps(report)


def test_prompt_timeout() -> None:
    report = run(ok_runner(prompt=subprocess.TimeoutExpired(["claude"], PROMPT_TIMEOUT_S)))
    assert report["status"]["ok"] is False
    assert report["status"]["error"] == f"timed out after {PROMPT_TIMEOUT_S:g}s"


def test_auth_status_timeout_or_garbage_still_runs_the_prompt() -> None:
    for status in (subprocess.TimeoutExpired(["claude"], 10), (0, "not json", "")):
        report = run(ok_runner(status=status))
        assert report["account"] is None and "unknown" in report["account_note"]
        assert report["status"]["ok"] is True


def test_unimplemented_provider_runs_nothing() -> None:
    runner = ok_runner()
    report = run(runner, llm_provider="openai")
    assert report["status"]["ok"] is False and "not implemented" in report["status"]["error"]
    assert runner.calls == []


@pytest.mark.parametrize(
    ("token", "hint"),
    [
        ("", None),
        ("short", "…"),
        ("sk-ant-oat01-abcdefghijklmnop-1234", "sk-ant-oat01-…1234"),
        ("some-other-provider-key-123456", "…3456"),
    ],
)
def test_mask_token(token: str, hint: str | None) -> None:
    assert mask_token(token) == hint


def test_provider_error_keeps_api_status_and_reason() -> None:
    provider = ClaudeCodeProvider(
        runner=ok_runner(
            prompt=(
                1,
                json.dumps({"is_error": True, "api_error_status": 429, "result": "x" * 500}),
                "",
            )
        ),
        sleep=lambda s: None,
        max_retries=0,
    )
    with pytest.raises(LLMError) as excinfo:
        provider.review(PING)
    cause = str(excinfo.value.__cause__)
    assert cause.startswith("claude reported an error: None (HTTP 429): ") and len(cause) < 260


def test_token_cut_by_the_stderr_tail_is_still_scrubbed() -> None:
    # The provider keeps the last 300 characters of stderr; the cut lands inside the token.
    stderr = "x" * 1000 + TOKEN + "y" * (300 - len(TOKEN) // 2)
    report = run(ok_runner(prompt=(1, "", stderr)))
    assert report["status"]["ok"] is False
    assert "SECRET" not in report["status"]["error"] and "secret" not in report["status"]["error"]


@pytest.mark.parametrize(
    "stdout",
    [
        "[1, 2]",
        json.dumps({**PING_OK, "total_cost_usd": "lots"}),
    ],
)
def test_unreadable_result_is_an_error_not_an_exception(stdout: str) -> None:
    report = run(ok_runner(prompt=(0, stdout, "")))
    assert report["status"]["ok"] is False and report["status"]["error"]


def test_whole_check_fits_the_dashboard_proxy_timeout() -> None:
    assert VERSION_TIMEOUT_S + AUTH_STATUS_TIMEOUT_S + PROMPT_TIMEOUT_S <= 25
