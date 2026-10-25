"""LLM connection check and the Claude login for admins (outside the spec, requested by the
user; see PROGRESS.md)."""

import logging
import os
import subprocess
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.schemas import LlmLoginCodeIn, LlmLoginOut, LlmTokenIn, LlmTokenOut
from app.api.security import AdminPrincipal
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.models import Review, User
from app.review.claude_login import (
    LOGIN_TTL_S,
    SESSIONS,
    LoginError,
    LoginSessions,
    LoginUnavailable,
    PtySetupToken,
    SetupTokenFactory,
)
from app.review.credentials import (
    clear_claude_token,
    save_claude_token,
    stored_claude_token,
)
from app.review.llm_check import check_llm, mask_token
from app.review.providers.claude_code import Runner

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


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
    principal: AdminPrincipal,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    runner: Annotated[Runner, Depends(get_llm_runner)],
    box: Annotated[SecretBox, Depends(get_secret_box)],
) -> dict[str, Any]:
    """Configured provider/model/CLI, auth method and account, one live call, month usage.

    POST because it makes a real (tiny) LLM call that is billed. The token is never returned.
    """
    stored = stored_claude_token(session, box)
    report = check_llm(settings, stored_token=stored.token if stored else "", runner=runner)
    audit(
        session,
        principal.user_id,
        "llm.check",
        f"llm:{report.provider}",
        ok=report.status.ok,
        latency_ms=report.status.latency_ms,
    )
    session.commit()
    return {**report.to_dict(), "usage": month_usage(session, settings.monthly_llm_budget_usd)}


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
    stored = stored_claude_token(session, box)
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
    by = session.get(User, stored.set_by) if stored and stored.set_by else None
    return LlmTokenOut(
        active=active,
        token_hint=mask_token(token),
        stored=stored is not None,
        stored_source=stored.source if stored else None,
        stored_by=(by.full_name or by.primary_email) if by else None,
        stored_at=stored.updated_at if stored else None,
        login_supported=login_supported,
    )


SessionDep = Annotated[Session, Depends(get_session)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SupportedDep = Annotated[bool, Depends(get_login_supported)]


@router.get("/admin/llm-token")
def llm_token(
    principal: AdminPrincipal,
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
    principal: AdminPrincipal,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
) -> LlmTokenOut:
    """A token from `claude setup-token` run elsewhere; applies to the next review."""
    save_claude_token(session, box, body.token, source="paste", user_id=principal.user_id)
    audit(session, principal.user_id, "llm.token.set", "llm:claude_code", source="paste")
    session.commit()
    return _token_out(session, box, settings, supported)


@router.delete("/admin/llm-token", status_code=204)
def clear_llm_token(principal: AdminPrincipal, session: SessionDep) -> Response:
    """Forget the stored token; reviews fall back to the environment."""
    if clear_claude_token(session):
        audit(session, principal.user_id, "llm.token.clear", "llm:claude_code")
        session.commit()
    return Response(status_code=204)


@router.post("/admin/llm-login")
def start_llm_login(
    principal: AdminPrincipal,
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
    principal: AdminPrincipal,
    session: SessionDep,
    box: BoxDep,
    settings: SettingsDep,
    supported: SupportedDep,
    sessions: Annotated[LoginSessions, Depends(get_login_sessions)],
) -> LlmTokenOut:
    """Hand the code to the CLI and store the token it prints."""
    try:
        token = sessions.finish(login_id, principal.user_id, body.code)
    except KeyError:
        raise HTTPException(404, "login expired or not found; start again") from None
    except LoginError as exc:
        audit(session, principal.user_id, "llm.login", "llm:claude_code", ok=False)
        session.commit()
        raise HTTPException(502, str(exc)) from None
    save_claude_token(session, box, token, source="login", user_id=principal.user_id)
    audit(session, principal.user_id, "llm.login", "llm:claude_code", ok=True)
    session.commit()
    return _token_out(session, box, settings, supported)


@router.delete("/admin/llm-login/{login_id}", status_code=204)
def cancel_llm_login(
    login_id: str,
    principal: AdminPrincipal,
    sessions: Annotated[LoginSessions, Depends(get_login_sessions)],
) -> Response:
    sessions.cancel(login_id)
    return Response(status_code=204)
