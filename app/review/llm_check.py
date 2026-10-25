"""LLM connection check for the Admin screen: what is configured, which account, does it answer.

Outside the spec (requested by the user, see PROGRESS.md). Runs the same Claude Code CLI path as a
review (ClaudeCodeProvider) with one tiny prompt and no source code. Every subprocess has a short
timeout, and the configured token never leaves this module: only a masked hint is reported and
every error string is scrubbed of anything that looks like an Anthropic key.
"""

import json
import logging
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.review.providers.base import LLMError, ReviewPrompt
from app.review.providers.claude_code import ClaudeCodeProvider, Runner, scrub_secrets

log = logging.getLogger(__name__)

# The dashboard reaches the API through a Next.js rewrite, which gives up after 30 s: the three
# calls together stay well under that (a healthy ping takes about 4 s).
VERSION_TIMEOUT_S = 3.0
AUTH_STATUS_TIMEOUT_S = 3.0
PROMPT_TIMEOUT_S = 15.0

PING = ReviewPrompt(
    system='You are a connectivity check. Answer with the JSON object {"ok": true}.',
    user="ping",
    json_schema={
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
)

AUTH_STORED = "subscription session (set from the Admin screen)"
AUTH_OAUTH = "subscription session (CLAUDE_CODE_OAUTH_TOKEN)"
AUTH_API_KEY = "API key (LLM_API_KEY)"
AUTH_NONE = "none"

ACCOUNT_NOTE_FROM_ENV = (
    "The Claude Code CLI only reports email/organization/plan for an interactive login "
    "(`claude auth login`); with a token from the environment it knows the auth method only."
)


@dataclass
class AuthInfo:
    method: str  # one of AUTH_STORED / AUTH_OAUTH / AUTH_API_KEY / AUTH_NONE
    token_hint: str | None  # e.g. "sk-ant-oat01-…AbCd", never the token
    cli_method: str | None = None  # `claude auth status` authMethod: oauth_token, api_key, ...
    logged_in: bool | None = None


@dataclass
class AccountInfo:
    email: str | None = None
    organization: str | None = None
    subscription_type: str | None = None
    api_provider: str | None = None


@dataclass
class LiveStatus:
    ok: bool
    latency_ms: int | None = None
    model: str | None = None
    cost_usd: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None


@dataclass
class LLMCheckReport:
    provider: str
    model: str  # configured LLM_MODEL, or "default" (the CLI picks)
    cli_binary: str | None
    cli_version: str | None
    auth: AuthInfo
    account: AccountInfo | None
    account_note: str | None
    status: LiveStatus
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def mask_token(token: str) -> str | None:
    """`sk-ant-oat01-…AbCd`: the key family and the last 4 characters, enough to tell keys apart."""
    if not token:
        return None
    match = re.match(r"(sk-ant-[a-z]+\d*-)", token)
    prefix = match.group(1) if match else ""
    tail = token[-4:] if len(token) >= 20 else ""
    return f"{prefix}…{tail}"


def _auth_token(settings: Settings, stored: str) -> tuple[str, str]:
    """The token ClaudeCodeProvider gets (same precedence as build_provider) and its label."""
    if stored:
        return stored, AUTH_STORED
    oauth = settings.claude_code_oauth_token.get_secret_value()
    if oauth:
        return oauth, AUTH_OAUTH
    api_key = settings.llm_api_key.get_secret_value()
    if api_key:
        return api_key, AUTH_API_KEY
    return "", AUTH_NONE


def _describe(exc: BaseException, timeout_s: float) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"timed out after {timeout_s:g}s"
    if isinstance(exc, FileNotFoundError):
        return "Claude Code CLI not found"
    if isinstance(exc, OSError):
        return f"could not start the CLI ({exc.__class__.__name__})"
    return str(exc) or exc.__class__.__name__


def _version(provider: ClaudeCodeProvider, token: str) -> tuple[str | None, str | None]:
    """(version, error); the error is set when the CLI cannot be run at all."""
    try:
        completed = provider.run_command(["--version"], timeout_s=VERSION_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        return None, _describe(exc, VERSION_TIMEOUT_S)
    if completed.returncode != 0:
        return None, scrub_secrets(f"`--version` exited {completed.returncode}", token)
    return scrub_secrets(completed.stdout.strip(), token)[:100] or None, None


def _auth_status(provider: ClaudeCodeProvider) -> dict[str, Any] | None:
    """`claude auth status --json`; exits 1 when not logged in but still prints the JSON."""
    try:
        completed = provider.run_command(
            ["auth", "status", "--json"], timeout_s=AUTH_STATUS_TIMEOUT_S
        )
        data = json.loads(completed.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError):  # ValueError: bad JSON or bytes
        return None
    return data if isinstance(data, dict) else None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value else None


def _ping(provider: ClaudeCodeProvider, token: str, clock: Callable[[], float]) -> LiveStatus:
    started = clock()
    try:
        result = provider.review(PING)
    except (LLMError, ValueError, TypeError) as exc:
        # review() wraps the last attempt's error; that one carries the reason. ValueError and
        # TypeError: a result the provider could not read (bad numbers), reported by class only.
        cause = exc.__cause__ or exc
        if not isinstance(cause, LLMError | subprocess.TimeoutExpired | OSError):
            cause = LLMError(f"unreadable CLI result ({cause.__class__.__name__})")
        return LiveStatus(
            ok=False,
            latency_ms=round((clock() - started) * 1000),
            error=scrub_secrets(_describe(cause, PROMPT_TIMEOUT_S), token)[:400],
        )
    return LiveStatus(
        ok=True,
        latency_ms=round((clock() - started) * 1000),
        model=result.model,
        cost_usd=result.cost_usd,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )


def check_llm(
    settings: Settings,
    *,
    stored_token: str = "",
    runner: Runner = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
) -> LLMCheckReport:
    """Configuration, account and one live call; never raises for an LLM/CLI failure.

    `stored_token` is the token set from the Admin screen, if any (it wins, like in reviews)."""
    token, method = _auth_token(settings, stored_token)
    auth = AuthInfo(method=method, token_hint=mask_token(token))
    model = settings.llm_model or "default"
    if settings.llm_provider != "claude_code":
        return LLMCheckReport(
            provider=settings.llm_provider,
            model=model,
            cli_binary=None,
            cli_version=None,
            auth=auth,
            account=None,
            account_note=None,
            status=LiveStatus(
                ok=False, error=f"LLM_PROVIDER={settings.llm_provider} is not implemented yet"
            ),
        )

    provider = ClaudeCodeProvider(
        binary=settings.claude_code_bin,
        model=settings.llm_model,
        auth_token=token,
        timeout_s=PROMPT_TIMEOUT_S,
        max_retries=0,
        runner=runner,
    )
    version, cli_error = _version(provider, token)
    account: AccountInfo | None = None
    note: str | None = None
    if cli_error is not None:
        status = LiveStatus(ok=False, error=cli_error)
        note = "The CLI could not be run, so the account is unknown."
    else:
        info = _auth_status(provider)
        if info is None:
            note = "`claude auth status` gave no answer, so the account is unknown."
        else:
            auth.cli_method = _str_or_none(info.get("authMethod"))
            auth.logged_in = bool(info.get("loggedIn"))
            account = AccountInfo(
                email=_str_or_none(info.get("email")),
                organization=_str_or_none(info.get("orgName")),
                subscription_type=_str_or_none(info.get("subscriptionType")),
                api_provider=_str_or_none(info.get("apiProvider")),
            )
            if account.email is None and account.organization is None:
                account = None
                note = ACCOUNT_NOTE_FROM_ENV
        status = _ping(provider, token, clock)

    log.info(
        "llm check",
        extra={
            "llm_provider": settings.llm_provider,
            "ok": status.ok,
            "latency_ms": status.latency_ms,
            "auth_method": method,
        },
    )
    return LLMCheckReport(
        provider=settings.llm_provider,
        model=model,
        cli_binary=settings.claude_code_bin,
        cli_version=version,
        auth=auth,
        account=account,
        account_note=note,
        status=status,
    )
