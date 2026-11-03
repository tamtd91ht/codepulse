"""One person's data and one team's numbers under permission sets (docs/specs/21 §2, §5).

`Principal.covers` answers for work in a dự án. A person's evaluation, stored scores or evidence
pack spans every dự án they work in, and a team's numbers belong to no dự án, so these helpers
read the same scopes person by person and team by team:

- `own`: yourself;
- `team`: also the people you lead (`lead_members`). Never the people of a team you only manage:
  a manager sees team aggregates, never per-person rows (Phase 0, a hard rule docs/specs/21 §3
  keeps in code; `covers` counts managed members in the "team" scope, so it is not used here);
- `project`: also everyone in a dự án you manage (granted on it, or an author in its repos);
- `all`: also everyone in a dự án you can access; "Quản lý tổng" (and admin) everyone.

For teams: `own` is your own teams, `team` the teams you lead or manage, `all` every team.
"""

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.api.access import _people_of, project_people
from app.api.security import Principal
from app.models import Account, ProjectGrant
from app.permissions.catalog import FLAG, RANK


def _person_in(session: Session, bp_ids: set[int], user_id: int) -> bool:
    """`user_id` is part of one of dự án `bp_ids` (`access._people_of`): three EXISTS."""
    if not bp_ids:
        return False
    granted, committed, opened = _people_of(list(bp_ids))
    return bool(
        session.scalar(
            select(
                or_(
                    exists(granted.where(ProjectGrant.user_id == user_id)),
                    exists(committed.where(Account.user_id == user_id)),
                    exists(opened.where(Account.user_id == user_id)),
                )
            )
        )
    )


def _projects_for(principal: Principal, held: str) -> set[int]:
    wanted = principal.managed_projects() if held == "project" else principal.visible_projects()
    return wanted or set()


def covers_person(
    session: Session,
    principal: Principal,
    key: str,
    user_id: int,
    *,
    leads: bool | None = None,
) -> bool:
    """Holds `key` over `user_id`'s personal data. `leads`: whether the viewer is that person's
    lead, when a feature defines it more narrowly than `lead_members` (evidence packs: the
    direct lead, docs/specs/20 D4)."""
    if principal.is_admin:
        return True
    held = principal.scope(key)
    if held is None:
        return False
    if held == FLAG or user_id == principal.user_id:
        return True
    if held == "own":
        return False
    if (user_id in principal.lead_members) if leads is None else leads:
        return True
    if held == "team":
        return False
    if held == "all" and principal.manages_all:
        return True
    return _person_in(session, _projects_for(principal, held), user_id)


def person_scope(session: Session, principal: Principal, key: str) -> set[int] | None:
    """Everyone `covers_person` lets `principal` see for `key`; None: everyone."""
    if principal.is_admin:
        return None
    held = principal.scope(key)
    if held is None:
        return set()
    if held == FLAG:
        return None
    people = {principal.user_id}
    if held == "own":
        return people
    people |= principal.lead_members
    if held == "team":
        return people
    if held == "all" and principal.manages_all:
        return None
    bps = _projects_for(principal, held)
    return people | project_people(session, bps) if bps else people


def at_least(principal: Principal, key: str, scope: str) -> bool:
    """Holds `key` at `scope` or wider."""
    held = principal.scope(key)
    return held is not None and RANK[held] >= RANK[scope]


def team_scope(principal: Principal, key: str) -> set[int] | None:
    """The teams `key` covers; None: every team."""
    if principal.is_admin:
        return None
    held = principal.scope(key)
    if held is None:
        return set()
    if held in ("all", FLAG):
        return None
    if held == "own":
        return set(principal.own_teams)
    return principal.lead_teams | principal.managed_teams


def covers_team(principal: Principal, key: str, team_id: int) -> bool:
    teams = team_scope(principal, key)
    return teams is None or team_id in teams


def sees_team(principal: Principal, team_id: int) -> bool:
    """Team aggregates: one's own teams, anonymous, for everyone (no catalog permission holds
    this yet: the member set has no `metrics.team`), and the teams `metrics.team` covers."""
    return team_id in principal.own_teams or covers_team(principal, "metrics.team", team_id)


def visible_team_ids(principal: Principal) -> set[int] | None:
    """The teams `sees_team` opens; None: every team."""
    teams = team_scope(principal, "metrics.team")
    return None if teams is None else principal.own_teams | teams
