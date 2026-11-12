"""Personal access tokens for Claude Code (docs/specs/26 §2): one's own list, make, revoke; an
admin lists and revokes anyone's.

These endpoints take the dashboard session only (CurrentPrincipal: cookie or session JWT). A
`cpat_` token never works here (read_token refuses it): a token cannot make more tokens.
The token itself is in the answer to POST /me/tokens only, once; never in a list, a log or the
audit.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.schemas import ORM
from app.api.security import AdminPrincipal, CurrentPrincipal
from app.mcp.tokens import DEFAULT_DAYS, MCP_SCOPES, create_token, revoke
from app.models import ApiToken, BusinessProject, User

router = APIRouter(tags=["tokens"])
SessionDep = Annotated[Session, Depends(get_session)]


class TokenOut(ORM):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    business_project_id: int | None = None  # docs/specs/30 §15: bound to this dự án only
    business_project_name: str | None = None
    active: bool = False


class TokenCreated(TokenOut):
    token: str  # cpat_…, shown this once


class TokenIn(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: list(MCP_SCOPES))
    days: int = DEFAULT_DAYS
    business_project_id: int | None = None


def _out(token: ApiToken, now: datetime | None = None, session: Session | None = None) -> TokenOut:
    at = now or datetime.now(UTC)
    out = TokenOut.model_validate(token)
    out.active = token.revoked_at is None and token.expires_at > at
    if token.business_project_id is not None and session is not None:
        bp = session.get(BusinessProject, token.business_project_id)
        out.business_project_name = bp.name if bp else None
    return out


def _list(session: Session, user_id: int) -> list[TokenOut]:
    rows = session.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user_id)
        .order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
    )
    now = datetime.now(UTC)
    return [_out(t, now, session) for t in rows]


def _revoke(session: Session, token: ApiToken, by_user_id: int, by_admin: bool) -> TokenOut:
    if revoke(session, token, by_user_id):
        audit(
            session,
            by_user_id,
            "api_token.revoke",
            f"api_token:{token.id}",
            user_id=token.user_id,
            name=token.name,
            by_admin=by_admin or None,
        )
    session.commit()
    session.refresh(token)
    return _out(token, session=session)


@router.get("/me/tokens", response_model=list[TokenOut])
def my_tokens(principal: CurrentPrincipal, session: SessionDep) -> list[TokenOut]:
    """Newest first, active or not."""
    return _list(session, principal.user_id)


@router.post("/me/tokens", response_model=TokenCreated, status_code=201)
def make_token(body: TokenIn, principal: CurrentPrincipal, session: SessionDep) -> TokenCreated:
    bp_id = body.business_project_id
    if bp_id is not None and (
        session.get(BusinessProject, bp_id) is None or not principal.can_view_project(bp_id)
    ):
        raise HTTPException(404, "không tìm thấy dự án")
    token, raw = create_token(
        session, principal.user_id, body.name, body.scopes, body.days, business_project_id=bp_id
    )
    audit(
        session,
        principal.user_id,
        "api_token.create",
        f"api_token:{token.id}",
        name=token.name,
        scopes=token.scopes,
        expires_at=token.expires_at.isoformat(),
        business_project_id=bp_id,
    )
    session.commit()
    session.refresh(token)
    return TokenCreated(**_out(token, session=session).model_dump(), token=raw)


@router.post("/me/tokens/{token_id}/revoke", response_model=TokenOut)
def revoke_my_token(token_id: int, principal: CurrentPrincipal, session: SessionDep) -> TokenOut:
    token = session.get(ApiToken, token_id)
    if token is None or token.user_id != principal.user_id:
        raise HTTPException(404, "không tìm thấy token")
    return _revoke(session, token, principal.user_id, by_admin=False)


@router.get("/admin/users/{user_id}/tokens", response_model=list[TokenOut])
def user_tokens(user_id: int, principal: AdminPrincipal, session: SessionDep) -> list[TokenOut]:
    if session.get(User, user_id) is None:
        raise HTTPException(404, "không tìm thấy người dùng")
    return _list(session, user_id)


@router.post("/admin/tokens/{token_id}/revoke", response_model=TokenOut)
def admin_revoke_token(token_id: int, principal: AdminPrincipal, session: SessionDep) -> TokenOut:
    token = session.get(ApiToken, token_id)
    if token is None:
        raise HTTPException(404, "không tìm thấy token")
    return _revoke(session, token, principal.user_id, by_admin=True)
