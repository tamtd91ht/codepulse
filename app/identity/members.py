"""GitLab members of a repo or dự án -> CodePulse people (outside docs/specs/05, requested by the
user 2026-10-26; see PROGRESS.md).

docs/specs/05 links accounts as commits and MRs come in, so someone who has not pushed yet has
no account, and an author who is not matched waits in "Unmapped accounts" for a person that
must already exist. Here the admin starts from who has access to the code:

- `fetch_members` lists everyone with access to the repos (inherited group members included),
  once per GitLab user;
- `suggest` proposes, per member, what to do: already linked; link to an existing person on
  firm evidence (same email, `<username>@<allowed domain>`, a commit email of theirs on GitLab's
  noreply or an allowed domain); "review" when the only hint is weak (the same full name: names
  collide), which the screen does not preselect; create a person; or skip (a bot, a blocked
  user);
- `import_members` applies what the admin confirmed, with each member's username and state read
  back from GitLab (never taken from the request). It refuses to move a GitLab account already
  linked to someone else, or to give a person a second GitLab account, unless asked to (`move`):
  sign-in trusts that link, so a wrong one would let one person sign in as another.

Linking the GitLab account is what the rest of the system uses: sign-in finds a person by it
(app/api/auth.py), the Telegram bot resolves `user=<username>` through it, and autolink then
ties the member's commit emails (noreply, `<username>@<domain>`) to the same person.
"""

import logging
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient, GitLabError
from app.identity.autolink import BOT_PATTERNS, NOREPLY, autolink
from app.models import Account, Connection, Project, User

log = logging.getLogger(__name__)

Action = Literal["linked", "link", "review", "create", "skip"]
# GitLab access levels, for the admin to tell a maintainer from a guest.
ACCESS = {
    10: "guest",
    15: "planner",
    20: "reporter",
    30: "developer",
    40: "maintainer",
    50: "owner",
}
MAX_REPOS = 200  # a dự án with more repos than this is listed from its first 200
MAX_MEMBERS = 1000


@dataclass
class Member:
    gitlab_id: int
    username: str
    name: str | None
    state: str  # active | blocked | deactivated | ...
    level: int  # highest GitLab access level over the repos
    connection_id: int  # the GitLab it was listed from: import reads the user back from it
    email: str | None = None  # only when the token may see it, or the user made it public
    repos: list[str] = field(default_factory=list)

    @property
    def access(self) -> str:
        return ACCESS.get(self.level, str(self.level))


@dataclass
class Listing:
    members: list[Member]
    truncated: bool  # more repos than MAX_REPOS, or more members than MAX_MEMBERS


@dataclass
class Suggestion:
    member: Member
    action: Action
    user_id: int | None = None  # linked or suggested person
    reason: str | None = None  # why this person / why skip


def fetch_members(
    projects: Sequence[Project],
    connections: dict[int, Connection],
    make_client: Callable[[Connection], GitLabClient],
    linked: Collection[int] = (),
) -> Listing:
    """Members of every repo, one entry per GitLab user (highest access, every repo listed).

    The public email (one GET /users/:id each) is looked up only for active members not in
    `linked` (GitLab ids already tied to a person): it only serves to suggest a match."""
    found: dict[int, Member] = {}
    truncated = len(projects) > MAX_REPOS
    by_connection: dict[int, list[Project]] = {}
    for project in projects[:MAX_REPOS]:
        if project.connection_id is not None:
            by_connection.setdefault(project.connection_id, []).append(project)
    for connection_id, repos in by_connection.items():
        with make_client(connections[connection_id]) as client:
            mine: set[int] = set()
            for project in repos:
                for raw in client.project_members(project.external_id):
                    gitlab_id = _gitlab_id(raw)
                    if gitlab_id is None:
                        continue
                    if gitlab_id not in found and len(found) >= MAX_MEMBERS:
                        truncated = True
                        break  # the rest of this repo's pages would add nobody
                    _merge(found, raw, project.path_with_namespace, connection_id)
                    mine.add(gitlab_id)
            for gitlab_id in mine - set(linked):
                member = found[gitlab_id]
                if member.email is None and member.state == "active" and not is_bot(member):
                    member.email = _public_email(client, gitlab_id)
    members = sorted(found.values(), key=lambda m: (m.name or m.username).casefold())
    return Listing(members, truncated)


def _gitlab_id(raw: dict[str, Any]) -> int | None:
    try:
        return int(raw["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _merge(found: dict[int, Member], raw: dict[str, Any], repo: str, connection_id: int) -> None:
    gitlab_id = _gitlab_id(raw)
    if gitlab_id is None:
        return
    try:
        level = int(raw.get("access_level") or 0)
    except (TypeError, ValueError):
        level = 0
    member = found.get(gitlab_id)
    if member is None:
        member = Member(
            gitlab_id=gitlab_id,
            username=str(raw.get("username") or gitlab_id),
            name=raw.get("name") or None,
            state=str(raw.get("state") or "active"),
            level=level,
            connection_id=connection_id,
            email=(str(raw["email"]).lower() if raw.get("email") else None),
        )
        found[gitlab_id] = member
    member.level = max(member.level, level)
    if repo not in member.repos:
        member.repos.append(repo)


def _public_email(client: GitLabClient, gitlab_id: int) -> str | None:
    try:
        email = client.user(gitlab_id).get("public_email")
    except GitLabError:
        return None  # a hidden or deleted user must not fail the whole list
    return str(email).lower() if email else None


def is_bot(member: Member) -> bool:
    names = (member.username, member.name or "")
    return any(p.search(name) for p in BOT_PATTERNS for name in names)


def suggest(
    session: Session, members: Iterable[Member], allowed_email_domains: Sequence[str]
) -> list[Suggestion]:
    """What to do with each member, from what CodePulse already knows. Read-only."""
    members = list(members)
    accounts = {
        a.external_key: a
        for a in session.scalars(
            select(Account).where(
                Account.kind == "gitlab_user",
                Account.external_key.in_([str(m.gitlab_id) for m in members]),
            )
        )
    }
    users = list(session.scalars(select(User)))
    # Only people the admin can pick: a deactivated person is never suggested.
    active = [u for u in users if u.active is not False]
    by_email = {u.primary_email.lower(): u for u in active if u.primary_email}
    names: dict[str, list[User]] = {}
    for u in active:
        if u.full_name:
            names.setdefault(u.full_name.casefold().strip(), []).append(u)
    domains = [d.lower() for d in allowed_email_domains]
    # Commit emails already tied to a person: GitLab's noreply with the id, or <username>@ an
    # allowed domain (where the local part is the username; on gmail.com it says nothing).
    by_local: dict[str, set[int]] = {}
    by_noreply: dict[str, int] = {}
    for key, user_id in session.execute(
        select(Account.external_key, Account.user_id).where(
            Account.kind == "git_email", Account.user_id.is_not(None)
        )
    ):
        if user_id is None:
            continue
        if m := NOREPLY.match(key):
            by_noreply[m["id"]] = user_id
        local, _, domain = key.lower().partition("@")
        if domain in domains:
            by_local.setdefault(local, set()).add(user_id)
    people = {u.id: u for u in active}

    out: list[Suggestion] = []
    for member in members:
        account = accounts.get(str(member.gitlab_id))
        if account is not None and account.user_id is not None:
            out.append(Suggestion(member, "linked", account.user_id))
            continue
        if is_bot(member):
            out.append(Suggestion(member, "skip", None, "bot"))
            continue
        if member.state != "active":
            out.append(Suggestion(member, "skip", None, member.state))
            continue
        user, reason, firm = _match(member, by_email, domains, by_noreply, by_local, names, people)
        if user is not None:
            out.append(Suggestion(member, "link" if firm else "review", user.id, reason))
        else:
            out.append(Suggestion(member, "create"))
    return out


def _match(
    member: Member,
    by_email: dict[str, User],
    domains: list[str],
    by_noreply: dict[str, int],
    by_local: dict[str, set[int]],
    names: dict[str, list[User]],
    people: dict[int, User],
) -> tuple[User | None, str | None, bool]:
    """(person, why, firm). Not firm: only a hint the admin must confirm."""
    if member.email and (user := by_email.get(member.email)):
        return user, "same_email", True
    username = member.username.lower()
    for domain in domains:
        if user := by_email.get(f"{username}@{domain}"):
            return user, "username_domain", True
    if (user_id := by_noreply.get(str(member.gitlab_id))) and user_id in people:
        return people[user_id], "commit_noreply", True
    owners = by_local.get(username, set())
    if len(owners) == 1 and (only := next(iter(owners))) in people:
        return people[only], "commit_email", True
    same_name = names.get((member.name or "").casefold().strip(), [])
    if len(same_name) == 1:
        return same_name[0], "same_name", False
    return None, None, False


# -- import ------------------------------------------------------------------------------------


@dataclass
class Choice:
    gitlab_id: int
    connection_id: int
    action: Literal["link", "create", "skip"]
    user_id: int | None = None  # link
    full_name: str | None = None  # create
    email: str | None = None  # create
    move: bool = False  # link even when the account or the person is linked elsewhere


@dataclass
class GitLabUser:
    """A member as GitLab says it is at import time."""

    username: str
    name: str | None
    state: str


@dataclass
class ImportResult:
    created: list[int] = field(default_factory=list)  # new user ids
    linked: int = 0
    skipped: int = 0
    # One per linked member, for the audit: who went to whom, and from whom when moved.
    items: list[dict[str, Any]] = field(default_factory=list)
    autolinked: dict[str, int] = field(default_factory=dict)  # autolink's reason -> count

    @property
    def emails_linked(self) -> int:
        return sum(self.autolinked.values())


class ImportRefused(ValueError):
    """A choice that cannot be applied as asked; nothing of the import is kept."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def resolve(client: GitLabClient, gitlab_ids: Iterable[int]) -> dict[int, GitLabUser]:
    """Each id's username, name and state from GitLab (GET /users/:id); unknown ids are absent."""
    out: dict[int, GitLabUser] = {}
    for gitlab_id in gitlab_ids:
        try:
            raw = client.user(gitlab_id)
        except GitLabError as exc:
            if exc.status_code == 404:
                continue
            raise
        out[gitlab_id] = GitLabUser(
            username=str(raw.get("username") or gitlab_id),
            name=raw.get("name") or None,
            state=str(raw.get("state") or "active"),
        )
    return out


def import_members(
    session: Session,
    choices: Sequence[Choice],
    gitlab: dict[int, GitLabUser],
    allowed_email_domains: Sequence[str],
) -> ImportResult:
    """Create or pick each confirmed person and link the member's GitLab account to them.

    `gitlab`: the members as GitLab reports them now (`resolve`); the username stored, and
    used by the Telegram bot and autolink, comes from there. Raises ImportRefused before
    anything is committed: an unknown or blocked GitLab user, an email another person has, an
    account linked to someone else or a person with another GitLab account (without `move`)."""
    result = ImportResult()
    taken = {
        e.lower()
        for e in session.scalars(select(User.primary_email).where(User.primary_email.is_not(None)))
        if e
    }
    for choice in choices:
        if choice.action == "skip":
            result.skipped += 1
            continue
        member = gitlab.get(choice.gitlab_id)
        if member is None:
            raise ImportRefused("unknown_gitlab_user", f"GitLab user {choice.gitlab_id} not found")
        if member.state != "active":
            raise ImportRefused("inactive", f"@{member.username} is {member.state} on GitLab")
        account = session.scalars(
            select(Account).where(
                Account.kind == "gitlab_user", Account.external_key == str(choice.gitlab_id)
            )
        ).first()
        previous = account.user_id if account else None
        if choice.action == "create":
            email = (choice.email or "").strip().lower() or None
            if email is not None:
                if email in taken:
                    raise ImportRefused("email_taken", email)
                taken.add(email)
            if previous is not None and not choice.move:
                raise ImportRefused("account_linked", f"@{member.username}")
            user = User(
                full_name=(choice.full_name or member.name or member.username).strip(),
                primary_email=email,
            )
            session.add(user)
            session.flush()
            result.created.append(user.id)
            user_id = user.id
        else:
            target = session.get(User, choice.user_id) if choice.user_id is not None else None
            if target is None:
                raise ImportRefused("user_not_found", f"user {choice.user_id} not found")
            user_id = target.id
            if previous is not None and previous != user_id and not choice.move:
                raise ImportRefused("account_linked", f"@{member.username}")
            other = session.scalars(
                select(Account.external_key).where(
                    Account.kind == "gitlab_user",
                    Account.user_id == user_id,
                    Account.external_key != str(choice.gitlab_id),
                )
            ).first()
            if other is not None and not choice.move:
                raise ImportRefused("person_has_account", f"user {user_id}")
        if account is None:
            account = Account(kind="gitlab_user", external_key=str(choice.gitlab_id))
            session.add(account)
        account.display_name = member.username
        account.user_id = user_id
        result.linked += 1
        result.items.append(
            {
                "gitlab_id": choice.gitlab_id,
                "username": member.username,
                "choice": choice.action,
                "user_id": user_id,
                "previous_user_id": previous,
            }
        )
    session.flush()
    # The member's commit emails (noreply, <username>@<domain>) now find the person too. This
    # runs the usual autolink over every unlinked account, as a sync would.
    result.autolinked = dict(autolink(session, allowed_email_domains).linked)
    return result


def link_email(
    session: Session, email: str, name: str | None, user_id: int
) -> tuple[Account, int | None]:
    """The commit author's git_email account, created when needed, linked to `user_id`.
    Returns it with its previous owner; an account another person owns is refused."""
    key = email.strip().lower()
    account = session.scalars(
        select(Account).where(Account.kind == "git_email", func.lower(Account.external_key) == key)
    ).first()
    previous = account.user_id if account else None
    if previous is not None and previous != user_id:
        raise ImportRefused("account_linked", key)
    if account is None:
        account = Account(kind="git_email", external_key=key, display_name=name)
        session.add(account)
    account.user_id = user_id
    session.flush()
    return account, previous
