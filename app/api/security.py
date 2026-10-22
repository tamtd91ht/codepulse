"""Sessions and role-based access (docs/specs/09 "Phân quyền", 11).

Roles come from data that already exists: `team_members.role` ("lead" / "manager") and the
ADMIN_EMAILS setting; everyone else is a developer. Phase 0 decision: individual scores are
visible to the person and their lead only, so a manager sees team aggregates, never per-person rows.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.config import Settings, get_settings
from app.models import Team, TeamMember, User

SESSION_COOKIE = "codepulse_session"
ALGORITHM = "HS256"


def _secret(settings: Settings) -> str:
    secret = settings.session_secret.get_secret_value()
    if len(secret) < 32:
        raise HTTPException(503, "SESSION_SECRET is not configured")
    return secret


def issue_token(user_id: int, settings: Settings, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iat": issued,
        "exp": issued + timedelta(hours=settings.session_ttl_hours),
        "iss": "codepulse",
    }
    return jwt.encode(claims, _secret(settings), algorithm=ALGORITHM)


def read_token(token: str, settings: Settings) -> int:
    try:
        claims = jwt.decode(token, _secret(settings), algorithms=[ALGORITHM], issuer="codepulse")
        return int(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(401, "invalid or expired session") from exc


@dataclass
class Principal:
    user_id: int
    email: str | None
    is_admin: bool
    lead_teams: set[int] = field(default_factory=set)
    managed_teams: set[int] = field(default_factory=set)  # includes sub-teams
    own_teams: set[int] = field(default_factory=set)
    lead_members: set[int] = field(default_factory=set)  # users in the teams this person leads

    @property
    def is_lead(self) -> bool:
        return bool(self.lead_teams)

    @property
    def is_manager(self) -> bool:
        return bool(self.managed_teams)

    def can_see_user(self, user_id: int) -> bool:
        """Individual data: yourself, people you lead, or admin."""
        return self.is_admin or user_id == self.user_id or user_id in self.lead_members

    def can_see_team(self, team_id: int) -> bool:
        """Team aggregates: your own teams (anonymous), teams you lead or manage, or admin."""
        return self.is_admin or team_id in self.own_teams | self.lead_teams | self.managed_teams

    def visible_users(self) -> set[int] | None:
        """None means everyone (admin)."""
        return None if self.is_admin else {self.user_id} | self.lead_members


def _descendants(teams: Iterable[Team], roots: set[int]) -> set[int]:
    children: dict[int | None, list[int]] = {}
    for team in teams:
        children.setdefault(team.parent_id, []).append(team.id)
    found, stack = set(roots), list(roots)
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def load_principal(
    session: Session, user_id: int, settings: Settings, today: date | None = None
) -> Principal:
    user = session.get(User, user_id)
    if user is None or user.active is False:
        raise HTTPException(401, "user is not active")
    day = today or datetime.now(UTC).date()
    memberships = [
        m
        for m in session.scalars(select(TeamMember).where(TeamMember.user_id == user_id))
        if m.joined_at <= day and (m.left_at is None or m.left_at > day)
    ]
    lead = {m.team_id for m in memberships if (m.role or "").lower() == "lead"}
    managed_roots = {m.team_id for m in memberships if (m.role or "").lower() == "manager"}
    managed = _descendants(session.scalars(select(Team)), managed_roots) if managed_roots else set()
    lead_members: set[int] = set()
    if lead:
        lead_members = {
            m.user_id
            for m in session.scalars(select(TeamMember).where(TeamMember.team_id.in_(lead)))
            if m.joined_at <= day and (m.left_at is None or m.left_at > day)
        }
    admins = {e.lower() for e in settings.admin_emails}
    return Principal(
        user_id=user.id,
        email=user.primary_email,
        is_admin=bool(user.primary_email and user.primary_email.lower() in admins),
        lead_teams=lead,
        managed_teams=managed,
        own_teams={m.team_id for m in memberships},
        lead_members=lead_members,
    )


def current_principal(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    if not token:
        raise HTTPException(401, "not signed in")
    principal = load_principal(session, read_token(token, settings), settings)
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require_admin(principal: CurrentPrincipal) -> Principal:
    if not principal.is_admin:
        raise HTTPException(403, "admin only")
    return principal


def require_lead(principal: CurrentPrincipal) -> Principal:
    if not (principal.is_admin or principal.is_lead):
        raise HTTPException(403, "lead only")
    return principal


def require_lead_or_manager(principal: CurrentPrincipal) -> Principal:
    if not (principal.is_admin or principal.is_lead or principal.is_manager):
        raise HTTPException(403, "lead or manager only")
    return principal


AdminPrincipal = Annotated[Principal, Depends(require_admin)]
LeadPrincipal = Annotated[Principal, Depends(require_lead)]
LeadOrManagerPrincipal = Annotated[Principal, Depends(require_lead_or_manager)]


def public_user(user: User) -> dict[str, Any]:
    return {"id": user.id, "full_name": user.full_name, "email": user.primary_email}
