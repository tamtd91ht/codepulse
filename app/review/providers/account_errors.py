"""Which LLM errors belong to the account that made the call (user 2026-11-09).

Only these move a review to another Claude account (app/review/llm_accounts.py): its login
failed, it used up its limit, or its plan cannot use the review model. Server errors,
overload, timeouts and network trouble are the same on every account, so switching would not
help and would make accounts flap; those keep the provider's own retries and the "review again
when the LLM works" flow (app/review/llm_recovery.py).

Only the CLI's structured error is read ("claude reported an error: … (HTTP 429): …", built
from an `is_error` result whose text is the API error, never model output). The stderr tail of
a crashed CLI ("claude exited 1: …") may echo the prompt, i.e. the diff, so a change that
mentions "rate_limit" or "model" must never park an account.
"""

import re
from datetime import UTC, datetime
from typing import Literal

# "rate": a burst limit (plain 429) that clears in seconds or minutes; "quota": the plan's usage
# limit, which lasts until its reset.
AccountFailure = Literal["auth", "quota", "rate", "model"]

STRUCTURED = "claude reported an error"

_MODEL = re.compile(
    r"not_found_error[^\n]{0,120}model|model[^\n]{0,80}(?:not (?:found|available|supported)"
    r"|does not exist|not have access|no access)|\(HTTP 404\)[^\n]{0,120}model",
    re.IGNORECASE,
)
_AUTH = re.compile(
    r"\(HTTP 40[13]\)|failed to authenticate|authentication_error|invalid (?:api key|x-api-key"
    r"|bearer|oauth)|oauth token (?:has )?(?:expired|been revoked)|please run /login"
    r"|permission_error",
    re.IGNORECASE,
)
_USAGE = re.compile(
    r"usage.?limit|limit reached|hit your limit|out of (?:extra )?usage|weekly limit"
    r"|credit balance|quota",
    re.IGNORECASE,
)
_RATE = re.compile(r"\(HTTP 429\)|rate_limit_error|rate.?limit|too many requests", re.IGNORECASE)


def structured_part(message: str) -> str | None:
    """The CLI's structured error inside an error text (and its causes joined by " | ")."""
    for part in message.split(" | "):
        if part.lstrip().startswith(STRUCTURED):
            return part.strip()
    return None


def account_error(message: str) -> tuple[AccountFailure, str] | None:
    """(why, the structured error) when the failure is the account's own; None otherwise."""
    part = structured_part(message)
    if part is None:
        return None
    if _MODEL.search(part):
        return "model", part
    if _AUTH.search(part):
        return "auth", part
    if _USAGE.search(part):
        return "quota", part
    if _RATE.search(part):
        return "rate", part
    return None


def classify_account_error(message: str) -> AccountFailure | None:
    found = account_error(message)
    return found[0] if found else None


# "Claude AI usage limit reached|1731150000": the reset as epoch seconds.
_RESET_EPOCH = re.compile(r"\|\s*(\d{10})\b")
# "resets at 2026-11-09T15:00:00Z" / "reset: 2026-11-09 15:00:00+07:00".
_RESET_ISO = re.compile(
    r"reset\w*[^0-9]{0,12}(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)",
    re.IGNORECASE,
)


def reset_time(message: str, now: datetime) -> datetime | None:
    """When a usage limit says it resets, if it says so in a form we can read; None otherwise.

    Ignored when it is in the past or more than 8 days ahead (a misread number)."""
    found: datetime | None = None
    if match := _RESET_EPOCH.search(message):
        found = datetime.fromtimestamp(int(match.group(1)), UTC)
    elif match := _RESET_ISO.search(message):
        text = match.group(1).replace(" ", "T").replace("Z", "+00:00")
        try:
            found = datetime.fromisoformat(text)
        except ValueError:
            found = None
        if found is not None and found.tzinfo is None:
            found = found.replace(tzinfo=UTC)
    if found is None or found <= now or (found - now).days > 8:
        return None
    return found
