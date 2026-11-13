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
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# "rate": a burst limit (plain 429) that clears in seconds or minutes; "quota": the plan's usage
# limit, which lasts until its reset.
AccountFailure = Literal["auth", "quota", "rate", "model"]

STRUCTURED = "claude reported an error"

# The CLI's `api_error` / the server's `api_error_code` (claude_code._error_message adds them as
# "[api_error=…]"): a name says more than the wording, which changes between CLI versions
# (2026-11-13: "You've hit your Fable limit", "Opus 5 requires usage credits" were not read as
# the usage limit, so reviews failed instead of moving to the backup account).
_CODE = re.compile(r"\[(api_error|api_error_code)=([a-z0-9_]+)\]")
_CODE_KIND: dict[str, AccountFailure] = {
    "usage_limit_reached": "quota",
    "model_requires_usage_credits": "quota",
    "long_context_credits_required": "quota",
    "credit_balance_low": "quota",
    "stored_login_refused": "auth",
    "api_key_auth_disabled": "auth",
    "org_disabled_credential": "auth",
    "invalid_credential_header": "auth",
    "provider_credentials": "auth",
    "gateway_signin_required": "auth",
    "gateway_session_expired": "auth",
}
# A server code (`api_error_code`) this build has no entry for, named like a usage limit or a
# login. Not the CLI's `api_error`: that list is known, and its other names (max_output_tokens,
# unattended_start_limit…) are not the account's.
_CODE_QUOTA = re.compile(
    r"usage_limit|quota|credits?_|_credits?\b|budget|spend_limit|billing|overage"
)
_CODE_AUTH = re.compile(r"oauth|login|credential|unauthori[sz]ed|auth_")

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
# The plan's usage limit. It also comes back as HTTP 429 / rate_limit_error, so a 429 is read
# for these first: a reset time, or wording about the plan's 5-hour / weekly / model limits.
_USAGE = re.compile(
    r"usage.?limit|limit reached|limit exceeded for|hit your limit|reached your (?:\S+ )?limit"
    r"|out of (?:extra )?usage|(?:5|five).?hour|session limit|weekly|opus limit|sonnet limit"
    r"|subscription|your plan|credit balance|quota|\|\s*\d{10}\b|\bresets?\b"
    # Claude CLI 2.1 wording: "You've hit your Fable limit / monthly spend limit / team's shared
    # budget", "Opus 5 requires usage credits", "You're out of usage credits", "Your seat type
    # doesn't include usage", "Your usage allocation has been disabled", "This service is
    # disabled for your org".
    r"|hit your [^\n]{0,40}?(?:limit|budget)|usage credits?|spend limit|shared budget"
    r"|out of usage|seat type|usage allocation|disabled for your org",
    re.IGNORECASE,
)
# A burst limit that clears in seconds: requests or tokens per minute, concurrency.
_BURST = re.compile(
    r"per.?minute|requests per|tokens per|\brpm\b|\btpm\b|acceleration limit|concurren"
    r"|(?:retry after|resets? in|try again in) \d+ ?(?:s\b|sec)",
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
    codes = _CODE.findall(part)
    for _, code in codes:
        if kind := _CODE_KIND.get(code):
            return kind, part
    for key, code in codes:
        if key != "api_error_code":
            continue
        if _CODE_AUTH.search(code):
            return "auth", part
        if _CODE_QUOTA.search(code):
            return "quota", part
    if _MODEL.search(part):
        return "model", part
    if _AUTH.search(part):
        return "auth", part
    if _USAGE.search(part) and not _BURST.search(part):
        return "quota", part
    if _RATE.search(part) or _USAGE.search(part):
        # A burst, or a 429 that does not say which: retried once, then a short pause; if it
        # keeps coming back it is treated as the usage limit (app/review/llm_accounts.py).
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


# "resets 3pm (Asia/Ho_Chi_Minh)" / "resets at 15:30 (Europe/Berlin)": a time of day in a
# named zone (without a zone the hour is ambiguous, so it is not read).
_RESET_CLOCK = re.compile(
    r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*\(([A-Za-z_]+(?:/[A-Za-z_+-]+)+|UTC)\)",
    re.IGNORECASE,
)


def _clock_reset(match: re.Match[str], now: datetime) -> datetime | None:
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    half = (match.group(3) or "").lower()
    if half == "pm" and hour < 12:
        hour += 12
    elif half == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    try:
        zone = ZoneInfo(match.group(4)) if match.group(4).upper() != "UTC" else UTC
    except (ZoneInfoNotFoundError, ValueError):
        return None
    local = now.astimezone(zone)
    at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= local:
        at += timedelta(days=1)
    return at.astimezone(UTC)


def reset_time(message: str, now: datetime) -> datetime | None:
    """When a usage limit says it resets, if it says so in a form we can read; None otherwise.

    Ignored when it is in the past or more than 8 days ahead (a misread number)."""
    found: datetime | None = None
    if match := _RESET_EPOCH.search(message):
        found = datetime.fromtimestamp(int(match.group(1)), UTC)
    elif clock := _RESET_CLOCK.search(message):
        found = _clock_reset(clock, now)
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
