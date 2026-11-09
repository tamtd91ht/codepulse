"""LLM connection check and the Claude login for admins (outside the spec, requested by the
user; see PROGRESS.md)."""

import logging
import os
import subprocess
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin_settings import monthly_budget_usd, with_overrides
from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.schemas import (
    LlmAccountIn,
    LlmAccountOrderIn,
    LlmAccountOut,
    LlmAccountPatch,
    LlmAccountsOut,
    LlmAccountTokenIn,
    LlmLoginCodeIn,
    LlmLoginOut,
    LlmTokenIn,
    LlmTokenOut,
)
from app.api.security import Principal, require
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.models import LlmAccount, Review, User
from app.review import llm_accounts as accts
from app.review.claude_login import (
    LOGIN_TTL_S,
    SESSIONS,
    LoginError,
    LoginSessions,
    LoginUnavailable,
    PtySetupToken,
    SetupTokenFactory,
)
from app.review.llm_check import check_llm, mask_token
from app.review.providers.account_errors import account_error
from app.review.providers.claude_code import Runner

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])
DEFAULT_LOCK = 7_310_201  # pg advisory lock: changes of the default Claude account
LlmAdmin = Annotated[Principal, require("admin.llm")]  # docs/specs/21 B4


def get_llm_runner() -> Runner:
    """The subprocess runner for the CLI; overridden in tests."""
    return subprocess.run


def month_usage(session: Session, budget_usd: float) -> dict[str, Any]:
    """This month's reviews and spend, counted like GET /admin/llm-costs."""
    month = func.date_trunc("month", Review.started_at)
    cost, reviews = session.execute(
        select(func.coalesce(func.sum(Review.cost_usd), 0), func.count())
        .where(Review.cost_usd.is_not(None))
        .where(month == func.date_trunc("month", func.now()))
    ).one()
    cost_usd = float(cost or 0)
    return {
        "reviews": int(reviews),
        "cost_usd": cost_usd,
        "budget_usd": budget_usd,
        "budget_pct": round(cost_usd / budget_usd * 100, 1) if budget_usd > 0 else None,
    }


@router.post("/admin/llm-check")
def llm_check(
    principal: LlmAdmin,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    runner: Annotated[Runner, Depends(get_llm_runner)],
    box: Annotated[SecretBox, Depends(get_secret_box)],
) -> dict[str, Any]:
    """Configured provider/model/CLI, auth method and account, one live call, month usage.

    POST because it makes a real (tiny) LLM call that is billed. The token is never returned.
    """
    tuned = with_overrides(session, settings)
    found = accts.accounts(session, box, tuned)
    chosen = accts.active(found, accts.review_model(tuned)) or found[0]
    report = check_llm(
        tuned,
        stored_token=chosen.token if chosen.id is not None else "",
        account_label=chosen.label if chosen.id is not None else None,
        runner=runner,
    )
    audit(
        session,
        principal.user_id,
        "llm.check",
        f"llm:{report.provider}",
        ok=report.status.ok,
        latency_ms=report.status.latency_ms,
    )
    session.commit()
    usage = month_usage(session, monthly_budget_usd(session, settings))
    return {**report.to_dict(), "usage": usage}


# -- Claude subscription token: "Đăng nhập Claude" or a pasted `claude setup-token` token --------


def get_login_sessions() -> LoginSessions:
    return SESSIONS


def get_setup_token_factory(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SetupTokenFactory:
    """Overridden in tests with a fake CLI."""
    return lambda: PtySetupToken(settings.claude_code_bin)


def get_login_supported() -> bool:
    return os.name == "posix"


def _token_out(
    session: Session, box: SecretBox, settings: Settings, login_supported: bool
) -> LlmTokenOut:
    default = session.scalar(select(LlmAccount).where(LlmAccount.is_default))
    stored = accts.to_account(default, box) if default is not None else None
    env_oauth = settings.claude_code_oauth_token.get_secret_value()
    env_key = settings.llm_api_key.get_secret_value()
    active: Literal["stored", "env_oauth", "env_api_key", "none"]
    if stored:
        active, token = "stored", stored.token
    elif env_oauth:
        active, token = "env_oauth", env_oauth
    elif env_key:
        active, token = "env_api_key", env_key
    else:
        active, token = "none", ""
    by = session.get(User, default.set_by) if default and default.set_by else None
    return LlmTokenOut(
        active=active,
        token_hint=mask_token(token),
        stored=default is not None,
        stored_source=default.source if default else None,
        stored_by=(by.full_name or by.primary_email) if by else None,
        stored_at=default.updated_at if default else None,
        login_supported=login_supported,
    )


SessionDep = Annotated[Session, Depends(get_session)]


def _renew(
    session: Session,
    box: SecretBox,
    account_id: int | None,
    label: str | None,
    token: str,
    source: str,
    user_id: int | None,
) -> LlmAccount:
    """A new token: for `account_id`, else a new account named `label`, else the default
    (created when there is none)."""
    row = (
        session.get(LlmAccount, account_id)
        if account_id is not None
        else None
        if label
        else session.scalar(select(LlmAccount).where(LlmAccount.is_default))
    )
    try:
        if row is None:
            _lock_default(session)
            return accts.add_account(
                session,
                box,
                label=label or "Tài khoản mặc định",
                token=token,
                source=source,
                user_id=user_id,
            )
        accts.replace_token(session, box, row, token=token, source=source, user_id=user_id)
        return row
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


BoxDep = Annotated[SecretBox, Depends(get_secret_box)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SupportedDep = Annotated[bool, Depends(get_login_supported)]


@router.get("/admin/llm-token")
def llm_token(
    principal: LlmAdmin,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
) -> LlmTokenOut:
    """Which credential reviews use now; the token itself is never returned."""
    return _token_out(session, box, settings, supported)


@router.put("/admin/llm-token")
def set_llm_token(
    body: LlmTokenIn,
    principal: LlmAdmin,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
) -> LlmTokenOut:
    """A token from `claude setup-token` run elsewhere, for the default account (created when
    there is none); applies to the next review."""
    _renew(session, box, None, None, body.token, "paste", principal.user_id)
    audit(session, principal.user_id, "llm.token.set", "llm:claude_code", source="paste")
    session.commit()
    return _token_out(session, box, settings, supported)


@router.delete("/admin/llm-token", status_code=204)
def clear_llm_token(principal: LlmAdmin, session: SessionDep) -> Response:
    """Forget the default account; the first backup (else the environment) takes over."""
    default = session.scalar(select(LlmAccount).where(LlmAccount.is_default))
    if default is not None:
        accts.remove_account(session, default)
        audit(session, principal.user_id, "llm.token.clear", "llm:claude_code")
        session.commit()
    return Response(status_code=204)


@router.post("/admin/llm-login")
def start_llm_login(
    principal: LlmAdmin,
    sessions: Annotated[LoginSessions, Depends(get_login_sessions)],
    factory: Annotated[SetupTokenFactory, Depends(get_setup_token_factory)],
    supported: SupportedDep,
) -> LlmLoginOut:
    """Start `claude setup-token`; the admin opens the URL and sends back the code."""
    if not supported:
        raise HTTPException(501, "login from the dashboard needs the api container (POSIX)")
    try:
        login_id, url = sessions.start(factory, principal.user_id)
    except LoginUnavailable as exc:
        raise HTTPException(501, str(exc)) from None
    except LoginError as exc:
        log.warning("claude login could not start", extra={"error": str(exc)[:200]})
        raise HTTPException(502, str(exc)) from None
    return LlmLoginOut(login_id=login_id, url=url, expires_in_s=int(LOGIN_TTL_S))


@router.post("/admin/llm-login/{login_id}")
def finish_llm_login(
    login_id: str,
    body: LlmLoginCodeIn,
    principal: LlmAdmin,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
    sessions: Annotated[LoginSessions, Depends(get_login_sessions)],
) -> LlmTokenOut:
    """Hand the code to the CLI and store the token it prints."""
    if body.account_id is not None and session.get(LlmAccount, body.account_id) is None:
        raise HTTPException(404, "account not found")  # before the code is spent
    try:
        token = sessions.finish(login_id, principal.user_id, body.code)
    except KeyError:
        raise HTTPException(404, "login expired or not found; start again") from None
    except LoginError as exc:
        audit(session, principal.user_id, "llm.login", "llm:claude_code", ok=False)
        session.commit()
        raise HTTPException(502, str(exc)) from None
    row = _renew(session, box, body.account_id, body.label, token, "login", principal.user_id)
    audit(session, principal.user_id, "llm.login", f"llm_account:{row.id}", ok=True)
    session.commit()
    return _token_out(session, box, settings, supported)


@router.delete("/admin/llm-login/{login_id}", status_code=204)
def cancel_llm_login(
    login_id: str,
    principal: LlmAdmin,
    sessions: Annotated[LoginSessions, Depends(get_login_sessions)],
) -> Response:
    sessions.cancel(login_id)
    return Response(status_code=204)


# -- several Claude accounts: one default, backups while it fails (app/review/llm_accounts.py) ----


def _accounts_out(
    session: Session, box: SecretBox, settings: Settings, login_supported: bool
) -> LlmAccountsOut:
    tuned = with_overrides(session, settings)
    model = accts.review_model(tuned)
    now = datetime.now(UTC)
    rows = accts.account_rows(session)
    names = {
        u.id: u.full_name or u.primary_email
        for u in session.scalars(select(User).where(User.id.in_({r.set_by for r in rows})))
    }
    out = [
        LlmAccountOut(**accts.describe(r, box, now, model), set_by_name=names.get(r.set_by or 0))
        for r in rows
    ]
    chosen = accts.active(accts.accounts(session, box, tuned), model, now)
    env = accts.env_accounts(tuned)
    return LlmAccountsOut(
        accounts=out,
        active_id=chosen.id if chosen else None,
        active_label=chosen.label if chosen else None,
        model=model,
        retry_min=accts.retry_minutes(session, tuned),
        env_token_hint=mask_token(env[0].token) if env else None,
        login_supported=login_supported,
    )


def _lock_default(session: Session) -> None:
    """Serialise changes of the default (the unique index allows one): two admins adding the
    first account or moving the default at once take turns instead of one getting a 500."""
    session.execute(select(func.pg_advisory_xact_lock(DEFAULT_LOCK)))


def _row(session: Session, account_id: int) -> LlmAccount:
    row = session.get(LlmAccount, account_id)
    if row is None:
        raise HTTPException(404, "account not found")
    return row


@router.get("/admin/llm-accounts")
def list_llm_accounts(
    principal: LlmAdmin,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
) -> LlmAccountsOut:
    """The accounts in the order calls try them, which one the next call uses, and why the
    others wait. Tokens are never returned, only a masked hint."""
    return _accounts_out(session, box, settings, supported)


@router.post("/admin/llm-accounts", status_code=201)
def add_llm_account(
    body: LlmAccountIn,
    principal: LlmAdmin,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
) -> LlmAccountsOut:
    """A pasted token as a new account: the default when it is the first, else the last backup."""
    row = _renew(session, box, None, body.label, body.token, "paste", principal.user_id)
    audit(session, principal.user_id, "llm.account.add", f"llm_account:{row.id}", label=row.label)
    session.commit()
    return _accounts_out(session, box, settings, supported)


@router.patch("/admin/llm-accounts/{account_id}")
def update_llm_account(
    account_id: int,
    body: LlmAccountPatch,
    principal: LlmAdmin,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
) -> LlmAccountsOut:
    """Rename, switch on/off, or make it the default (the old default becomes the first backup)."""
    row = _row(session, account_id)
    changes: dict[str, Any] = body.model_dump(exclude_none=True)
    if body.enabled is False and (body.is_default or row.is_default):
        raise HTTPException(409, "the default account cannot be switched off; make another default")
    if body.label is not None:
        row.label = body.label
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.is_default:
        _lock_default(session)
        accts.set_default(session, row)
    audit(session, principal.user_id, "llm.account.update", f"llm_account:{row.id}", **changes)
    session.commit()
    return _accounts_out(session, box, settings, supported)


@router.put("/admin/llm-accounts/order")
def order_llm_accounts(
    body: LlmAccountOrderIn,
    principal: LlmAdmin,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
) -> LlmAccountsOut:
    """The backups' order (the default always comes first)."""
    accts.reorder(session, body.ids)
    audit(session, principal.user_id, "llm.account.order", "llm:claude_code", ids=body.ids)
    session.commit()
    return _accounts_out(session, box, settings, supported)


@router.put("/admin/llm-accounts/{account_id}/token")
def replace_llm_account_token(
    account_id: int,
    body: LlmAccountTokenIn,
    principal: LlmAdmin,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
) -> LlmAccountsOut:
    """A new pasted token for an account (usable again at once)."""
    row = _row(session, account_id)
    _renew(session, box, row.id, None, body.token, "paste", principal.user_id)
    audit(session, principal.user_id, "llm.account.token", f"llm_account:{row.id}")
    session.commit()
    return _accounts_out(session, box, settings, supported)


@router.delete("/admin/llm-accounts/{account_id}", status_code=204)
def delete_llm_account(account_id: int, principal: LlmAdmin, session: SessionDep) -> Response:
    """Forget an account; when it was the default, the first enabled backup takes over."""
    row = _row(session, account_id)
    accts.remove_account(session, row)
    audit(session, principal.user_id, "llm.account.delete", f"llm_account:{account_id}")
    session.commit()
    return Response(status_code=204)


@router.post("/admin/llm-accounts/{account_id}/check")
def check_llm_account(
    account_id: int,
    principal: LlmAdmin,
    session: SessionDep,
    settings: SettingsDep,
    runner: Annotated[Runner, Depends(get_llm_runner)],
    box: BoxDep,
) -> dict[str, Any]:
    """One live call on this account. It answers: usable again at once. It fails for a reason of
    its own: waits like after a failed review (the result is what reviews will do)."""
    row = _row(session, account_id)
    tuned = with_overrides(session, settings)
    account = accts.to_account(row, box)
    report = check_llm(tuned, stored_token=account.token, account_label=row.label, runner=runner)
    now = datetime.now(UTC)
    if report.status.ok:
        accts.reset_state(row)
        row.last_ok_at = now
    elif found := account_error(report.status.error or ""):
        kind, detail = found
        row.failures = (0 if row.state == "ok" else row.failures) + 1
        row.state = "quota" if kind == "rate" else kind
        row.retry_at = accts.next_retry(
            kind, row.failures, accts.retry_minutes(session, tuned), now, detail
        )
        row.blocked_model = accts.review_model(tuned) if kind == "model" else None
        row.last_error = detail[:500]
        row.last_failed_at = now
    audit(
        session,
        principal.user_id,
        "llm.account.check",
        f"llm_account:{row.id}",
        ok=report.status.ok,
        latency_ms=report.status.latency_ms,
    )
    session.commit()
    return report.to_dict()
