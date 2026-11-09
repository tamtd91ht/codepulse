"""Several Claude accounts: one default, backups only while it fails (user 2026-11-09).

The accounts live in `llm_accounts` (encrypted tokens, never returned). The order is the
default, then the backups by `position`; a token from the environment (CLAUDE_CODE_OAUTH_TOKEN,
then LLM_API_KEY) comes last, and with no token at all the CLI's own login is used.

When an account fails for a reason of its own (app/review/providers/account_errors.py) it is
skipped until `retry_at`:

- usage limit with a reset time in the message: that time (trying earlier cannot work);
- usage limit without one: the base interval (Admin > Cấu hình `llm_account_retry_min`, 15 min
  by default), doubling with each further failure, up to 8x;
- login failed: 8x the base (a revoked or expired token does not heal by itself; logging in
  again or "Kiểm tra" on the Admin screen clears it at once);
- model not in the plan: until the review model changes or an admin checks it again.

Nothing pings in the background: the first real call after `retry_at` tries the account again
(a failing login or limit answers within seconds), so the default comes back by itself and no
call is spent on probing. Failures are written in their own transaction and never raise: a
review must not fail because this bookkeeping did.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import SecretBox, secret_box
from app.db import get_sessionmaker
from app.models import LlmAccount
from app.review.providers.account_errors import AccountFailure, reset_time
from app.review.providers.failover import Account

log = logging.getLogger(__name__)

ENV_LABEL = "Biến môi trường"
CLI_LABEL = "Đăng nhập của CLI"
TOKEN_PREFIXES = ("sk-ant-oat", "sk-ant-api")
MAX_BACKOFF = 8  # x the base interval
RATE_PAUSE = timedelta(minutes=1)  # after a burst 429
LABEL_MAX = 80


# -- reading --------------------------------------------------------------------------------------


def _ordered(session: Session, *, enabled_only: bool) -> list[LlmAccount]:
    query = select(LlmAccount).order_by(
        LlmAccount.is_default.desc(), LlmAccount.position, LlmAccount.id
    )
    if enabled_only:
        query = query.where(LlmAccount.enabled)
    return list(session.scalars(query))


def account_rows(session: Session) -> list[LlmAccount]:
    """Every stored account, in the order calls try them (disabled ones too, for the screen)."""
    return _ordered(session, enabled_only=False)


def to_account(row: LlmAccount, box: SecretBox) -> Account:
    return Account(
        id=row.id,
        label=row.label,
        token=box.decrypt(row.token_encrypted),
        is_default=row.is_default,
        state=row.state,
        retry_at=row.retry_at,
        blocked_model=row.blocked_model,
        set_by=row.set_by,
    )


def env_accounts(settings: Settings, taken: set[str] | None = None) -> list[Account]:
    """The environment's token as a last backup (not when an account already holds it)."""
    token = (
        settings.claude_code_oauth_token.get_secret_value()
        or settings.llm_api_key.get_secret_value()
    )
    if not token or token in (taken or set()):
        return []
    return [Account(id=None, label=ENV_LABEL, token=token)]


def accounts(session: Session, box: SecretBox, settings: Settings) -> list[Account]:
    """What a call tries, in order: enabled stored accounts, then the environment's token; the
    CLI's own login when there is neither."""
    stored = [to_account(r, box) for r in _ordered(session, enabled_only=True)]
    found = stored + env_accounts(settings, {a.token for a in stored})
    return found or [Account(id=None, label=CLI_LABEL, token="")]


def load_accounts(settings: Settings) -> list[Account]:
    """For workers: as `accounts`, in a session of its own. Never raises: with the database
    unreadable the environment's token (or the CLI login) still lets reviews run."""
    try:
        with get_sessionmaker()() as session:
            return accounts(session, secret_box(), settings)
    except Exception as exc:
        log.warning("llm accounts unavailable", extra={"error": exc.__class__.__name__})
        return env_accounts(settings) or [Account(id=None, label=CLI_LABEL, token="")]


def active(found: list[Account], model: str, now: datetime | None = None) -> Account | None:
    """The account the next call would use."""
    when = now or datetime.now(UTC)
    return next((a for a in found if a.usable(when, model)), None)


def current_token(session: Session, box: SecretBox, settings: Settings) -> str:
    """For one-shot users of a single token (Stetho, the evidence draft, commands, the startup
    check): the account a review would use now, else the first one ("" for the CLI login)."""
    found = accounts(session, box, settings)
    model = review_model(settings)
    chosen = active(found, model) or found[0]
    return chosen.token


def load_current_token(settings: Settings) -> str:
    """As `current_token`, in a session of its own; never raises."""
    try:
        with get_sessionmaker()() as session:
            return current_token(session, secret_box(), settings)
    except Exception as exc:
        log.warning("llm accounts unavailable", extra={"error": exc.__class__.__name__})
        found = env_accounts(settings)
        return found[0].token if found else ""


def review_model(settings: Settings) -> str:
    """The one model every account answers with: Admin > Cấu hình, else the default. Pinned on
    every call, so no account falls back to its plan's own default model."""
    from app.admin_settings import DEFAULT_REVIEW_MODEL

    return settings.llm_model.strip() or DEFAULT_REVIEW_MODEL


# -- when to try again ----------------------------------------------------------------------------


def retry_minutes(session: Session, settings: Settings) -> int:
    from app.admin_settings import effective

    try:
        return int(effective(session, settings)["llm_account_retry_min"])
    except Exception:
        return settings.llm_account_retry_min


def next_retry(
    kind: AccountFailure, failures: int, base_min: int, now: datetime, message: str
) -> datetime | None:
    """When a failed account is tried again; None: not by itself (state "model").

    `failures` counts this one too (1 for the first)."""
    if kind == "model":
        return None
    if kind == "rate":
        # A 429 that did not say it was the plan's limit: a short pause the first time; when it
        # is back right after, it is the usage limit (which also answers 429), backing off.
        if failures <= 1:
            return now + RATE_PAUSE
        failures -= 1
    longest = timedelta(minutes=base_min * MAX_BACKOFF)
    if kind == "auth":
        return now + longest
    if reset := reset_time(message, now):
        return reset + timedelta(minutes=1)
    step = timedelta(minutes=base_min * 2 ** max(failures - 1, 0))
    return now + min(step, longest)


# -- recording what happened ----------------------------------------------------------------------

REASON = {
    "quota": "hết hạn mức",
    "rate": "bị giới hạn tốc độ",
    "auth": "đăng nhập không còn hiệu lực",
    "model": "gói không dùng được model review",
}


def record_failure(
    account: Account, kind: AccountFailure, message: str, settings: Settings, model: str
) -> None:
    """Skip `account` until its next try; tell whoever set it up the first time it fails."""
    if account.id is None:
        log.warning("llm account failed", extra={"account": account.label, "kind": kind})
        return
    try:
        with get_sessionmaker()() as session:
            row = session.get(LlmAccount, account.id, with_for_update=True)
            if row is None:
                return
            now = datetime.now(UTC)
            parked = (row.retry_at is not None and row.retry_at > now) or (
                row.state == "model" and row.blocked_model == model
            )
            if row.state != "ok" and parked:
                # Another worker that loaded the list before it failed recorded it already:
                # one failure, one step of backoff.
                session.commit()
                return
            was_ok = row.state == "ok"
            row.failures = (0 if was_ok else row.failures) + 1
            row.state = "quota" if kind == "rate" else kind
            row.retry_at = next_retry(
                kind, row.failures, retry_minutes(session, settings), now, message
            )
            row.blocked_model = model if kind == "model" else None
            row.last_error = message[:500]
            row.last_failed_at = now
            row.last_used_at = now
            # A first 429 may be a burst: told only once it comes back (then it is the limit).
            if (was_ok and kind != "rate") or (kind == "rate" and row.failures == 2):
                _tell(session, row, "quota" if kind == "rate" else kind)
            session.commit()
        log.warning(
            "llm account failed, using the next one",
            extra={"account_id": account.id, "kind": kind, "retry_at": str(row.retry_at)},
        )
    except Exception as exc:
        log.warning("llm account failure not recorded", extra={"error": exc.__class__.__name__})


def _tell(session: Session, row: LlmAccount, kind: AccountFailure) -> None:
    from app.notifications.inbox import notify

    action = (
        "Đăng nhập lại tài khoản này ở Admin > LLM."
        if kind == "auth"
        else "Đổi model review hoặc dùng tài khoản khác."
        if kind == "model"
        else "Sẽ tự thử lại; trong lúc đó review chạy bằng tài khoản dự phòng."
    )
    notify(
        session,
        [row.set_by],
        kind="llm_account",
        title=f"Tài khoản Claude {row.label}: {REASON[kind]}",
        body=action,
        link="/admin#llm",
        tone="bad" if kind != "quota" else "warn",
        ref=f"llm_account:{row.id}",
    )


def record_success(account: Account, started: datetime | None = None) -> None:
    """The account answered: usable again, and when it was last used (written at most once a
    minute per account, so a busy worker does not update the row on every call).

    A call that started before another worker saw this account fail (a long deep review) does
    not clear that failure: only failures older than `started` are."""
    if account.id is None:
        return
    try:
        with get_sessionmaker()() as session:
            now = datetime.now(UTC)
            since = started or now
            session.execute(
                update(LlmAccount)
                .where(LlmAccount.id == account.id, LlmAccount.state == "ok")
                .where(
                    LlmAccount.last_used_at.is_(None)
                    | (LlmAccount.last_used_at < now - timedelta(minutes=1))
                )
                .values(last_ok_at=now, last_used_at=now)
                .execution_options(synchronize_session=False)
            )
            session.execute(
                update(LlmAccount)
                .where(LlmAccount.id == account.id, LlmAccount.state != "ok")
                .where(LlmAccount.last_failed_at.is_(None) | (LlmAccount.last_failed_at < since))
                .values(
                    state="ok",
                    failures=0,
                    retry_at=None,
                    blocked_model=None,
                    last_error=None,
                    last_ok_at=now,
                    last_used_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            session.commit()
        if account.state != "ok":
            log.info("llm account works again", extra={"account_id": account.id})
    except Exception as exc:
        log.warning("llm account success not recorded", extra={"error": exc.__class__.__name__})


# -- managing accounts (Admin > LLM) --------------------------------------------------------------


def check_token(token: str) -> str:
    token = token.strip()
    if not token.startswith(TOKEN_PREFIXES):
        raise ValueError(
            "not a Claude token (sk-ant-oat… from `claude setup-token`, or sk-ant-api…)"
        )
    return token


def add_account(
    session: Session, box: SecretBox, *, label: str, token: str, source: str, user_id: int | None
) -> LlmAccount:
    """A new account: the default when it is the first, otherwise the last backup."""
    token = check_token(token)
    has_default = session.scalar(select(func.count()).where(LlmAccount.is_default)) or 0
    last = session.scalar(select(func.max(LlmAccount.position))) or 0
    row = LlmAccount(
        label=(label.strip() or "Tài khoản Claude")[:LABEL_MAX],
        token_encrypted=box.encrypt(token),
        source=source,
        set_by=user_id,
        is_default=not has_default,
        position=last + 1,
        enabled=True,
        state="ok",
        failures=0,
    )
    session.add(row)
    session.flush()
    return row


def replace_token(
    session: Session,
    box: SecretBox,
    row: LlmAccount,
    *,
    token: str,
    source: str,
    user_id: int | None,
) -> None:
    """A new login for an existing account: usable again at once."""
    row.token_encrypted = box.encrypt(check_token(token))
    row.source = source
    row.set_by = user_id
    reset_state(row)
    session.flush()


def reset_state(row: LlmAccount) -> None:
    row.state = "ok"
    row.failures = 0
    row.retry_at = None
    row.blocked_model = None
    row.last_error = None


def set_default(session: Session, row: LlmAccount) -> None:
    """Make `row` the default; the old default becomes the first backup."""
    if row.is_default:
        return
    old = session.scalar(select(LlmAccount).where(LlmAccount.is_default))
    if old is not None:
        old.is_default = False
        old.position = min(r.position for r in account_rows(session)) - 1
        session.flush()  # the unique index allows one default at a time
    row.is_default = True
    row.enabled = True
    session.flush()


def reorder(session: Session, ids: list[int]) -> None:
    """Backups in this order (ids not listed keep their place after them)."""
    rows = {r.id: r for r in account_rows(session)}
    for position, account_id in enumerate(ids, start=1):
        if account_id in rows:
            rows[account_id].position = position
    rest = [r for r in rows.values() if r.id not in ids]
    for offset, r in enumerate(sorted(rest, key=lambda r: r.position), start=len(ids) + 1):
        r.position = offset
    session.flush()


def remove_account(session: Session, row: LlmAccount) -> None:
    """Delete it; when it was the default, the first enabled backup takes over."""
    was_default = row.is_default
    session.delete(row)
    session.flush()
    if was_default:
        nxt = next((r for r in account_rows(session) if r.enabled), None)
        if nxt is not None:
            nxt.is_default = True
            session.flush()


def describe(row: LlmAccount, box: SecretBox, now: datetime, model: str) -> dict[str, Any]:
    """What the Admin screen shows of one account (never the token)."""
    from app.review.llm_check import mask_token

    try:
        hint = mask_token(box.decrypt(row.token_encrypted))
    except Exception:
        hint = None
    account = to_account(row, box) if hint is not None else None
    return {
        "id": row.id,
        "label": row.label,
        "token_hint": hint,
        "source": row.source,
        "set_by": row.set_by,
        "is_default": row.is_default,
        "position": row.position,
        "enabled": row.enabled,
        "state": row.state,
        "usable": bool(row.enabled and account and account.usable(now, model)),
        "retry_at": row.retry_at,
        "failures": row.failures,
        "last_error": row.last_error,
        "blocked_model": row.blocked_model,
        "last_used_at": row.last_used_at,
        "last_ok_at": row.last_ok_at,
        "last_failed_at": row.last_failed_at,
        "updated_at": row.updated_at,
    }
