"""Command grammar of the review bot. Pure functions: parsing and matching, no I/O.

Preferred form (order-free key=value pairs; every key but type is optional):

    /review type=mr|merge-request|commit [id=<iid|sha|url>] [repo=<hint>[,<hint>...]]
            [user=<hint>] [range=today|3d|7d|<period>] [again=1]

    type=mr id=412 repo=global-api        review MR !412 of cloud-saas-global-api
    type=commit user=tamtd                the latest commit of tamtd (in the bot's scope)
    type=commit user=tamtd range=7d       tamtd's commits of the last 7 days
    type=commit repo=wallet,customer range=09-2026,10-2026
                                          commits of two repos from 1 Sep to 31 Oct 2026
                                          (a period as in /report: 01-09-2026,15-10-2026 ...)
    type=mr user=an                       the latest MR of An
    ... again=1                           also review again what already has a finished review
                                          (a new round; default: answer with the old result)

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
    /report [user=<hint>|all] [time=<period>] [repo=<hint>]   (app/telegram/report.py)
    /link <code>  /unlink  /whoami  /help

A repo is named by its GitLab path or any suffix of it ("api", "omicrm/api"); a person by
@gitlab-username, email, or part of their name. "me" always means the sender.
"""

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from app.models.source import ALL_BRANCHES
from app.telegram.report import SEP, Period, parse_period
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
    "report",
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
    # repo path hint (argument or taken from a URL); "a,b" names several repos (lists only)
    repo_hint: str | None = None
    people: tuple[str, ...] = ()  # user hints ("me", "@name", email, name part)
    days: int = 1  # window of the "today" action (range=7d -> 7)
    # range=09-2026,10-2026: whole days in REPORT_TZ, end inclusive (clipped to today); None =
    # the rolling `days` window.
    period: Period | None = None
    branch: str | None = None  # branch= (default: repo's review branch, else default; * = all)
    # Keyed form without a single explicit target: show the picker (buttons) instead of queuing
    # everything; all=1 queues everything at once like the legacy actions.
    pick: bool = False
    # again=1: also take items that already have a finished review (each becomes a new round).
    # Default no: a done item answers with its result instead of spending the LLM budget again.
    again: bool = False
    # include_reviewed=1: lists (and "the latest item") also show what already has a review,
    # with its result; default no: only what still needs one. again=1 implies it.
    include_reviewed: bool = False


# include_reviewed: the canonical name; includeReviewed / hasReviewed arrive casefolded.
INCLUDE_REVIEWED_KEYS = ("include_reviewed", "includereviewed", "hasreviewed", "has_reviewed")
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
    "again",
    "rereview",
    *INCLUDE_REVIEWED_KEYS,
}
TRUE_WORDS = {"1", "true", "yes", "y", "on", "có"}
FALSE_WORDS = {"0", "false", "no", "n", "off", "không", "khong"}
TYPES = {
    "mr": "mr",
    "merge-request": "mr",
    "merge_request": "mr",
    "mergerequest": "mr",
    "commit": "commit",
}
RANGE = re.compile(r"^(?:(?P<today>today|hôm nay|homnay)|(?P<n>\d{1,3})\s*d(?:ays?)?)$", re.I)
MAX_RANGE_DAYS = 92  # a quarter: range=07-2026,09-2026
RANGE_HELP = (
    "range: today, 7d, một tháng 09-2026, một ngày 15-10-2026, hoặc khoảng"
    " range=09-2026,10-2026 / range=01-09-2026,15-10-2026"
)


def parse_keyed(args: Sequence[str], today: date | None = None) -> ReviewArgs | str | None:
    """`key=value` form; None when the words are not in that form at all.

    `today` (REPORT_TZ) anchors an absolute range=; the caller passes it, tests may too."""
    pairs: dict[str, str] = {}
    free: list[str] = []
    # "range=09-2026, 10-2026" / "repo=a, b" arrive as two words: glue the separators back.
    for word in SEP.sub(lambda m: m.group(0).strip(), " ".join(args)).split():
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
            " Cú pháp: /review type=mr|commit [id=] [repo=a,b] [branch=] [user=] [range=] [all=1]"
            " [include_reviewed=1] [again=1]"
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
    again_word = (pairs.get("again") or pairs.get("rereview") or "").casefold()
    if again_word and again_word not in TRUE_WORDS and again_word not in FALSE_WORDS:
        return "again chỉ nhận 1/0 (yes/no): again=1 để review lại mục đã review."
    again = again_word in TRUE_WORDS
    shown_word = next((pairs[k] for k in INCLUDE_REVIEWED_KEYS if k in pairs), "").casefold()
    if shown_word and shown_word not in TRUE_WORDS and shown_word not in FALSE_WORDS:
        return (
            "include_reviewed chỉ nhận 1/0 (true/false):"
            " include_reviewed=1 để hiện cả mục đã review."
        )
    include_reviewed = again or shown_word in TRUE_WORDS
    if ref and (from_url := parse_gitlab_url(ref)):
        url_kind, path, value = from_url
        if url_kind != kind:
            return f"Link đó là {url_kind}, không phải {kind}."
        ref, repo = value, repo or path
    repos = [r for r in (repo or "").split(",") if r.strip()]
    if len(repos) > 1 and (ref or (user and not window)):
        return (
            "Nhiều repo (repo=a,b) chỉ dùng cho danh sách commit/MR;"
            " id= hay mục gần nhất cần một repo."
        )
    repo = ",".join(r.strip() for r in repos) or None
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
        return ReviewArgs(
            kind, ref=ref, repo_hint=repo, again=again, include_reviewed=include_reviewed
        )
    people = (user,) if user else ()
    days = 7
    period: Period | None = None
    if window:
        if kind == "mr":
            return (
                "range chỉ dùng với type=commit; với MR hãy dùng id= hoặc bỏ trống để chọn"
                " trong danh sách MR đang mở."
            )
        if m := RANGE.match(window):
            days = 1 if m["today"] else max(1, min(int(m["n"]), MAX_RANGE_DAYS))
        else:
            parsed = parse_period(window, today or date.today())
            if isinstance(parsed, str):
                if "tương lai" in parsed:
                    return "range nằm trong tương lai; chưa có commit nào."
                if "tối đa" in parsed:
                    return f"range tối đa {MAX_RANGE_DAYS} ngày (một quý); hãy chia nhỏ khoảng."
                return f"{parsed.split(' Thời gian:')[0]} {RANGE_HELP}"
            if parsed.days > MAX_RANGE_DAYS:
                return f"range tối đa {MAX_RANGE_DAYS} ngày (một quý); hãy chia nhỏ khoảng."
            period, days = parsed, parsed.days
    # One person, no window: their latest item, queued at once (one tap on the phone).
    if user and not window:
        action: Action = "last" if kind == "commit" else "lastmr"
        return ReviewArgs(
            action,
            repo_hint=repo,
            people=people,
            branch=branch,
            again=again,
            include_reviewed=include_reviewed,
        )
    # Otherwise a list: the picker, unless all=1 asks for everything right away.
    if kind == "commit":
        return ReviewArgs(
            "today",
            repo_hint=repo,
            people=people,
            days=days,
            period=period,
            branch=branch,
            pick=not auto,
            again=again,
            include_reviewed=include_reviewed,
        )
    return ReviewArgs(
        "open",
        repo_hint=repo,
        people=people,
        branch=branch,
        pick=not auto,
        again=again,
        include_reviewed=include_reviewed,
    )


def parse_review(args: Sequence[str], today: date | None = None) -> ReviewArgs | str:
    """Interpret the words after /review; returns an error message on bad usage."""
    if not args:
        return "Thiếu loại review. Dùng /help để xem cú pháp."
    keyed = parse_keyed(args, today)
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
