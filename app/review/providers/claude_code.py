"""LLM provider backed by the Claude Code CLI in print mode (Phase 0 decision: `claude -p`).

The diff is untrusted input, so the CLI runs with every tool disabled, no settings or session
files, and an empty working directory: the model can only answer with JSON.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.ratelimit import Limiter, RateLimiter
from app.review.providers.base import LLMError, ReviewPrompt, ReviewResult

log = logging.getLogger(__name__)

# Calls per minute to the CLI, across every worker when the limiter is the shared one
# (docs/specs/19 §4.6; app/ratelimit.py).
LLM_MAX_PER_MINUTE = 30

# What the CLI subprocess call looks like; injected in tests.
Runner = Callable[..., subprocess.CompletedProcess[str]]
# One stream-json event from the CLI (`--output-format stream-json --verbose`).
EventSink = Callable[[dict[str, Any]], None]


def stream_lines(
    cmd: Sequence[str],
    *,
    stdin: str,
    cwd: str,
    env: Mapping[str, str],
    timeout_s: float,
    on_line: Callable[[str], None],
) -> tuple[int, str]:
    """Run the CLI and hand every stdout line over as it arrives: (exit code, stderr).

    A watchdog kills the process at the deadline (TimeoutExpired is raised afterwards)."""
    proc = subprocess.Popen(  # noqa: S603 - the configured CLI, argv list, no shell
        list(cmd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=dict(env),
    )
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        proc.kill()

    watchdog = threading.Timer(timeout_s, kill)
    watchdog.daemon = True
    watchdog.start()
    assert proc.stdin and proc.stdout and proc.stderr  # noqa: S101 - the PIPEs above
    err_pipe = proc.stderr
    stderr: list[str] = []
    drain = threading.Thread(target=lambda: stderr.append(err_pipe.read()), daemon=True)
    drain.start()
    try:
        try:
            proc.stdin.write(stdin)
            proc.stdin.close()
        except OSError:
            pass  # the CLI died before reading; its exit code and stderr say why
        for line in proc.stdout:
            on_line(line)
        proc.wait()
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        drain.join(timeout=5)
    if timed_out.is_set():
        raise subprocess.TimeoutExpired(list(cmd), timeout_s)
    return proc.returncode, "".join(stderr)


_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")
# Variables the claude subprocess may see (upper-cased names). Everything else stays home.
ENV_PASSTHROUGH = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
        "TERM",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "CLAUDE_CONFIG_DIR",
        "ANTHROPIC_BASE_URL",
        "DISABLE_TELEMETRY",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    }
)


def scrub_secrets(text: str, secret: str = "") -> str:
    """Drop the configured token and anything shaped like an Anthropic key from CLI output."""
    if secret:
        text = text.replace(secret, "***")
    return _KEY_RE.sub("sk-ant-***", text)


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
        max_per_minute: float = LLM_MAX_PER_MINUTE,
        runner: Runner = subprocess.run,
        streamer: Callable[..., tuple[int, str]] = stream_lines,
        sleep: Callable[[float], None] = time.sleep,
        limiter: Limiter | None = None,
    ) -> None:
        """`limiter` paces the calls; by default this process alone (`RateLimiter` at
        `max_per_minute`), in the workers the Redis-backed one every worker shares
        (app.review.providers.build_provider)."""
        self._binary = binary
        self._model = model
        self._auth_token = auth_token
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._runner = runner
        self._streamer = streamer
        self._sleep = sleep
        self._limiter: Limiter = limiter or RateLimiter(max_per_minute / 60.0, sleep=sleep)

    @property
    def limiter(self) -> Limiter:
        return self._limiter

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

    def repo_command(
        self, prompt: ReviewPrompt, tools: Sequence[str], max_turns: int, *, stream: bool = False
    ) -> list[str]:
        """Deep review (docs/specs/18): only the named read-only tools, no settings from the
        checkout (its CLAUDE.md is data in the prompt, never an instruction), bounded turns."""
        allowed = ",".join(tools)
        # No --allowedTools on purpose: inside the working directory Read/Grep/Glob need no
        # approval, outside it they hit a permission prompt that print mode answers "no".
        # Auto-approving Read would let an injected instruction read /proc/self/environ or
        # ~/.claude credentials and echo them into the review text.
        cmd = [
            self._binary,
            "-p",
            "--output-format",
            *(["stream-json", "--verbose"] if stream else ["json"]),
            "--tools",
            allowed,
            "--disallowedTools",
            "Bash,Write,Edit,MultiEdit,NotebookEdit,WebFetch,WebSearch,Task,Agent",
            "--max-turns",
            str(max_turns),
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--system-prompt",
            prompt.system,
            "--json-schema",
            json.dumps(prompt.json_schema, separators=(",", ":")),
        ]
        if self._model:
            cmd += ["--model", self._model]
        return cmd

    def review_in_repo(
        self,
        prompt: ReviewPrompt,
        workdir: str,
        *,
        tools: Sequence[str],
        max_turns: int,
        timeout_s: float,
        on_event: EventSink | None = None,
    ) -> ReviewResult:
        """One attempt (a deep pass is expensive); the caller decides whether to fall back.

        With `on_event` the CLI streams (stream-json) and every event is handed over as it
        arrives, for the live view; the result is the same either way."""
        cmd = self.repo_command(prompt, tools, max_turns, stream=on_event is not None)
        self._limiter.acquire()
        try:
            if on_event is None:
                data = self._run_once(cmd, prompt.user, cwd=workdir, timeout_s=timeout_s)
            else:
                data = self._stream_once(cmd, prompt.user, workdir, timeout_s, on_event)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise LLMError(f"claude deep review: {exc.__class__.__name__}") from None
        return self._result(data)

    def _stream_once(
        self, cmd: Sequence[str], stdin: str, cwd: str, timeout_s: float, on_event: EventSink
    ) -> dict[str, Any]:
        result: dict[str, Any] | None = None

        def on_line(line: str) -> None:
            nonlocal result
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return
            if not isinstance(event, dict):
                return
            if event.get("type") == "result":
                result = event
            try:
                on_event(event)
            except Exception:  # the live view is a bonus: never break the review over it
                log.warning("live review event not handled", exc_info=True)

        code, stderr = self._streamer(
            cmd, stdin=stdin, cwd=cwd, env=self._env(), timeout_s=timeout_s, on_line=on_line
        )
        if result is None:
            tail = scrub_secrets(stderr, self._auth_token)[-300:]
            raise LLMError(f"claude exited {code} without a result: {tail}")
        return self._checked(result)

    def _env(self) -> Mapping[str, str]:
        """A minimal environment: the CLI gets what it needs to run and authenticate, not the
        worker's DATABASE_URL / secrets (the model's text is stored and posted to GitLab)."""
        env = {k: v for k, v in os.environ.items() if k.upper() in ENV_PASSTHROUGH}
        if self._auth_token:
            # `claude setup-token` issues sk-ant-oat… tokens; anything else is an API key.
            key = (
                "CLAUDE_CODE_OAUTH_TOKEN"
                if self._auth_token.startswith("sk-ant-oat")
                else "ANTHROPIC_API_KEY"
            )
            env[key] = self._auth_token
        return env

    def _run_once(
        self,
        cmd: Sequence[str],
        stdin: str,
        cwd: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        if cwd is None:
            with tempfile.TemporaryDirectory(prefix="codepulse-review-") as empty:
                return self._run_once(cmd, stdin, cwd=empty, timeout_s=timeout_s)
        completed = self._runner(
            list(cmd),
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s or self._timeout_s,
            cwd=cwd,
            env=self._env(),
            check=False,
        )
        if completed.returncode != 0 and not completed.stdout.strip():
            # stderr may echo the prompt; keep only its tail, scrubbed before it is cut.
            tail = scrub_secrets(completed.stderr, self._auth_token)[-300:]
            raise LLMError(f"claude exited {completed.returncode}: {tail}")
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError("claude returned non-JSON output") from exc
        return self._checked(data)

    def _checked(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise LLMError("claude returned JSON that is not an object")
        if data.get("is_error") or data.get("subtype") != "success":
            raise LLMError(scrub_secrets(_error_message(data), self._auth_token))
        return dict(data)

    def run_command(
        self, args: Sequence[str], *, timeout_s: float
    ) -> subprocess.CompletedProcess[str]:
        """Run another CLI command (`--version`, `auth status`) with the same auth and sandbox."""
        with tempfile.TemporaryDirectory(prefix="codepulse-review-") as cwd:
            return self._runner(
                [self._binary, *args],
                input="",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                cwd=cwd,
                env=self._env(),
                check=False,
            )

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
        return self._result(data)

    @staticmethod
    def _result(data: Mapping[str, Any]) -> ReviewResult:
        usage = data.get("usage") or {}
        prompt_tokens = sum(
            int(usage.get(k) or 0)
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        models = list((data.get("modelUsage") or {}).keys())
        output = data.get("structured_output")
        return ReviewResult(
            output=output if output is not None else data.get("result", ""),
            model=models[0] if models else "claude",
            prompt_tokens=prompt_tokens,
            completion_tokens=int(usage.get("output_tokens") or 0),
            cost_usd=float(data.get("total_cost_usd") or 0.0),
            raw={k: data.get(k) for k in ("session_id", "num_turns", "duration_ms")},
        )


def _error_message(data: Mapping[str, Any]) -> str:
    """Why the CLI gave up, e.g. `(HTTP 401): Failed to authenticate...`, kept short.

    The caller scrubs it; the cut keeps the start of `result`, so a key there still matches.

    `result` is only read for `is_error` results, where it holds the API error and not model text.
    """
    message = f"claude reported an error: {data.get('subtype')}"
    if status := data.get("api_error_status"):
        message += f" (HTTP {status})"
    if data.get("is_error") and (detail := str(data.get("result") or "").strip()):
        message += f": {detail[:200]}"
    return message
