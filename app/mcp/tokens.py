"""Personal access tokens (docs/specs/26 §2): make, look up, check scope.

A token is `cpat_<43 url-safe chars>`; only its SHA-256 is stored. It works on /mcp
(app/mcp/server.py) and, with a `testcases:*` / `results:write` scope, on /api/v1/testcases/*
(docs/specs/30 §15, app/api/security.py), never beyond its owner's permissions: the principal is
loaded with `load_principal`, exactly as for a dashboard session, then narrowed to the token's
dự án when it is bound to one.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.security import Principal, load_principal
from app.config import Settings
from app.models import ApiToken

TOKEN_PREFIX = "cpat_"  # noqa: S105 - the visible prefix, not a secret
SCOPE_READ = "findings:read"
SCOPE_CLOSE = "findings:close_request"
# docs/specs/26 §22 (user 2026-11-11): ask for a review of pushed commits from Claude Code.
SCOPE_REVIEW = "reviews:request"
MCP_SCOPES = (SCOPE_READ, SCOPE_CLOSE, SCOPE_REVIEW)
# docs/specs/30 §15 (G11): the public testcase API (/api/v1/testcases/*, app/api/security.py).
SCOPE_TC_READ = "testcases:read"
SCOPE_TC_WRITE = "testcases:write"
SCOPE_RESULTS_WRITE = "results:write"
TESTCASE_SCOPES = (SCOPE_TC_READ, SCOPE_TC_WRITE, SCOPE_RESULTS_WRITE)
SCOPES = MCP_SCOPES + TESTCASE_SCOPES
VALID_DAYS = (30, 90, 180, 365)
DEFAULT_DAYS = 90
MAX_ACTIVE_PER_USER = 10
NAME_MAX = 80
# last_used_at is written at most this often per token (one UPDATE per call is waste).
TOUCH_EVERY = timedelta(minutes=5)


class TokenError(Exception):
    """The bearer token is missing, unknown, expired, revoked, or its owner is not active."""


@dataclass
class TokenAuth:
    token: ApiToken
    principal: Principal

    def has(self, scope: str) -> bool:
        return scope in (self.token.scopes or [])


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def active_count(session: Session, user_id: int, now: datetime | None = None) -> int:
    at = now or datetime.now(UTC)
    return int(
        session.scalar(
            select(func.count()).where(
                ApiToken.user_id == user_id,
                ApiToken.revoked_at.is_(None),
                ApiToken.expires_at > at,
            )
        )
        or 0
    )


def create_token(
    session: Session,
    user_id: int,
    name: str,
    scopes: list[str],
    days: int = DEFAULT_DAYS,
    now: datetime | None = None,
    business_project_id: int | None = None,
) -> tuple[ApiToken, str]:
    """A new token row (flushed, not committed) and the token itself, shown once.
    HTTPException 422/409 for bad input or too many active tokens. The caller checks that its
    owner may see `business_project_id` (the dự án the token is bound to)."""
    clean = name.strip()
    if not clean or len(clean) > NAME_MAX:
        raise HTTPException(422, f"tên token cần 1-{NAME_MAX} ký tự")
    if days not in VALID_DAYS:
        raise HTTPException(422, f"hạn dùng chỉ được {', '.join(map(str, VALID_DAYS))} ngày")
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise HTTPException(422, f"scope không hợp lệ: {', '.join(unknown)}")
    at = now or datetime.now(UTC)
    if active_count(session, user_id, at) >= MAX_ACTIVE_PER_USER:
        raise HTTPException(
            409, f"tối đa {MAX_ACTIVE_PER_USER} token còn hiệu lực: thu hồi bớt token cũ"
        )
    secret = secrets.token_urlsafe(32)
    raw = f"{TOKEN_PREFIX}{secret}"
    # findings:read always for an MCP token (any MCP scope asked, or none at all); a token for the
    # testcase API only stays out of /mcp. The order of SCOPES, no duplicates.
    api_only = bool(scopes) and all(s in TESTCASE_SCOPES for s in scopes)
    granted = [s for s in SCOPES if (s == SCOPE_READ and not api_only) or s in scopes]
    token = ApiToken(
        user_id=user_id,
        name=clean,
        prefix=secret[:8],
        token_hash=hash_token(raw),
        scopes=granted,
        expires_at=at + timedelta(days=days),
        business_project_id=business_project_id,
    )
    session.add(token)
    session.flush()
    return token, raw


def authenticate(
    session: Session, raw: str | None, settings: Settings, now: datetime | None = None
) -> TokenAuth:
    """The token and its owner's principal; TokenError when it does not authenticate.
    Touches last_used_at (at most every TOUCH_EVERY); the caller commits."""
    if not raw or not raw.startswith(TOKEN_PREFIX):
        raise TokenError("cần header Authorization: Bearer cpat_…")
    at = now or datetime.now(UTC)
    token = session.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw)))
    if token is None:
        raise TokenError("token không hợp lệ")
    if token.revoked_at is not None:
        raise TokenError("token đã bị thu hồi")
    if token.expires_at <= at:
        raise TokenError("token đã hết hạn: tạo token mới ở trang Của tôi")
    try:
        principal = load_principal(session, token.user_id, settings)
    except HTTPException as exc:
        raise TokenError("tài khoản của token không còn hoạt động") from exc
    if token.last_used_at is None or at - token.last_used_at >= TOUCH_EVERY:
        token.last_used_at = at
    # docs/specs/30 §15: what the token may do, and the one dự án it is bound to (if any); the
    # dự án rules of Principal then refuse every other dự án.
    principal.token_id = int(token.id)
    principal.token_scopes = tuple(token.scopes or [])
    principal.token_project = token.business_project_id
    return TokenAuth(token=token, principal=principal)


def revoke(session: Session, token: ApiToken, by_user_id: int, now: datetime | None = None) -> bool:
    """False when it was already revoked."""
    if token.revoked_at is not None:
        return False
    token.revoked_at = now or datetime.now(UTC)
    token.revoked_by = by_user_id
    return True
