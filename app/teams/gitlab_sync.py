"""A team kept in sync with a GitLab group (docs/specs/31 §20.7, R2).

A team with `gitlab_group_path` follows the **direct** members of that group: members inherited
from parent groups are not read (a QA group under "omicx" would otherwise pull in the whole
company), nor are those of its subgroups (give them their own team). Each GitLab member maps to
a person through their `gitlab_user` account (`external_key` = GitLab user id); then:

- a mapped, active person who is not in the team today is added (role "member", joined today,
  `source = "gitlab"`);
- a row the sync added (`source = "gitlab"`), role member, of someone no longer in the group
  ends today (`left_at`); rows added by hand and lead / manager rows are never touched;
- GitLab members without a linked person are listed in the outcome, never created; blocked
  users, pending invitations, "minimal access" members and bots are skipped.

Permission sets given to the team (docs/specs/21 §11) follow membership on their own.

The group is read through the first GitLab connection that can see it (the connection the
repos use); a failure leaves the team as it was and is kept in `gitlab_sync_error`.
"""

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.identity.autolink import BOT_PATTERNS
from app.models import Account, Connection, Team, TeamMember, User

log = logging.getLogger(__name__)

ClientFactory = Callable[[Connection], GitLabClient]

SOURCE_GITLAB = "gitlab"
PROTECTED_ROLES = {"lead", "manager"}
MIN_ACCESS = 10  # guest; 5 is "minimal access", which sees no project
MAX_PATH = 255
_PATH = re.compile(r"^[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*$")


class GroupNotFound(Exception):
    """No GitLab connection sees the group."""


def normalize_group_path(raw: str) -> str:
    """'omicx/qa', '/omicx/qa/' or 'https://gitlab.x/omicx/qa' -> 'omicx/qa'; ValueError if it
    is not a group path."""
    text = raw.strip()
    if text.startswith(("http://", "https://")):
        text = urlsplit(text).path
        text = text.split("/-/", 1)[0]  # https://gitlab.x/groups/omicx/qa/-/group_members
        if text.startswith("/groups/"):
            text = text[len("/groups") :]
    text = text.strip("/")
    if not text or len(text) > MAX_PATH or not _PATH.match(text):
        raise ValueError("đường dẫn nhóm GitLab không hợp lệ (ví dụ omicx/qa)")
    return text


@dataclass
class Unmapped:
    gitlab_id: int
    username: str
    name: str | None


@dataclass
class SyncOutcome:
    team_id: int
    group_path: str
    added: list[int] = field(default_factory=list)  # user ids
    ended: list[int] = field(default_factory=list)  # user ids
    unmapped: list[Unmapped] = field(default_factory=list)
    skipped: int = 0  # bots, blocked users, pending invitations, inactive people
    error: str | None = None


def _is_bot(member: dict[str, Any]) -> bool:
    names = (str(member.get("username") or ""), str(member.get("name") or ""))
    return bool(member.get("bot")) or any(p.search(n) for p in BOT_PATTERNS for n in names)


def _counts(member: dict[str, Any]) -> bool:
    return (
        member.get("state", "active") == "active"
        and member.get("membership_state", "active") == "active"
        and int(member.get("access_level") or MIN_ACCESS) >= MIN_ACCESS
        and not _is_bot(member)
    )


def fetch_group_members(
    session: Session, make_client: ClientFactory, group_path: str
) -> list[dict[str, Any]]:
    """The group's direct members, from the first GitLab connection that sees the group (one
    that answers 404 / 403 is passed over). Other failures raise GitLabError."""
    connections = session.scalars(
        select(Connection).where(Connection.kind == "gitlab").order_by(Connection.id)
    ).all()
    for connection in connections:
        try:
            with make_client(connection) as client:
                return list(client.group_members(group_path))
        except GitLabError as exc:
            if exc.status_code in (403, 404):
                continue
            raise
    raise GroupNotFound(f"không kết nối GitLab nào thấy nhóm {group_path}")


def _present(row: TeamMember, today: date) -> bool:
    """In the team today, or set to join later (not added again)."""
    return row.left_at is None or row.left_at > today


def apply_members(
    session: Session,
    team: Team,
    members: Iterable[dict[str, Any]],
    today: date,
    actor_user_id: int | None = None,
) -> SyncOutcome:
    """Bring the team's rows in line with the GitLab members read (the caller commits)."""
    outcome = SyncOutcome(team_id=team.id, group_path=team.gitlab_group_path or "")
    counted: dict[int, dict[str, Any]] = {}
    for member in members:
        if not _counts(member):
            outcome.skipped += 1
            continue
        counted[int(member["id"])] = member
    # A comprehension, not dict(result): a Result has keys() (its columns), so dict() would
    # index it by column name.
    linked: dict[str, int | None] = {
        key: user_id
        for key, user_id in session.execute(
            select(Account.external_key, Account.user_id).where(
                Account.kind == "gitlab_user",
                Account.external_key.in_([str(i) for i in counted]),
                Account.user_id.is_not(None),
            )
        )
    }
    inactive = set(
        session.scalars(
            select(User.id).where(
                User.id.in_([u for u in linked.values() if u is not None]),
                User.active.is_(False),
            )
        )
    )
    wanted: set[int] = set()
    for gitlab_id, member in sorted(counted.items()):
        user_id = linked.get(str(gitlab_id))
        if user_id is None:
            outcome.unmapped.append(
                Unmapped(gitlab_id, str(member.get("username") or ""), member.get("name"))
            )
        elif user_id in inactive:
            outcome.skipped += 1
        else:
            wanted.add(user_id)

    rows = list(session.scalars(select(TeamMember).where(TeamMember.team_id == team.id)))
    present = {r.user_id for r in rows if _present(r, today)}
    for user_id in sorted(wanted - present):
        # Ended today (by hand or by an earlier sync) and back in the group: the same row.
        row = next((r for r in rows if r.user_id == user_id and r.joined_at == today), None)
        if row is None:
            session.add(
                TeamMember(
                    team_id=team.id,
                    user_id=user_id,
                    joined_at=today,
                    role="member",
                    source=SOURCE_GITLAB,
                )
            )
        else:
            row.left_at = None
        outcome.added.append(user_id)
    for row in rows:
        if (
            row.source == SOURCE_GITLAB
            and (row.role or "member").lower() not in PROTECTED_ROLES
            and row.user_id not in wanted
            and row.joined_at <= today
            and _present(row, today)
        ):
            row.left_at = today
            outcome.ended.append(row.user_id)
    if outcome.added or outcome.ended:
        audit(
            session,
            actor_user_id,
            "team.gitlab_sync",
            f"team:{team.id}",
            group=team.gitlab_group_path,
            added=outcome.added,
            ended=outcome.ended,
        )
    return outcome


def sync_team(
    session: Session,
    team_id: int,
    make_client: ClientFactory,
    *,
    actor_user_id: int | None = None,
    now: datetime | None = None,
) -> SyncOutcome | None:
    """Read the team's group from GitLab, then apply it under a lock on the team row (a button
    press and the hourly job never add the same row twice). None: the team is gone or has no
    group. A GitLab failure changes no member and is kept in `gitlab_sync_error`. The caller
    commits."""
    team = session.get(Team, team_id)
    if team is None or not team.gitlab_group_path:
        return None
    path = team.gitlab_group_path
    moment = now or datetime.now(UTC)
    members: list[dict[str, Any]] | None = None
    error: str | None = None
    try:
        members = fetch_group_members(session, make_client, path)
    except GroupNotFound as exc:
        error = str(exc)
    except GitLabError as exc:
        error = f"GitLab lỗi khi đọc nhóm {path}: {exc}"
    team = session.get(Team, team_id, with_for_update=True, populate_existing=True)
    if team is None or team.gitlab_group_path != path:
        return None  # removed or pointed elsewhere meanwhile: the next sync reads the new one
    team.gitlab_sync_at = moment
    team.gitlab_sync_error = error
    if members is None:
        log.warning("team gitlab sync failed", extra={"team_id": team_id, "error": error})
        return SyncOutcome(team_id=team_id, group_path=path, error=error)
    outcome = apply_members(session, team, members, moment.date(), actor_user_id)
    log.info(
        "team gitlab sync",
        extra={
            "team_id": team_id,
            "added": len(outcome.added),
            "ended": len(outcome.ended),
            "unmapped": len(outcome.unmapped),
        },
    )
    return outcome


def linked_team_ids(session: Session) -> list[int]:
    return list(
        session.scalars(
            select(Team.id).where(Team.gitlab_group_path.is_not(None)).order_by(Team.id)
        )
    )
