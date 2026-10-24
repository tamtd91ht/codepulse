"""Command grammar of the review bot. Pure functions: parsing and matching, no I/O.

Preferred form (order-free key=value pairs; every key but type is optional):

    /review type=mr|merge-request|commit [id=<iid|sha|url>] [repo=<hint>] [user=<hint>]
            [range=today|3d|7d|30d]

    type=mr id=412 repo=global-api        review MR !412 of cloud-saas-global-api
    type=commit user=tamtd                the latest commit of tamtd (in the bot's scope)
    type=commit user=tamtd range=7d       tamtd's commits of the last 7 days
    type=mr user=an                       the latest MR of An

Hints may be abbreviated ("global-api" for "cloud-saas-global-api", "tamtd" for
tamtd@company.vn); the bot resolves them, asking an LLM when several items fit.

Legacy positional form, still accepted:

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

from app.models.source import ALL_BRANCHES
from app.telegram.resolve import rank

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
    days: int = 1  # window of the "today" action (range=7d -> 7)
    branch: str | None = None  # branch= (default: repo's review branch, else default; * = all)
    # Keyed form without a single explicit target: show the picker (buttons) instead of queuing
    # everything; all=1 queues everything at once like the legacy actions.
    pick: bool = False


KEYS = {
    "type",
    "id",
    "sha",
    "mr",
    "repo",
    "project",
    "user",
    "author",
    "range",
    "since",
    "branch",
    "ref",
    "all",
    "auto",
}
TRUE_WORDS = {"1", "true", "yes", "y", "on", "có"}
TYPES = {
    "mr": "mr",
    "merge-request": "mr",
    "merge_request": "mr",
    "mergerequest": "mr",
    "commit": "commit",
}
RANGE = re.compile(r"^(?:(?P<today>today|hôm nay|homnay)|(?P<n>\d{1,3})\s*d(?:ays?)?)$", re.I)
MAX_RANGE_DAYS = 90


def parse_keyed(args: Sequence[str]) -> ReviewArgs | str | None:
    """`key=value` form; None when the words are not in that form at all."""
    pairs: dict[str, str] = {}
    free: list[str] = []
    for word in args:
        key, sep, value = word.partition("=")
        if sep and key.casefold() in KEYS:
            pairs[key.casefold()] = value.strip().strip("\"'")
        else:
            free.append(word)
    if not pairs:
        return None
    if free:
        return (
            f"Không hiểu '{' '.join(free)}'."
            " Cú pháp: /review type=mr|commit [id=] [repo=] [branch=] [user=] [range=] [all=1]"
        )
    kind_name = TYPES.get(pairs.get("type", "").casefold())
    if kind_name is None:
        return "Thiếu hoặc sai type. Dùng type=mr (merge-request) hoặc type=commit."
    kind: Action = "mr" if kind_name == "mr" else "commit"
    ref = pairs.get("id") or pairs.get("sha") or pairs.get("mr") or None
    repo = pairs.get("repo") or pairs.get("project") or None
    user = pairs.get("user") or pairs.get("author") or None
    window = pairs.get("range") or pairs.get("since") or None
    branch = pairs.get("branch") or pairs.get("ref") or None
    if branch and branch.casefold() in ("*", "all"):
        branch = ALL_BRANCHES  # every branch
    auto = (pairs.get("all") or pairs.get("auto") or "").casefold() in TRUE_WORDS
    if ref and (from_url := parse_gitlab_url(ref)):
        url_kind, path, value = from_url
        if url_kind != kind:
            return f"Link đó là {url_kind}, không phải {kind}."
        ref, repo = value, repo or path
    if ref and user:
        return (
            "id= đã chỉ rõ một MR/commit; bỏ user= (hoặc bỏ id= để lấy mục gần nhất của người đó)."
        )
    if ref:
        ref = ref.lstrip("!#")
        if kind == "mr" and not ref.isdigit():
            return "id của merge request phải là số (ví dụ id=412) hoặc link."
        if kind == "commit" and not SHA.match(ref):
            return "id của commit phải là 7-40 ký tự hex hoặc link commit."
        return ReviewArgs(kind, ref=ref, repo_hint=repo)
    people = (user,) if user else ()
    days = 7
    if window:
        m = RANGE.match(window)
        if not m:
            return "range phải là today hoặc số ngày, ví dụ range=7d."
        days = 1 if m["today"] else max(1, min(int(m["n"]), MAX_RANGE_DAYS))
        if kind == "mr":
            return (
                "range chỉ dùng với type=commit; với MR hãy dùng id= hoặc bỏ trống để chọn"
                " trong danh sách MR đang mở."
            )
    # One person, no window: their latest item, queued at once (one tap on the phone).
    if user and not window:
        action: Action = "last" if kind == "commit" else "lastmr"
        return ReviewArgs(action, repo_hint=repo, people=people, branch=branch)
    # Otherwise a list: the picker, unless all=1 asks for everything right away.
    if kind == "commit":
        return ReviewArgs(
            "today", repo_hint=repo, people=people, days=days, branch=branch, pick=not auto
        )
    return ReviewArgs("open", repo_hint=repo, people=people, branch=branch, pick=not auto)


def parse_review(args: Sequence[str]) -> ReviewArgs | str:
    """Interpret the words after /review; returns an error message on bad usage."""
    if not args:
        return "Thiếu loại review. Dùng /help để xem cú pháp."
    keyed = parse_keyed(args)
    if keyed is not None:
        return keyed
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
    """Repos a hint may mean, best first; all when no hint.

    Exact path, then "/<hint>" suffix, then substring, then the last path segment ending with
    the hint ("global-api" -> "vihat/cloud-saas-global-api"), then token overlap. Several
    results mean the caller should ask (or let the resolver pick)."""
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
    inside = [p for p in items if needle in path_of(p).casefold()]
    if inside:
        return inside
    ranked = rank(needle, [path_of(p) for p in items])
    if not ranked:
        return []
    best = ranked[0][0]
    return [items[i] for score, i in ranked if score >= best - 0.25]
