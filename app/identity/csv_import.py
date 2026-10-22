"""Team configuration import from teams.csv / users.csv / accounts.csv (docs/specs/05).

Idempotent: rows are upserted by natural key, so re-importing the same files changes nothing.
"""

import csv
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Team, TeamMember, User

ACCOUNT_KINDS = {"gitlab_user", "git_email"}


class CsvImportError(ValueError):
    """A CSV problem, reported with file and line so the admin can fix it."""


@dataclass
class ImportResult:
    teams: int = 0
    users: int = 0
    memberships: int = 0
    accounts: int = 0
    errors: list[str] = field(default_factory=list)


def _rows(path: Path, required: set[str]) -> Iterable[tuple[int, dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise CsvImportError(f"{path.name}: missing columns {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            yield line, {k: (v or "").strip() for k, v in row.items() if k}


def import_teams(session: Session, path: Path, result: ImportResult) -> None:
    rows = list(_rows(path, {"team_name", "parent_team"}))
    teams = {t.name: t for t in session.scalars(select(Team))}
    for _, row in rows:
        if row["team_name"] not in teams:
            teams[row["team_name"]] = Team(name=row["team_name"])
            session.add(teams[row["team_name"]])
            result.teams += 1
    session.flush()
    for line, row in rows:  # second pass: parents may appear after their children
        parent = row["parent_team"]
        if not parent:
            continue
        if parent not in teams:
            result.errors.append(f"{path.name}:{line}: unknown parent team {parent!r}")
            continue
        teams[row["team_name"]].parent_id = teams[parent].id


def import_users(session: Session, path: Path, result: ImportResult) -> None:
    teams = {t.name: t for t in session.scalars(select(Team))}
    for line, row in _rows(path, {"full_name", "primary_email", "team_name", "role", "joined_at"}):
        email = row["primary_email"].lower()
        if "@" not in email:
            result.errors.append(f"{path.name}:{line}: invalid primary_email {email!r}")
            continue
        user = session.scalars(select(User).where(User.primary_email == email)).first()
        if user is None:
            user = User(primary_email=email)
            session.add(user)
            result.users += 1
        user.full_name = row["full_name"] or user.full_name
        session.flush()

        if not row["team_name"]:
            continue
        team = teams.get(row["team_name"])
        if team is None:
            result.errors.append(f"{path.name}:{line}: unknown team {row['team_name']!r}")
            continue
        try:
            joined = date.fromisoformat(row["joined_at"])
            left = date.fromisoformat(row["left_at"]) if row.get("left_at") else None
        except ValueError:
            result.errors.append(f"{path.name}:{line}: dates must be YYYY-MM-DD")
            continue
        member = session.get(TeamMember, (team.id, user.id, joined))
        if member is None:
            member = TeamMember(team_id=team.id, user_id=user.id, joined_at=joined)
            session.add(member)
            result.memberships += 1
        member.role = row["role"] or member.role
        member.left_at = left


def import_accounts(session: Session, path: Path, result: ImportResult) -> None:
    for line, row in _rows(path, {"kind", "external_key", "primary_email"}):
        kind, key = row["kind"], row["external_key"]
        if kind not in ACCOUNT_KINDS:
            result.errors.append(f"{path.name}:{line}: kind must be one of {sorted(ACCOUNT_KINDS)}")
            continue
        if kind == "git_email":
            key = key.lower()
        elif not key.isdigit():
            result.errors.append(f"{path.name}:{line}: gitlab_user external_key is the numeric id")
            continue
        email = row["primary_email"].lower()
        user = session.scalars(select(User).where(User.primary_email == email)).first()
        if user is None:
            result.errors.append(f"{path.name}:{line}: no user with primary_email {email!r}")
            continue
        account = session.scalars(
            select(Account).where(Account.kind == kind, Account.external_key == key)
        ).first()
        if account is None:
            account = Account(kind=kind, external_key=key)
            session.add(account)
            result.accounts += 1
        account.user_id = user.id  # an explicit import wins over automatic linking
    session.flush()


def import_identity(
    session: Session,
    *,
    teams: Path | None = None,
    users: Path | None = None,
    accounts: Path | None = None,
) -> ImportResult:
    """Import in dependency order; the caller commits only if `errors` is empty."""
    result = ImportResult()
    if teams:
        import_teams(session, teams, result)
    if users:
        import_users(session, users, result)
    if accounts:
        import_accounts(session, accounts, result)
    session.flush()
    return result
