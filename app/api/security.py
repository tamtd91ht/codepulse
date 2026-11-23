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
    TeamPermissionSet,
    User,
    UserPermissionSet,
)
from app.permissions.catalog import (
    BY_KEY,
    FLAG,
    PROJECT_ASSIGNABLE,
    PROJECT_TEAM_KEYS,
    everything,
    merge,
    valid_item,
)
from app.ratelimit import WindowCounter

SESSION_COOKIE = "codepulse_session"
ALGORITHM = "HS256"


def _secret(settings: Settings) -> str:
    secret = settings.session_secret.get_secret_value()
    if len(secret) < 32:
        raise HTTPException(503, "SESSION_SECRET is not configured")
    return secret


def issue_token(
    user_id: int, settings: Settings, now: datetime | None = None, epoch: int = 0
) -> str:
    """A session token. `epoch` is the person's `session_epoch` when they signed in (`sv`):
    `session_user` refuses the token once an admin has signed them out since."""
    issued = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "sv": epoch,
        "iat": issued,
        "exp": issued + timedelta(hours=settings.session_ttl_hours),
        "iss": "codepulse",
    }
    return jwt.encode(claims, _secret(settings), algorithm=ALGORITHM)


def read_session(token: str, settings: Settings) -> tuple[int, int]:
    """(user id, session epoch) of a signed, unexpired token; a token from before the epoch
    existed counts as epoch 0."""
    try:
        claims = jwt.decode(token, _secret(settings), algorithms=[ALGORITHM], issuer="codepulse")
        return int(claims["sub"]), int(claims.get("sv", 0))
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(401, "invalid or expired session") from exc


def read_token(token: str, settings: Settings) -> int:
    return read_session(token, settings)[0]


def session_user(session: Session, token: str, settings: Settings) -> User:
    """The person a session token signs in: active, and not signed out by an admin since the
    token was issued (docs/specs/33 §14). Every way in with a session token comes through
    here: requests, the inbox stream, the presence socket."""
    user_id, epoch = read_session(token, settings)
    user = session.get(User, user_id)
    if user is None or user.active is False:
        raise HTTPException(401, "user is not active")
    if (user.session_epoch or 0) != epoch:
        raise HTTPException(401, "phiên đăng nhập đã bị quản trị viên kết thúc, hãy đăng nhập lại")
    return user


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
    # docs/specs/21 §10: dự án id -> "lead" | "manager" (project_grants.admin_role), the dự án
    # this person sets up (repos, .claude repo, rules) without being admin.
    project_admin: dict[int, str] = field(default_factory=dict)
    # docs/specs/31 §21.5 (R1b): the dự án where someone else of a team this person leads
    # (lead_member_projects) or manages (managed_member_projects) holds a project grant now.
    lead_member_projects: set[int] = field(default_factory=set)
    managed_member_projects: set[int] = field(default_factory=set)
    # The dự án-wide permissions some set of this person gives at "team": merged with a wider
    # scope (say "project" from the built-in Quản lý dự án set) they keep the team's dự án.
    team_granted: set[str] = field(default_factory=set)
    # docs/specs/31 §21.0 (R2): users.is_tester, or a current member of a team with is_qa
    # (the "tester" built-in set).
    is_tester: bool = False
    # docs/specs/21 §13: dự án id -> the rights the sets of that dự án give this person, each
    # over the whole of that dự án only (and only while they still have a grant there).
    project_perms: dict[int, frozenset[str]] = field(default_factory=dict)

    def _bound_out(self, bp_id: int | None) -> bool:
        """A dự án outside the one the token is bound to."""
        return self.token_project is not None and bp_id != self.token_project

    # -- docs/specs/21 ---------------------------------------------------------------------

    def can(self, key: str) -> bool:
        """Holds permission `key` at any scope, across the system or in some dự án (§13):
        enough to reach an endpoint, which then checks the object (`covers*`)."""
        return (
            self.is_admin
            or key in self.perms
            or any(key in held for held in self.project_perms.values())
        )

    def holds_in(self, key: str, bp_id: int | None) -> bool:
        """docs/specs/21 §13: a set of dự án `bp_id` gives `key` over the whole of it."""
        return (
            bp_id is not None
            and key in self.project_perms.get(bp_id, frozenset())
            and self.can_view_project(bp_id)
        )

    def local_projects(self, key: str) -> set[int]:
        """The dự án whose own sets give `key` (§13), among those one still sees."""
        return {
            bp
            for bp, held in self.project_perms.items()
            if key in held and self.can_view_project(bp)
        }

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

    def team_projects(self, key: str) -> set[int]:
        """Where the "team" scope of a dự án-wide permission (catalog `PROJECT_TEAM_KEYS`,
        docs/specs/31 §21.5) reaches: the dự án where someone of a team one leads, and for the
        permissions that say so (`managed_team`) of a team one manages, holds a project grant,
        among the dự án one sees. One's own grants do not count: they are not the team's."""
        perm = BY_KEY.get(key)
        ids = set(self.lead_member_projects)
        if perm is not None and perm.managed_team:
            ids |= self.managed_member_projects
        return {bp for bp in ids if self.can_view_project(bp)}

    def covers_project(self, key: str, bp_id: int | None) -> bool:
        """Holds the dự án-wide permission `key` over dự án `bp_id` (docs/specs/31 §21.5):
        "team" = a dự án of `team_projects(key)`; "project" = a dự án one manages, plus the
        team's when some set gives `key` at "team" (merging keeps the widest scope, which must
        not lose them; a "project" alone does not reach them: managing one dự án makes nobody
        the owner of the dự án their team works in); "all" = every dự án one sees. "team" on a
        permission outside `PROJECT_TEAM_KEYS` (tests.run: per test) reaches no dự án."""
        if bp_id is None or not self.can_view_project(bp_id):
            return False
        if self.holds_in(key, bp_id):
            return True
        held = self.scope(key)
        if held in ("all", FLAG):
            return True
        if held not in ("team", "project"):
            return False
        if held == "project" and self.can_manage_project(bp_id):
            return True
        if held == "project" and key not in self.team_granted:
            return False
        if key not in PROJECT_TEAM_KEYS:
            return False
        perm = BY_KEY[key]  # visibility checked above: no need for team_projects' filter
        return bp_id in self.lead_member_projects or (
            perm.managed_team and bp_id in self.managed_member_projects
        )

    def covers(self, key: str, user_id: int | None, bp_id: int | None) -> bool:
        """Holds `key` over the work of `user_id` in dự án `bp_id` (docs/specs/21 §2): own
        work; with "team" also the people one leads or manages; with "project" also everyone in a
        dự án one manages; with "all" everyone in a dự án one can access. "team" is
        `team_people(key)`."""
        if self._bound_out(bp_id):
            return False
        if self.is_admin:
            return True
        if self.holds_in(key, bp_id):
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

    # -- setting a dự án up (docs/specs/21 §10) ------------------------------------------------

    def can_setup_project(self, bp_id: int | None) -> bool:
        """Add repos to dự án `bp_id`, configure them, its .claude repo, rule files and review
        rules: `admin.projects` (every dự án), else `project.setup` at "project" on the dự án
        one manages (Lead / Manager dự án always hold a manage grant), or at "all" on every
        dự án one sees, the meaning "project" / "all" have for every other permission
        (2026-11-18: a project.setup granted by hand at "project" to a plain manager used to
        cover nothing, so the menu never showed)."""
        if bp_id is None or self._bound_out(bp_id):
            return False
        if self.can("admin.projects"):
            return True
        held = self.perms.get("project.setup")
        if held == "all":
            return self.can_view_project(bp_id)
        return held == "project" and self.can_manage_project(bp_id)

    def setup_projects(self) -> set[int] | None:
        """The dự án this person sets up; None: every one (admin.projects)."""
        if self.token_project is None and self.can("admin.projects"):
            return None
        ids = (
            set(self.project_roles) | set(self.project_admin)
            if self.token_project is None
            else {self.token_project}
        )
        return {bp for bp in ids if self.can_setup_project(bp)}

    def can_staff_project(self, bp_id: int | None) -> bool:
        """Change who sees dự án `bp_id` and name its Leads: `admin.projects`, or its Manager
        (project_grants.admin_role = "manager") who may set it up."""
        if self.can("admin.projects"):
            return not self._bound_out(bp_id)
        return (
            bp_id is not None
            and self.project_admin.get(bp_id) == "manager"
            and self.can_setup_project(bp_id)
        )

    # -- a dự án's own rights (docs/specs/21 §13) ----------------------------------------------

    def whole_project(self, key: str, bp_id: int | None) -> bool:
        """Holds `key` over everyone's work in dự án `bp_id`: "all" (or a flag) on a dự án one
        sees, "project" on one one manages, or a set of that dự án. What one may hand out there."""
        if bp_id is None or not self.can_view_project(bp_id):
            return False
        if self.is_admin or self.holds_in(key, bp_id):
            return True
        held = self.perms.get(key)
        return held in ("all", FLAG) or (held == "project" and self.can_manage_project(bp_id))

    def can_permit_project(self, bp_id: int | None) -> bool:
        """Make the dự án's own permission sets and tick rights for its people: admin, a holder
        of admin.permissions, or `project.permissions` ("project": the dự án one is Manager dự án
        of; "all": every dự án one sees)."""
        if bp_id is None or not self.can_view_project(bp_id):
            return False
        if self.is_admin or "admin.permissions" in self.perms:
            return True
        held = self.perms.get("project.permissions")
        return held == "all" or (held == "project" and self.project_admin.get(bp_id) == "manager")

    def permit_projects(self) -> set[int] | None:
        """The dự án `can_permit_project` holds for; None: every one."""
        if self.token_project is None and (self.is_admin or "admin.permissions" in self.perms):
            return None
        ids = set(self.project_roles) | set(self.project_admin)
        if self.token_project is not None:
            ids = {self.token_project}
        return {bp for bp in ids if self.can_permit_project(bp)}

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
    project_admin: dict[int, str] = {}
    manages_all = not is_admin and bool(user.manage_all)
    if manages_all:
        # Every dự án as its manager: every rule on project_roles then holds as for a granted
        # manager. Repos outside every dự án stay the admins'.
        roles = {int(bp): "manage" for bp in session.scalars(select(BusinessProject.id))}
    if not is_admin:
        for bp, role, admin_role in session.execute(
            select(
                ProjectGrant.business_project_id, ProjectGrant.role, ProjectGrant.admin_role
            ).where(ProjectGrant.user_id == user_id)
        ).tuples():
            if not manages_all:
                roles[int(bp)] = str(role)
            if admin_role:
                project_admin[int(bp)] = str(admin_role)
    managed_members: set[int] = set()
    if managed:
        managed_members = {
            m.user_id
            for m in session.scalars(select(TeamMember).where(TeamMember.team_id.in_(managed)))
            if m.joined_at <= day and (m.left_at is None or m.left_at > day)
        } - {user.id}
    lead_projects, managed_projects = (
        _member_projects(session, lead_members - {user.id}, managed_members)
        if not is_admin
        else (set(), set())
    )
    auto_roles = ["member"]
    if lead:
        auto_roles.append("lead")
    if managed_roots:
        auto_roles.append("manager")
    if any(role == "manage" for role in roles.values()) and not manages_all:
        auto_roles.append("project_manager")
    if manages_all:
        auto_roles.append("manage_all")
    if project_admin:
        auto_roles.append("project_admin")
    # docs/specs/31 §21.0 (R2): flagged a tester, or a current member of a QA team.
    is_tester = bool(user.is_tester) or (
        bool(memberships)
        and session.scalar(
            select(Team.id)
            .where(Team.id.in_({m.team_id for m in memberships}), Team.is_qa.is_(True))
            .limit(1)
        )
        is not None
    )
    if is_tester:
        auto_roles.append("tester")
    items = (
        []
        if is_admin
        else _permission_items(session, user.id, auto_roles, {m.team_id for m in memberships})
    )
    project_perms = {} if is_admin else project_items(session, user.id, set(roles))
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
        perms=everything() if is_admin else merge(items),
        managed_members=managed_members,
        project_admin=project_admin,
        lead_member_projects=lead_projects,
        managed_member_projects=managed_projects,
        team_granted={k for k, v in items if v == "team"} & PROJECT_TEAM_KEYS,
        is_tester=is_tester,
        project_perms=project_perms,
    )


def _member_projects(
    session: Session, led: set[int], managed: set[int]
) -> tuple[set[int], set[int]]:
    """The dự án where an active person of `led` / of `managed` holds a project grant
    (docs/specs/31 §21.5): one query, none when one leads and manages nobody."""
    people = led | managed
    if not people:
        return set(), set()
    lead_out: set[int] = set()
    managed_out: set[int] = set()
    rows = session.execute(
        select(ProjectGrant.user_id, ProjectGrant.business_project_id)
        .join(User, User.id == ProjectGrant.user_id)
        .where(ProjectGrant.user_id.in_(sorted(people)), User.active.is_not(False))
    ).all()
    for uid, bp in rows:
        if uid in led:
            lead_out.add(int(bp))
        if uid in managed:
            managed_out.add(int(bp))
    return lead_out, managed_out


def _permission_items(
    session: Session, user_id: int, auto_roles: list[str], team_ids: set[int] | None = None
) -> list[tuple[str, str]]:
    """The items of the sets of these roles, the sets given to the person and the sets given to
    the teams they are in now (docs/specs/21, §11); `merge` makes their union."""
    given = select(UserPermissionSet.set_id).where(UserPermissionSet.user_id == user_id)
    by_team = select(TeamPermissionSet.set_id).where(
        TeamPermissionSet.team_id.in_(sorted(team_ids or set()))
    )
    rows = session.execute(
        select(PermissionSetItem.permission, PermissionSetItem.scope)
        .join(PermissionSet, PermissionSet.id == PermissionSetItem.set_id)
        .where(
            PermissionSet.business_project_id.is_(None),
            or_(
                PermissionSet.auto_role.in_(auto_roles),
                PermissionSet.id.in_(given),
                PermissionSet.id.in_(by_team),
            ),
        )
    ).tuples()
    return [(str(k), str(v)) for k, v in rows]


def project_items(session: Session, user_id: int, granted: set[int]) -> dict[int, frozenset[str]]:
    """docs/specs/21 §13: the rights the sets of each dự án give `user_id` there, for the dự án
    they still have a grant on (`granted`). Only rights a dự án's set may hold count (a row the
    catalog no longer allows there is dropped, never widened)."""
    if not granted:
        return {}
    rows = session.execute(
        select(
            PermissionSet.business_project_id,
            PermissionSetItem.permission,
            PermissionSetItem.scope,
        )
        .join(PermissionSetItem, PermissionSetItem.set_id == PermissionSet.id)
        .join(UserPermissionSet, UserPermissionSet.set_id == PermissionSet.id)
        .where(
            UserPermissionSet.user_id == user_id,
            PermissionSet.business_project_id.in_(sorted(granted)),
        )
    ).tuples()
    out: dict[int, set[str]] = {}
    for bp, key, scope in rows:
        if bp is None or key not in PROJECT_ASSIGNABLE or not valid_item(str(key), str(scope)):
            continue
        out.setdefault(int(bp), set()).add(str(key))
    return {bp: frozenset(keys) for bp, keys in out.items()}


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


def require_setup() -> Any:
    """FastAPI dependency (docs/specs/21 §10): one who sets up some dự án, i.e. holds
    `admin.projects` or `project.setup`; each endpoint then checks the dự án with
    `guard_setup`."""

    def dependency(principal: CurrentPrincipal) -> Principal:
        if not (principal.can("admin.projects") or principal.can("project.setup")):
            raise HTTPException(403, "thiếu quyền quản trị dự án")
        return principal

    return Depends(dependency)


def guard_setup(principal: Principal, bp_id: int | None) -> None:
    """403 unless `principal` sets up dự án `bp_id` (404 when they cannot even see it; a repo
    outside every dự án is the admins')."""
    if principal.can_setup_project(bp_id):
        return
    if bp_id is not None and not principal.can_view_project(bp_id):
        raise HTTPException(404, "dự án not found")
    raise HTTPException(403, "chỉ Lead / Manager của dự án này (hoặc admin) làm được việc này")


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
        principal = load_principal(session, session_user(session, token, settings).id, settings)
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
