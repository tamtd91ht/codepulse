"""Command grammar of the review bot. Pure functions: parsing and matching, no I/O.

    /review mr <iid|url> [repo]        review one merge request
    /review commit <sha> [repo]        review one commit
    /review last [repo] [user]         the latest commit of a person (default: you)
    /review lastmr [repo] [user]       the latest merge request of a person
    /review today [repo] [users...]    today's commits of one or more people
    /review open [repo]                open MRs that have no review yet (lead)
    /status  /critical [repo]  /score [user] [week|month]  /repos  /sync [repo]
    /link <code>  /unlink  /whoami  /help

A repo is named by its GitLab path or any suffix of it ("api", "omicrm/api"); a person by
@gitlab-username, email, or part of their name. "me" always means the sender.
"""

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

Action = Literal["mr", "commit", "last", "lastmr", "today", "open"]
ACTIONS: tuple[Action, ...] = ("mr", "commit", "last", "lastmr", "today", "open")
# Shorthands: "/mr 12" == "/review mr 12", "/today" == "/review today".
ALIASES: dict[str, Action] = {
    "mr": "mr",
    "commit": "commit",
    "last": "last",
    "lastmr": "lastmr",
    "today": "today",
    "open": "open",
}
KNOWN = {
    "start",
    "help",
    "link",
    "unlink",
    "whoami",
    "repos",
    "status",
    "critical",
    "score",
    "sync",
}

SHA = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
MR_URL = re.compile(r"^/(?P<path>.+?)/-/merge_requests/(?P<iid>\d+)(?:/.*)?$")
COMMIT_URL = re.compile(r"^/(?P<path>.+?)/-/commit/(?P<sha>[0-9a-f]{7,40})", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    name: str  # "review", "help", ...
    args: tuple[str, ...]
    raw: str


def parse(text: str, bot_username: str | None = None) -> Command | None:
    """None when the text is not a command for this bot (plain chat, or /cmd@OtherBot)."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    head, *rest = text.split()
    name, _, mention = head[1:].partition("@")
    if mention and bot_username and mention.casefold() != bot_username.casefold():
        return None
    name = name.casefold()
    if name in ALIASES:
        return Command("review", (ALIASES[name], *rest), text)
    return Command(name, tuple(rest), text)


@dataclass(frozen=True)
class ReviewArgs:
    action: Action
    ref: str | None = None  # MR iid or commit sha when given directly
    repo_hint: str | None = None  # repo path hint (argument or taken from a URL)
    people: tuple[str, ...] = ()  # user hints ("me", "@name", email, name part)


def parse_review(args: Sequence[str]) -> ReviewArgs | str:
    """Interpret the words after /review; returns an error message on bad usage."""
    if not args:
        return "Thiếu loại review. Dùng /help để xem cú pháp."
    action = args[0].casefold()
    rest = list(args[1:])
    if action not in ACTIONS:
        return f"Không hiểu '/review {args[0]}'. Dùng /help để xem cú pháp."
    action_t: Action = action
    if action_t in ("mr", "commit"):
        if not rest:
            what, example = ("số MR", "123") if action_t == "mr" else ("mã commit", "a1b2c3d")
            return f"Thiếu {what}. Ví dụ: /review {action_t} {example} api"
        ref, repo = rest[0], (rest[1] if len(rest) > 1 else None)
        if from_url := parse_gitlab_url(ref):
            kind, path, value = from_url
            if kind != action_t:
                return f"Link đó là {kind}, không phải {action_t}."
            return ReviewArgs(action_t, ref=value, repo_hint=path)
        ref = ref.lstrip("!#")
        if action_t == "mr" and not ref.isdigit():
            return "Số MR phải là số (ví dụ 123 hoặc !123) hoặc link merge request."
        if action_t == "commit" and not SHA.match(ref):
            return "Mã commit phải là 7-40 ký tự hex, hoặc link commit."
        return ReviewArgs(action_t, ref=ref, repo_hint=repo)
    if action_t == "open":
        return ReviewArgs(action_t, repo_hint=rest[0] if rest else None)
    # last / lastmr / today: [repo] [people...]; a repo never contains '@' and is not an email.
    repo_hint: str | None = None
    people: list[str] = []
    for word in rest:
        if repo_hint is None and not people and not looks_like_person(word):
            repo_hint = word
        else:
            people.append(word)
    return ReviewArgs(action_t, repo_hint=repo_hint, people=tuple(people))


def looks_like_person(word: str) -> bool:
    return word.startswith("@") or "@" in word or word.casefold() == "me"


def parse_gitlab_url(text: str) -> tuple[str, str, str] | None:
    """('mr', 'group/repo', '12') or ('commit', 'group/repo', 'sha') from a GitLab web URL."""
    if "://" not in text:
        return None
    path = urlsplit(text).path
    if m := MR_URL.match(path):
        return "mr", m["path"].strip("/"), m["iid"]
    if m := COMMIT_URL.match(path):
        return "commit", m["path"].strip("/"), m["sha"]
    return None


def match_repo[T](projects: Iterable[T], hint: str | None, path_of: Callable[[T], str]) -> list[T]:
    """Repos whose GitLab path equals the hint or ends with "/<hint>"; all when no hint."""
    items = list(projects)
    if not hint:
        return items
    needle = hint.strip("/").casefold()
    exact = [p for p in items if path_of(p).casefold() == needle]
    if exact:
        return exact
    suffix = [p for p in items if path_of(p).casefold().endswith(f"/{needle}")]
    if suffix:
        return suffix
    return [p for p in items if needle in path_of(p).casefold()]
