"""One review call over several Claude accounts: the default first, backups only while it fails
(user 2026-11-09; the accounts and their state live in app/review/llm_accounts.py).

Every call walks the accounts in order and uses the first one that is usable: an account that
failed is skipped until its `retry_at`, and once that time has come the next real call tries it
again (there is no background ping). Success there brings the default back at once. Only an
account-level failure (login, usage limit, model not in the plan:
app/review/providers/account_errors.py) moves on to the next account; any other error is raised
as it is, because another account would fail the same way.

What the model sees does not depend on the account: every call is stateless (no session, no
settings or CLAUDE.md of the account, `--no-session-persistence`) and pinned to one model, so a
review reads the same whichever account answered it.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.ratelimit import Limiter
from app.review.providers.account_errors import AccountFailure, account_error
from app.review.providers.base import LLMError, ReviewPrompt, ReviewResult
from app.review.providers.claude_code import ClaudeCodeProvider, EventSink


@dataclass(frozen=True)
class Account:
    """A credential reviews may run on. `id` None: not stored (the environment token, or the
    CLI's own login when there is no token at all), so its failures are not recorded."""

    id: int | None
    label: str
    token: str
    is_default: bool = False
    state: str = "ok"
    retry_at: datetime | None = None
    blocked_model: str | None = None
    set_by: int | None = None

    def usable(self, now: datetime, model: str) -> bool:
        if self.state == "ok":
            return True
        if self.state == "model":  # another review model may be in the plan
            return self.blocked_model != model
        return self.retry_at is not None and self.retry_at <= now


# (account, why, the structured API error) -> None; (account, when the call started) -> None.
# Neither may raise.
OnFailure = Callable[[Account, AccountFailure, str], None]
OnSuccess = Callable[[Account, datetime], None]


def error_text(exc: BaseException) -> str:
    """The error and its causes: `review()` wraps the last attempt, which carries the reason."""
    parts: list[str] = []
    seen: BaseException | None = exc
    while seen is not None and len(parts) < 4:
        parts.append(str(seen))
        seen = seen.__cause__
    return " | ".join(p for p in parts if p)


def unavailable_message(accounts: Sequence[Account], now: datetime) -> str:
    """Why no account can take a call; worded so app/review/llm_recovery.py sees an outage."""
    reasons = {"quota": "usage limit", "auth": "authentication failed", "model": "model"}
    parts = []
    for a in accounts:
        why = reasons.get(a.state, a.state)
        when = f" until {a.retry_at:%H:%M} UTC" if a.retry_at and a.retry_at > now else ""
        parts.append(f"{a.label}: {why}{when}")
    return "llm unavailable: every Claude account is unavailable (" + "; ".join(parts) + ")"


class FailoverProvider:
    """Looks like one ClaudeCodeProvider (review, review_in_repo, limiter)."""

    name = "claude_code"

    def __init__(
        self,
        *,
        accounts: Callable[[], list[Account]],
        make: Callable[[str], ClaudeCodeProvider],
        model: str,
        limiter: Limiter,
        on_failure: OnFailure,
        on_success: OnSuccess,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """`accounts` is read on every call, so a new login or a state another worker wrote
        applies at once; `make` builds the CLI provider for a token (same model and limiter)."""
        self._accounts = accounts
        self._make = make
        self._model = model
        self._limiter = limiter
        self._on_failure = on_failure
        self._on_success = on_success
        self._clock = clock

    @property
    def limiter(self) -> Limiter:
        return self._limiter

    @property
    def model(self) -> str:
        return self._model

    def review(self, prompt: ReviewPrompt) -> ReviewResult:
        return self._run(lambda p: p.review(prompt))

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
        return self._run(
            lambda p: p.review_in_repo(
                prompt,
                workdir,
                tools=tools,
                max_turns=max_turns,
                timeout_s=timeout_s,
                on_event=on_event,
            )
        )

    def candidates(self) -> list[Account]:
        """The accounts a call would try now, in order."""
        now = self._clock()
        return [a for a in self._accounts() if a.usable(now, self._model)]

    def _run(self, call: Callable[[ClaudeCodeProvider], ReviewResult]) -> ReviewResult:
        accounts = self._accounts()
        now = self._clock()
        usable = [a for a in accounts if a.usable(now, self._model)]
        if not usable:
            raise LLMError(unavailable_message(accounts, now))
        failed: list[str] = []
        for account in usable:
            started = self._clock()
            try:
                result = call(self._make(account.token))
            except LLMError as exc:
                found = account_error(error_text(exc))
                if found is None:
                    raise  # not this account's fault: another one would fail the same way
                kind, detail = found
                self._on_failure(account, kind, detail)
                failed.append(f"{account.label}: {detail[:300]}")
                continue
            self._on_success(account, started)
            raw: dict[str, Any] = dict(result.raw)
            raw["llm_account"] = account.label
            raw["llm_account_id"] = account.id
            result.raw = raw
            return result
        raise LLMError("llm unavailable: every Claude account failed: " + " || ".join(failed))
