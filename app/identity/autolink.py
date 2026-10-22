"""Automatic account -> user linking (docs/specs/05). Never overrides an existing link."""

import functools
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, User

log = logging.getLogger(__name__)

NOREPLY = re.compile(r"^(?P<id>\d+)-(?P<username>[^@]+)@users\.noreply\.[^@]+$")
BOT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\[bot\]",
        r"^renovate",
        r"^dependabot",
        r"^project_\d+_bot",
        r"^group_\d+_bot",
        r"(^|[-_.])ci[-_.]?bot",
        r"^ghost$",
    )
]

# gitlab user id -> public email ("" when hidden); None means "do not look it up".
PublicEmailLookup = Callable[[int], str | None]


@dataclass
class LinkResult:
    linked: dict[str, int] = field(default_factory=dict)  # reason -> count
    bots: int = 0


def is_bot(account: Account) -> bool:
    names = [account.external_key, account.display_name or ""]
    return any(p.search(name) for p in BOT_PATTERNS for name in names)


def _domain_and_local(email: str) -> tuple[str, str]:
    local, _, domain = email.partition("@")
    return local, domain


def autolink(
    session: Session,
    allowed_email_domains: Sequence[str],
    public_email: PublicEmailLookup | None = None,
) -> LinkResult:
    result = LinkResult()
    if public_email is not None:
        public_email = functools.cache(public_email)  # one GET /users/:id per GitLab user
    domains = {d.lower() for d in allowed_email_domains}
    users_by_email = {
        u.primary_email.lower(): u for u in session.scalars(select(User)) if u.primary_email
    }
    unlinked = list(session.scalars(select(Account).where(Account.user_id.is_(None))))
    gitlab_accounts = {
        a.external_key: a
        for a in session.scalars(select(Account).where(Account.kind == "gitlab_user"))
    }

    def link(account: Account, user: User | None, reason: str) -> bool:
        if user is None or user.id is None:
            return False
        account.user_id = user.id
        result.linked[reason] = result.linked.get(reason, 0) + 1
        return True

    def user_of(user_id: int | None) -> User | None:
        return session.get(User, user_id) if user_id is not None else None

    # Bots first: they get an inactive user so reports can exclude them.
    for account in [a for a in unlinked if is_bot(a)]:
        bot = User(full_name=account.display_name or account.external_key, active=False)
        session.add(bot)
        session.flush()
        account.user_id = bot.id
        result.bots += 1
    unlinked = [a for a in unlinked if a.user_id is None]

    # GitLab users: their public email, or <username>@<allowed domain>, is a primary email.
    for account in [a for a in unlinked if a.kind == "gitlab_user"]:
        username = (account.display_name or "").lower()
        email = public_email(int(account.external_key)) if public_email else None
        if email and link(account, users_by_email.get(email.lower()), "gitlab_public_email"):
            continue
        for domain in domains:
            if link(account, users_by_email.get(f"{username}@{domain}"), "gitlab_username_domain"):
                break

    # Git emails.
    for account in [a for a in unlinked if a.kind == "git_email" and a.user_id is None]:
        email = account.external_key.lower()
        if link(account, users_by_email.get(email), "primary_email"):
            continue
        if m := NOREPLY.match(email):
            gitlab = gitlab_accounts.get(m["id"])
            if gitlab and link(account, user_of(gitlab.user_id), "gitlab_noreply"):
                continue
        local, domain = _domain_and_local(email)
        if domain in domains:
            gitlab = next(
                (a for a in gitlab_accounts.values() if (a.display_name or "").lower() == local),
                None,
            )
            if gitlab and link(account, user_of(gitlab.user_id), "gitlab_username_domain"):
                continue
        if public_email:
            for gitlab in gitlab_accounts.values():
                if gitlab.user_id is None:
                    continue
                if (public_email(int(gitlab.external_key)) or "").lower() == email:
                    link(account, user_of(gitlab.user_id), "gitlab_public_email")
                    break

    session.flush()
    log.info("identity autolink", extra={"linked": result.linked, "bots": result.bots})
    return result


def unmapped_accounts(session: Session) -> list[Account]:
    """Accounts no person owns yet: what the admin 'Unmapped accounts' view lists."""
    return list(
        session.scalars(
            select(Account)
            .where(Account.user_id.is_(None))
            .order_by(Account.kind, Account.external_key)
        )
    )
