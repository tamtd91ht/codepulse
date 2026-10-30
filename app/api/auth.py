"""Dashboard sign-in with GitLab OAuth / OpenID Connect (Phase 0 decision: GitLab SSO)."""

import hmac
import re
import secrets
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin_settings import link_base
from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import SESSION_COOKIE, CurrentPrincipal, issue_token, public_user
from app.config import Settings, get_settings
from app.models import Account, User

router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "codepulse_oauth_state"
SCOPES = "openid email profile read_user"


DEFAULT_PORTS = {"http": 80, "https": 443}
HOST_HEADER = re.compile(r"^[A-Za-z0-9.\-]+(:[0-9]{1,5})?$|^\[[0-9A-Fa-f:.]+\](:[0-9]{1,5})?$")


def _seen_host(request: Request) -> tuple[str, int | None] | None:
    """(hostname, port or None) the browser used, from the dashboard proxy's X-Forwarded-Host
    or Host; None when missing or not a plain host[:port]."""
    raw = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    seen = raw.split(",")[0].strip()
    if not HOST_HEADER.match(seen):
        return None
    try:
        parts = urlsplit(f"//{seen}")
        return (parts.hostname or "").lower(), parts.port
    except ValueError:  # port out of range
        return None


def _serves(base: str, host: str, port: int | None) -> bool:
    parts = urlsplit(base)
    default = DEFAULT_PORTS.get(parts.scheme)
    return (parts.hostname or "").lower() == host and (parts.port or default) == (port or default)


def _origin(base: str) -> str:
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}"


def _site(request: Request, session: Session, settings: Settings) -> tuple[str, str]:
    """(base of the callback, where to land after sign-in) for the address the browser used.

    The OAuth state cookie lives on that address, so the callback must come back to it: a domain
    put in front of ip:port (DASHBOARD_URL or the Admin public URL) signs in there. A host that
    APP_BASE_URL already serves keeps the registered <APP_BASE_URL> callback (cookies ignore the
    port). Only configured addresses are used, with their scheme from config, never the header's.
    """
    app_base = settings.app_base_url.rstrip("/")
    dashboard = settings.dashboard_url.rstrip("/")
    seen = _seen_host(request)
    if seen is None or seen[0] == (urlsplit(app_base).hostname or "").lower():
        return app_base, dashboard
    if _serves(dashboard, *seen):
        return _origin(dashboard), dashboard
    public = link_base(session, settings).rstrip("/")
    if _serves(public, *seen):
        return _origin(public), public
    return app_base, dashboard


def _callback_url(base: str) -> str:
    return base + "/api/v1/auth/callback"


def _gitlab(settings: Settings) -> str:
    if not (settings.gitlab_oauth_url and settings.gitlab_oauth_client_id):
        raise HTTPException(503, "GitLab sign-in is not configured")
    return settings.gitlab_oauth_url.rstrip("/")


def _secure(base: str) -> bool:
    return base.startswith("https://")


def get_oauth_http() -> httpx.Client:
    return httpx.Client(timeout=10)


@router.get("/login")
def login(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    base, _ = _site(request, session, settings)
    state = secrets.token_urlsafe(24)
    query = urlencode(
        {
            "client_id": settings.gitlab_oauth_client_id,
            "redirect_uri": _callback_url(base),
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
        }
    )
    response = RedirectResponse(f"{_gitlab(settings)}/oauth/authorize?{query}", status_code=302)
    response.set_cookie(
        STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", secure=_secure(base)
    )
    return response


def find_user(session: Session, info: dict[str, Any]) -> User | None:
    """GitLab account first (sub = GitLab user id), then a verified email."""
    account = session.scalars(
        select(Account).where(
            Account.kind == "gitlab_user", Account.external_key == str(info["sub"])
        )
    ).first()
    if account and account.user_id:
        return session.get(User, account.user_id)
    email = (info.get("email") or "").lower()
    if email and info.get("email_verified", True):
        return session.scalars(select(User).where(User.primary_email == email)).first()
    return None


def bootstrap_admin(session: Session, info: dict[str, Any], settings: Settings) -> User | None:
    """First sign-in of an ADMIN_EMAILS address on a fresh install: create its user.

    Everyone else is imported (docs/specs/05); only a GitLab-verified admin email is trusted here.
    """
    email = (info.get("email") or "").lower()
    if not email or info.get("email_verified") is not True:
        return None
    if email not in {e.lower() for e in settings.admin_emails}:
        return None
    user = User(primary_email=email, full_name=info.get("name") or None)
    session.add(user)
    session.flush()
    audit(session, user.id, "user.bootstrap_admin", f"user:{user.id}")
    return user


@router.get("/callback")
def callback(
    request: Request,
    code: str,
    state: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    http: Annotated[httpx.Client, Depends(get_oauth_http)],
) -> RedirectResponse:
    expected = request.cookies.get(STATE_COOKIE, "")
    if not expected or not hmac.compare_digest(expected, state):
        raise HTTPException(400, "invalid OAuth state")
    site, landing = _site(request, session, settings)
    base = _gitlab(settings)
    try:
        token = http.post(
            f"{base}/oauth/token",
            data={
                "client_id": settings.gitlab_oauth_client_id,
                "client_secret": settings.gitlab_oauth_client_secret.get_secret_value(),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": _callback_url(site),
            },
        )
        token.raise_for_status()
        userinfo = http.get(
            f"{base}/oauth/userinfo",
            headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        )
        userinfo.raise_for_status()
    except (httpx.HTTPError, KeyError) as exc:
        raise HTTPException(502, "GitLab sign-in failed") from exc

    info = userinfo.json()
    user = find_user(session, info) or bootstrap_admin(session, info, settings)
    if user is None or user.active is False:
        raise HTTPException(403, "this GitLab account is not registered in CodePulse")
    audit(session, user.id, "auth.login", f"user:{user.id}")
    session.commit()

    response = RedirectResponse(landing, status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        issue_token(user.id, settings),
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=_secure(site),
    )
    response.delete_cookie(STATE_COOKIE)
    return response


@router.post("/logout", status_code=204)
def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/me")
def me(
    principal: CurrentPrincipal,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    user = session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(401, "user is not active")
    return {
        **public_user(user),
        "is_admin": principal.is_admin,
        "lead_teams": sorted(principal.lead_teams),
        "managed_teams": sorted(principal.managed_teams),
        "teams": sorted(principal.own_teams),
        # dự án id -> "view" | "manage" (empty for admins, who see and manage every dự án).
        "projects": {str(bp): role for bp, role in sorted(principal.project_roles.items())},
        # Admin > Chạy command (COMMAND_RUNS_ENABLED); always false for everyone else.
        "command_runs": principal.is_admin and settings.command_runs_enabled,
    }
