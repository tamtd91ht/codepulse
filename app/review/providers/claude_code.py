"""LLM provider backed by the Claude Code CLI in print mode (Phase 0 decision: `claude -p`).

The diff is untrusted input, so the CLI runs with every tool disabled, no settings or session
files, and an empty working directory: the model can only answer with JSON.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.ratelimit import RateLimiter
from app.review.providers.base import LLMError, ReviewPrompt, ReviewResult

log = logging.getLogger(__name__)

# What the CLI subprocess call looks like; injected in tests.
Runner = Callable[..., subprocess.CompletedProcess[str]]


class ClaudeCodeProvider:
    name = "claude_code"

    def __init__(
        self,
        *,
        binary: str = "claude",
        model: str = "",
        auth_token: str = "",
        timeout_s: float = 120.0,
        max_retries: int = 3,
        max_per_minute: float = 30.0,
        runner: Runner = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._binary = binary
        self._model = model
        self._auth_token = auth_token
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._runner = runner
        self._sleep = sleep
        self._limiter = RateLimiter(max_per_minute / 60.0, sleep=sleep)

    def command(self, prompt: ReviewPrompt) -> list[str]:
        cmd = [
            self._binary,
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--system-prompt",
            prompt.system,
            "--json-schema",
            json.dumps(prompt.json_schema, separators=(",", ":")),
        ]
        if self._model:
            cmd += ["--model", self._model]
        return cmd

    def _env(self) -> Mapping[str, str]:
        env = dict(os.environ)
        if self._auth_token:
            # `claude setup-token` issues sk-ant-oat… tokens; anything else is an API key.
            key = (
                "CLAUDE_CODE_OAUTH_TOKEN"
                if self._auth_token.startswith("sk-ant-oat")
                else "ANTHROPIC_API_KEY"
            )
            env[key] = self._auth_token
        return env

    def _run_once(self, cmd: Sequence[str], stdin: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="codepulse-review-") as cwd:
            completed = self._runner(
                list(cmd),
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout_s,
                cwd=cwd,
                env=self._env(),
                check=False,
            )
        if completed.returncode != 0 and not completed.stdout.strip():
            # stderr may echo the prompt; keep only its tail and never the environment.
            raise LLMError(f"claude exited {completed.returncode}: {completed.stderr[-300:]}")
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError("claude returned non-JSON output") from exc
        if data.get("is_error") or data.get("subtype") != "success":
            raise LLMError(f"claude reported an error: {data.get('subtype')}")
        return dict(data)

    def review(self, prompt: ReviewPrompt) -> ReviewResult:
        cmd = self.command(prompt)
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            try:
                data = self._run_once(cmd, prompt.user)
                break
            except (LLMError, subprocess.TimeoutExpired, OSError) as exc:
                last = exc
                log.warning(
                    "claude call failed",
                    extra={"attempt": attempt + 1, "error": exc.__class__.__name__},
                )
                if attempt < self._max_retries:
                    self._sleep(min(2.0**attempt * 2, 30.0))
        else:
            raise LLMError(f"claude failed after {self._max_retries + 1} attempts") from last

        usage = data.get("usage") or {}
        prompt_tokens = sum(
            int(usage.get(k) or 0)
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        models = list((data.get("modelUsage") or {}).keys())
        output = data.get("structured_output")
        return ReviewResult(
            output=output if output is not None else data.get("result", ""),
            model=models[0] if models else (self._model or "claude"),
            prompt_tokens=prompt_tokens,
            completion_tokens=int(usage.get("output_tokens") or 0),
            cost_usd=float(data.get("total_cost_usd") or 0.0),
            raw={k: data.get(k) for k in ("session_id", "num_turns", "duration_ms")},
        )
