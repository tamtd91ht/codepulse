"""Sessions and role-based access (docs/specs/09 "Phân quyền", 11).

Roles come from data that already exists: `team_members.role` ("lead" / "manager") and the
ADMIN_EMAILS setting; everyone else is a developer. Phase 0 decision: individual scores are
visible to the person and their lead only, so a manager sees team aggregates, never per-person rows.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request
from redis import Redis
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.audit_trail import set_actor
from app.config import Settings, get_settings
from app.models import (
    BusinessProject,
    PermissionSet,
    PermissionSetItem,
    ProjectGrant,
    Team,
    TeamMember,
    User,
    UserPermissionSet,
)
from app.permissions.catalog import BY_KEY, FLAG, everything, merge
from app.ratelimit import WindowCounter

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
    # dự án id -> "view" | "manage" (project_grants); admins need none.
    project_roles: dict[int, str] = field(default_factory=dict)
    # users.manage_all: "manage" on every dự án, granted or not (not admin: no Admin screens).
    manages_all: bool = False
    # docs/specs/21: the permissions held (catalog key -> scope, or "flag"), the union of the
    # permission sets of this person's roles and those given to them; admins hold everything.
    perms: dict[str, str] = field(default_factory=dict)
    # People in the teams this person manages (sub-teams included): the "team" scope with
    # lead_members.
    managed_members: set[int] = field(default_factory=set)
    # docs/specs/30 §15 (G11): set when the caller is a personal access token (cpat_), not a
    # dashboard session: its id, scopes and the one dự án it is bound to (None: every dự án of
    # its owner). A bound token sees no other dự án, whatever its owner may.
    token_id: int | None = None
    token_scopes: tuple[str, ...] | None = None
    token_project: int | None = None

    def _bound_out(self, bp_id: int | None) -> bool:
        """A dự án outside the one the token is bound to."""
        return self.token_project is not None and bp_id != self.token_project

    # -- docs/specs/21 ---------------------------------------------------------------------

    def can(self, key: str) -> bool:
        """Holds permission `key` at any scope."""
        return self.is_admin or key in self.perms

    def scope(self, key: str) -> str | None:
        """The scope `key` is held at ("flag" for a flag); None: not held."""
        if self.is_admin:
            perm = BY_KEY.get(key)
            return None if perm is None else FLAG if perm.is_flag else "all"
        return self.perms.get(key)

    def team_people(self, key: str) -> set[int]:
        """Who the "team" scope of `key` reaches: the people one leads, and for the
        permissions that say so (catalog `managed_team`) the people of the teams one manages."""
        perm = BY_KEY.get(key)
        if perm is not None and perm.managed_team:
            return self.lead_members | self.managed_members
        return set(self.lead_members)

    def covers(self, key: str, user_id: int | None, bp_id: int | None) -> bool:
        """Holds `key` over the work of `user_id` in dự án `bp_id` (docs/specs/21 §2): own
        work; with "team" also the people one leads or manages; with "project" also everyone in a
        dự án one manages; with "all" everyone in a dự án one can access. "team" is
        `team_people(key)`."""
        if self._bound_out(bp_id):
            return False
        if self.is_admin:
            return True
        held = self.perms.get(key)
        if held is None:
            return False
        if held == "flag":
            return True
        if user_id is not None and user_id == self.user_id:
            return True
        if held == "own":
            return False
        led = user_id is not None and user_id in self.team_people(key)
        if led and (bp_id is None or self.can_view_project(bp_id)):
            return True
        if held == "team":
            return False
        if held == "project":
            return self.can_manage_project(bp_id)
        return self.can_view_project(bp_id)  # "all"

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

    # -- dự án grants (docs/specs/09 "Phân quyền theo dự án") -----------------------------------
    # A dự án you hold no grant on does not exist for you; repos outside every dự án are the
    # admins'. Inside a dự án you see, the team rules above still hold ("view"); "manage" covers
    # everyone's work there. Your own work stays yours wherever it is.

    def visible_projects(self) -> set[int] | None:
        """None means every dự án (admin)."""
        if self.token_project is not None:
            return {self.token_project} if self.can_view_project(self.token_project) else set()
        return None if self.is_admin else set(self.project_roles)

    def managed_projects(self) -> set[int] | None:
        """None means every dự án (admin)."""
        if self.token_project is not None:
            return {self.token_project} if self.can_manage_project(self.token_project) else set()
        if self.is_admin:
            return None
        return {bp for bp, role in self.project_roles.items() if role == "manage"}

    def can_view_project(self, bp_id: int | None) -> bool:
        if self._bound_out(bp_id):
            return False
        return self.is_admin or (bp_id is not None and bp_id in self.project_roles)

    def can_manage_project(self, bp_id: int | None) -> bool:
        if self._bound_out(bp_id):
            return False
        return self.is_admin or (bp_id is not None and self.project_roles.get(bp_id) == "manage")

    def can_see_work(self, author_user_id: int | None, bp_id: int | None) -> bool:
        """A review, its findings or a commit, in a repo of dự án `bp_id`."""
        if self._bound_out(bp_id):
            return False
        if self.is_admin or (author_user_id is not None and author_user_id == self.user_id):
            return True
        if not self.can_view_project(bp_id):
            return False
        if self.can_manage_project(bp_id):
            return True
        return author_user_id is not None and author_user_id in self.lead_members

    def may_review_work(self, author_user_id: int | None, bp_id: int | None) -> bool:
        """Asking for a review of a commit / MR in a repo of dự án `bp_id` (dashboard and
        Telegram alike, 2026-11-02): your own work in a dự án you see; anyone else's, and work
        whose author is not mapped to a person, only as a manager of that dự án or admin.
        Leading a team gives no review right (it still shows the team's results)."""
        if self.can_manage_project(bp_id):
            return True
        return (
            self.can_view_project(bp_id)
            and author_user_id is not None
            and author_user_id == self.user_id
        )

    def reviews_others(self, bp_id: int | None) -> bool:
        """May ask for reviews of other people's work in dự án `bp_id`."""
        return self.can_manage_project(bp_id)


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
    is_admin = bool(user.primary_email and user.primary_email.lower() in admins)
    roles: dict[int, str] = {}
    manages_all = not is_admin and bool(user.manage_all)
    if manages_all:
        # Every dự án as its manager: every rule on project_roles then holds as for a granted
        # manager. Repos outside every dự án stay the admins'.
        roles = {int(bp): "manage" for bp in session.scalars(select(BusinessProject.id))}
    elif not is_admin:
        roles = {
            int(bp): str(role)
            for bp, role in session.execute(
                select(ProjectGrant.business_project_id, ProjectGrant.role).where(
                    ProjectGrant.user_id == user_id
                )
            ).tuples()
        }
    managed_members: set[int] = set()
    if managed:
        managed_members = {
            m.user_id
            for m in session.scalars(select(TeamMember).where(TeamMember.team_id.in_(managed)))
            if m.joined_at <= day and (m.left_at is None or m.left_at > day)
        } - {user.id}
    auto_roles = ["member"]
    if lead:
        auto_roles.append("lead")
    if managed_roots:
        auto_roles.append("manager")
    if any(role == "manage" for role in roles.values()) and not manages_all:
        auto_roles.append("project_manager")
    if manages_all:
        auto_roles.append("manage_all")
    return Principal(
        user_id=user.id,
        email=user.primary_email,
        is_admin=is_admin,
        lead_teams=lead,
        managed_teams=managed,
        own_teams={m.team_id for m in memberships},
        lead_members=lead_members,
        project_roles=roles,
        manages_all=manages_all,
        perms=everything() if is_admin else _permissions(session, user.id, auto_roles),
        managed_members=managed_members,
    )


def _permissions(session: Session, user_id: int, auto_roles: list[str]) -> dict[str, str]:
    """The union of the sets of these roles and the sets given to the person (docs/specs/21)."""
    given = select(UserPermissionSet.set_id).where(UserPermissionSet.user_id == user_id)
    rows = session.execute(
        select(PermissionSetItem.permission, PermissionSetItem.scope)
        .join(PermissionSet, PermissionSet.id == PermissionSetItem.set_id)
        .where(or_(PermissionSet.auto_role.in_(auto_roles), PermissionSet.id.in_(given)))
    ).tuples()
    return merge([(str(k), str(v)) for k, v in rows])


def require(key: str) -> Any:
    """FastAPI dependency: the principal holding permission `key` (at any scope; endpoints
    check the scope on the object with `Principal.covers`), else 403."""
    if key not in BY_KEY:
        raise ValueError(f"unknown permission {key!r}")  # caught at import, not per request

    def dependency(principal: CurrentPrincipal) -> Principal:
        if not principal.can(key):
            raise HTTPException(403, f"thiếu quyền {BY_KEY[key].label.lower()}")
        return principal

    return Depends(dependency)


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
    if token.startswith(API_TOKEN_PREFIX):
        principal = token_principal(request, session, settings, token)
    else:
        principal = load_principal(session, read_token(token, settings), settings)
    request.state.principal = principal
    # §15.14 (G12): the audit trail's actor, a token's owner included.
    set_actor(session, principal.user_id)
    return principal


# -- the public testcase API (docs/specs/30 §15, G11) ----------------------------------------------

API_TOKEN_PREFIX = "cpat_"  # noqa: S105 - app/mcp/tokens.py TOKEN_PREFIX (no import cycle)
TESTCASE_API = "/api/v1/testcases"
TOKEN_RATE_PER_MINUTE = 600
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Recording results (POST only): a CI token holding results:write only may call these (uploading
# a result's attachments included, docs/specs/30 §15 G6), and read its CI imports
# (CI_IMPORT_PATHS, the CLI's --wait), and nothing else.
RESULT_PATHS = re.compile(
    r"^/api/v1/testcases/"
    r"(?:tests/\d+/results|runs/\d+/results|runs/import-results|attachments)/?$"
)
CI_IMPORT_PATHS = re.compile(r"^/api/v1/testcases/ci-imports(?:/\d+)?/?$")

_rate_redis: Redis | None = None


def _token_rate_store() -> Any:
    """One Redis client per process (lazy): a client per call would open a connection pool each
    time."""
    global _rate_redis
    if _rate_redis is None:
        _rate_redis = Redis.from_url(
            str(get_settings().redis_url), socket_connect_timeout=0.5, socket_timeout=0.5
        )
    return _rate_redis


# One per process; tests swap it (WindowCounter(None) counts in memory).
token_rate = WindowCounter(_token_rate_store)


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def needed_scopes(method: str, path: str) -> tuple[str, ...]:
    """Any one of these scopes lets a token make this call."""
    verb = method.upper()
    if verb in READ_METHODS:
        if CI_IMPORT_PATHS.match(path):
            return ("testcases:read", "testcases:write", "results:write")
        return ("testcases:read", "testcases:write")
    if verb == "POST" and RESULT_PATHS.match(path):
        return ("results:write", "testcases:write")
    return ("testcases:write",)


def token_principal(request: Request, session: Session, settings: Settings, raw: str) -> Principal:
    """A personal access token on the testcase API: only /api/v1/testcases/* (its OpenAPI docs
    included), within the token's scopes and rate; its owner's permissions, narrowed to its
    dự án."""
    from app.mcp.tokens import TokenError, authenticate  # app.mcp.tokens imports this module

    path = request.url.path
    if not _under(path, TESTCASE_API):
        raise HTTPException(401, "token chỉ dùng cho /api/v1/testcases")
    try:
        auth = authenticate(session, raw, settings)
    except TokenError as exc:
        raise HTTPException(401, str(exc)) from exc
    session.commit()  # last_used_at, even when the call is refused below
    principal = auth.principal
    wait = token_rate.hit(f"cp:api:rate:{principal.token_id}", TOKEN_RATE_PER_MINUTE)
    if wait is not None:
        raise HTTPException(
            429,
            f"quá {TOKEN_RATE_PER_MINUTE} lần gọi mỗi phút: chờ {wait} giây",
            headers={"Retry-After": str(wait)},
        )
    wanted = needed_scopes(request.method, path)
    held = principal.token_scopes or ()
    if not any(s in held for s in wanted):
        raise HTTPException(403, f"token thiếu scope {' hoặc '.join(wanted)}")
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


def require_insights(principal: CurrentPrincipal) -> Principal:
    """Luồng làm việc (docs/specs/20 §8): lead and above only: an admin, a lead or a manager of
    a team, or a manager of a dự án (granted, or every one with manage_all). A member, or one
    who only views a dự án, does not see it (user request 2026-11-03)."""
    if not principal.can("insights.view"):
        raise HTTPException(403, "Luồng làm việc chỉ dành cho lead trở lên")
    return principal


def require_members_page(principal: CurrentPrincipal) -> Principal:
    """Thành viên (the per-person evaluation of a team): lead and above, as Luồng làm việc; a
    member's own numbers are on their own page (/members/{id})."""
    if not principal.can("members.view"):
        raise HTTPException(403, "Thành viên chỉ dành cho lead trở lên")
    return principal


def lead_and_above(principal: Principal) -> bool:
    """An admin, manage_all, a lead or a manager of a team, or a manager of a dự án: who sees
    the team-wide pages (Luồng làm việc, Thành viên), user request 2026-11-03."""
    return (
        principal.is_admin
        or principal.manages_all
        or principal.is_lead
        or principal.is_manager
        or "manage" in principal.project_roles.values()
    )


insights_allowed = lead_and_above


AdminPrincipal = Annotated[Principal, Depends(require_admin)]
InsightsPrincipal = Annotated[Principal, Depends(require_insights)]
MembersPagePrincipal = Annotated[Principal, Depends(require_members_page)]
LeadPrincipal = Annotated[Principal, Depends(require_lead)]
LeadOrManagerPrincipal = Annotated[Principal, Depends(require_lead_or_manager)]


def public_user(user: User) -> dict[str, Any]:
    return {"id": user.id, "full_name": user.full_name, "email": user.primary_email}
