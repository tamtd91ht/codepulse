"""What the bot does with a command: identity, authorization, queueing reviews, answers.

Rules (same as the dashboard, docs/specs/09, 11): a person may ask for reviews of their own
work; a lead may ask for anyone in the teams they lead; an admin for anyone. Reviews run
through the normal worker pipeline, so scores and findings land in the same tables and the
same weekly/monthly metrics as reviews started by webhooks.
"""

import html
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.security import Principal, load_principal
from app.collector.gitlab.client import GitLabError
from app.collector.sync import SYNC_JOB_TIMEOUT, ClientFactory, Enqueuer
from app.config import Settings
from app.models import (
    Account,
    BusinessProject,
    Commit,
    Connection,
    MergeRequest,
    MetricsUserPeriod,
    Project,
    Review,
    ReviewFinding,
    TelegramBot,
    TelegramRequest,
    User,
)
from app.models.source import ALL_BRANCHES
from app.review.rubric import RUBRIC_VERSION
from app.scoring.verdict import VERDICT_LABEL
from app.telegram.commands import (
    MAX_RANGE_DAYS,
    Command,
    ReviewArgs,
    match_repo,
    parse,
    parse_review,
)
from app.telegram.picker import (
    MAX_ITEMS,
    Item,
    MemoryPickerStore,
    PickerState,
    PickerStore,
    all_branches_row,
    decode,
    keyboard,
    menu_keyboard,
    new_token,
    people_keyboard,
    render_text,
    repo_keyboard,
    repo_pages,
)
from app.telegram.resolve import NoResolver, Resolver
from app.webhook.jobs import REVIEW_RETRY, review_job_timeout

log = logging.getLogger(__name__)

VERDICT_ICON = {"approve": "✅", "needs_human": "⚠️", "fix_required": "⚠️", "block": "❌"}
TELEGRAM_KIND = "telegram"
# docs/specs/09 "Phân quyền theo dự án": a bot serves one dự án, so its users need a grant on it.
NOT_GRANTED = "Bạn chưa được cấp quyền vào dự án của bot này; liên hệ admin."
LINK_CODE_TTL_S = 15 * 60
LINK_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
FINISHED = ("done", "failed", "skipped", "superseded")
ACTIVE = ("queued", "running", "done")
# Targets a bulk command (today / open) leaves alone: handled, or skipped on purpose.
TAKEN = ("queued", "running", "done", "skipped")
SEVERE = ("critical", "high")

esc = html.escape


# -- small stores (Redis in production, memory in tests) ------------------------------------------


# How many of a branch's commits the picker reads when it keeps only some people's.
MAX_SCAN = 400

BRANCH_HINT = (
    "Repo làm việc trên nhánh khác? Chọn <b>nhánh review</b> ở trang Admin, mục Repo, hoặc gõ"
    " <code>/review type=commit branch=develop</code> (<code>branch=all</code>: mọi nhánh)."
)


def review_branch(project: Project, asked: str | None = None) -> str:
    """The branch "recent commits" come from: what the person typed, else the repo's review
    branch set in admin, else GitLab's default branch (docs/specs/17 §Nhánh review)."""
    return asked or project.review_branch or project.default_branch or "HEAD"


def branch_label(branch: str) -> str:
    return "mọi nhánh" if branch == ALL_BRANCHES else f"nhánh {branch}"


def repo_names(repos: list[Project], limit: int = 15) -> str:
    """Repo paths for a "say which one" reply; what does not fit is counted, not dropped."""
    names = ", ".join(f"<code>{esc(p.path_with_namespace)}</code>" for p in repos[:limit])
    more = len(repos) - limit
    return names + (f" và {more} repo khác (gõ /repos để xem hết)" if more > 0 else "")


class LinkCodes(Protocol):
    def issue(self, user_id: int) -> str: ...
    def redeem(self, code: str) -> int | None: ...


class CommandLimiter(Protocol):
    def allow(self, key: str) -> bool: ...


def new_code() -> str:
    return "".join(secrets.choice(LINK_ALPHABET) for _ in range(6))


class RedisLinkCodes:
    def __init__(self, redis: Redis, ttl_s: int = LINK_CODE_TTL_S) -> None:
        self._redis, self._ttl = redis, ttl_s

    def issue(self, user_id: int) -> str:
        for _ in range(5):
            code = new_code()
            if self._redis.set(f"tg:link:{code}", user_id, ex=self._ttl, nx=True):
                return code
        raise RuntimeError("could not allocate a link code")

    def redeem(self, code: str) -> int | None:
        raw = self._redis.getdel(f"tg:link:{code.strip().upper()}")
        return int(raw) if raw else None


class MemoryLinkCodes:
    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl_s: int = LINK_CODE_TTL_S):
        self._codes: dict[str, tuple[int, float]] = {}
        self._clock, self._ttl = clock, ttl_s

    def issue(self, user_id: int) -> str:
        code = new_code()
        self._codes[code] = (user_id, self._clock() + self._ttl)
        return code

    def redeem(self, code: str) -> int | None:
        entry = self._codes.pop(code.strip().upper(), None)
        if entry is None or entry[1] < self._clock():
            return None
        return entry[0]


class RedisCommandLimiter:
    """At most `limit` costly commands per key per window (fixed window, Redis INCR)."""

    def __init__(self, redis: Redis, limit: int, window_s: int = 600) -> None:
        self._redis, self._limit, self._window = redis, limit, window_s

    def allow(self, key: str) -> bool:
        pipe = self._redis.pipeline()
        pipe.incr(f"tg:rl:{key}")
        pipe.expire(f"tg:rl:{key}", self._window, nx=True)
        count, _ = pipe.execute()
        return int(count) <= self._limit


class AllowAll:
    def allow(self, key: str) -> bool:
        return True


WEBHOOK_TAKEOVER_TTL_S = 3600


class WebhookGuard(Protocol):
    """Remembers that the poller deleted a bot's webhook, so a returning one is not fought."""

    def claim(self, bot_id: int) -> bool: ...


class RedisWebhookGuard:
    def __init__(self, redis: Redis, ttl_s: int = WEBHOOK_TAKEOVER_TTL_S) -> None:
        self._redis, self._ttl = redis, ttl_s

    def claim(self, bot_id: int) -> bool:
        return bool(self._redis.set(f"tg:webhook_deleted:{bot_id}", 1, ex=self._ttl, nx=True))


class MemoryWebhookGuard:
    def __init__(
        self, clock: Callable[[], float] = time.monotonic, ttl_s: int = WEBHOOK_TAKEOVER_TTL_S
    ) -> None:
        self._until: dict[int, float] = {}
        self._clock, self._ttl = clock, ttl_s

    def claim(self, bot_id: int) -> bool:
        now = self._clock()
        if self._until.get(bot_id, 0.0) > now:
            return False
        self._until[bot_id] = now + self._ttl
        return True


# -- incoming messages ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Incoming:
    update_id: int
    chat_id: int
    chat_type: str  # private | group | supergroup | channel
    message_id: int | None
    from_id: int
    username: str | None
    first_name: str
    text: str


@dataclass(frozen=True)
class Callback:
    """A tap on one of the bot's inline buttons."""

    update_id: int
    callback_id: str
    chat_id: int
    message_id: int
    from_id: int
    username: str | None
    data: str


@dataclass
class CallbackOutcome:
    """What the poller does after a tap: answer the tap (always), maybe rewrite the message."""

    answer: str | None = None
    alert: bool = False
    text: str | None = None  # new message text (keyboard from `markup`, none = buttons removed)
    markup: dict[str, Any] | None = None
    send: str | None = None  # an extra message after the edit (results that need room)


def callback(update: dict[str, Any]) -> Callback | None:
    query = update.get("callback_query")
    if not isinstance(query, dict):
        return None
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    sender = query.get("from") or {}
    if not query.get("id") or chat.get("id") is None or not message.get("message_id"):
        return None
    return Callback(
        update_id=int(update.get("update_id") or 0),
        callback_id=str(query["id"]),
        chat_id=int(chat["id"]),
        message_id=int(message["message_id"]),
        from_id=int(sender.get("id") or 0),
        username=sender.get("username"),
        data=str(query.get("data") or ""),
    )


def incoming(update: dict[str, Any]) -> Incoming | None:
    """The text message inside an update, or None for anything else (edits, joins, media)."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    if not isinstance(text, str) or not sender.get("id") or chat.get("id") is None:
        return None
    if sender.get("is_bot"):
        return None
    return Incoming(
        update_id=int(update.get("update_id") or 0),
        chat_id=int(chat["id"]),
        chat_type=str(chat.get("type") or "private"),
        message_id=int(message["message_id"]) if message.get("message_id") else None,
        from_id=int(sender["id"]),
        username=sender.get("username"),
        first_name=str(sender.get("first_name") or ""),
        text=text,
    )


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def relative_time(when: datetime | None, now: datetime) -> str:
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} phút trước"
    if seconds < 86400:
        return f"{seconds // 3600} giờ trước"
    return f"{seconds // 86400} ngày trước"


# -- targets --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    kind: str  # mr | commit
    project_id: int
    ref: str  # iid for an MR, full sha for a commit
    sha: str  # reviewed sha (MR head or the commit itself)
    label: str  # "omicrm/api !12" / "omicrm/api a1b2c3d4"
    force: bool = False  # review again although a finished review exists (new round)
    round: int | None = None  # with force: the round being replaced (dedupes repeated taps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "project_id": self.project_id,
            "ref": self.ref,
            "sha": self.sha,
            "label": self.label,
            "force": self.force,
            "round": self.round,
        }


class Denied(Exception):
    """A message for the sender, not a bug."""


# -- the service ----------------------------------------------------------------------------------


class BotService:
    def __init__(
        self,
        session: Session,
        bot: TelegramBot,
        settings: Settings,
        queue: Enqueuer,
        make_client: ClientFactory,
        link_codes: LinkCodes,
        limiter: CommandLimiter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        resolver: Resolver | None = None,
        picker: PickerStore | None = None,
    ) -> None:
        self._s = session
        self._resolver: Resolver = resolver or NoResolver()
        self._picker: PickerStore = picker or MemoryPickerStore()
        # Set while handling a message whose reply carries buttons; the poller reads it.
        self.pending_markup: dict[str, Any] | None = None
        self._bot = bot
        self._settings = settings
        self._queue = queue
        self._make_client = make_client
        self._codes = link_codes
        self._limiter = limiter
        self._clock = clock
        self._tz = ZoneInfo(settings.report_tz)

    # -- entry ---------------------------------------------------------------------------------

    def handle(self, msg: Incoming) -> str | None:
        """The HTML reply for one message, or None when the bot stays silent."""
        allowed = self._bot.allowed_chat_ids or []
        if allowed and msg.chat_id not in allowed:
            log.info(
                "telegram message from a chat outside the allowlist", extra={"bot_id": self._bot.id}
            )
            return None
        command = parse(msg.text, self._bot.bot_username)
        if command is None:
            return self._help(linked=False) if msg.chat_type == "private" else None
        account = self._account(msg)
        linked = account is not None and account.user_id is not None
        if command.name in ("start", "help"):
            return self._help(linked=linked)
        if command.name == "link":
            return self._link(msg, account, command)
        if account is None or account.user_id is None:
            return self._not_linked()
        user = self._s.get(User, account.user_id)
        if user is None or user.active is False:
            return "Tài khoản của bạn đang tắt trong CodePulse; liên hệ admin."
        principal = load_principal(self._s, user.id, self._settings)
        if not principal.can_view_project(self._bot.business_project_id):
            return NOT_GRANTED
        try:
            return self._dispatch(command, msg, account, principal)
        except Denied as exc:
            return str(exc)
        except GitLabError as exc:
            # The GitLab host and path stay in the log, not in a group chat.
            log.warning(
                "telegram command: gitlab error",
                extra={"bot_id": self._bot.id, "status": exc.status_code, "error": str(exc)},
            )
            status = f" (HTTP {exc.status_code})" if exc.status_code else " (timeout / lỗi mạng)"
            return f"GitLab không trả lời được{status}; thử lại sau."

    def _dispatch(self, command: Command, msg: Incoming, account: Account, me: Principal) -> str:
        match command.name:
            case "whoami":
                return self._whoami(account, me)
            case "unlink":
                self._s.delete(account)
                audit(self._s, me.user_id, "telegram.unlink", f"account:{account.id}")
                return "Đã gỡ liên kết Telegram. Dùng /link &lt;mã&gt; để liên kết lại."
            case "repos":
                return self._repos()
            case "status":
                return self._status(me)
            case "critical":
                return self._critical(me, command.args[0] if command.args else None)
            case "score":
                return self._score(me, command.args)
            case "sync":
                return self._sync(me, command.args[0] if command.args else None)
            case "review":
                return self._review(command, msg, me)
        return "Không có lệnh này. Dùng /help để xem danh sách."

    # -- identity ------------------------------------------------------------------------------

    @staticmethod
    def _display(msg: Incoming) -> str:
        return (f"@{msg.username}" if msg.username else msg.first_name or str(msg.from_id))[:200]

    def _account(self, msg: Incoming) -> Account | None:
        """The sender's Telegram account row, if one exists. Strangers (anyone can DM a bot)
        are never written to the database; /link creates the row."""
        account = self._s.scalars(
            select(Account).where(
                Account.kind == TELEGRAM_KIND, Account.external_key == str(msg.from_id)
            )
        ).first()
        if account is not None and account.display_name != self._display(msg):
            account.display_name = self._display(msg)
        return account

    def _link(self, msg: Incoming, account: Account | None, command: Command) -> str:
        if not command.args:
            return self._not_linked()
        user_id = self._codes.redeem(command.args[0])
        if user_id is None:
            return "Mã không đúng hoặc đã hết hạn (mã sống 15 phút). Tạo mã mới ở trang Của tôi."
        user = self._s.get(User, user_id)
        if user is None or user.active is False:
            return "Người dùng của mã này không còn hoạt động."
        if account is None:
            account = Account(
                kind=TELEGRAM_KIND, external_key=str(msg.from_id), display_name=self._display(msg)
            )
            self._s.add(account)
            self._s.flush()
        account.user_id = user.id
        audit(
            self._s,
            user.id,
            "telegram.link",
            f"account:{account.id}",
            telegram=account.external_key,
        )
        who = esc(user.full_name or user.primary_email or "")
        return f"Đã liên kết với <b>{who}</b>. Gõ /help để bắt đầu."

    def _not_linked(self) -> str:
        return (
            "Telegram của bạn chưa gắn với người dùng CodePulse.\n"
            f"Mở {esc(self._settings.dashboard_url)} → <b>Của tôi</b> → <b>Telegram</b> → "
            "Tạo mã liên kết, rồi gửi cho tôi: <code>/link MÃ</code>."
        )

    def _help(self, linked: bool) -> str:
        scope = self._scope_label()
        lines = [
            f"<b>CodePulse review bot</b> · {esc(scope)}",
            "",
            "<b>Review</b>",
            "<b>/review</b> - menu bấm nút: chọn commit / MR trong danh sách, không cần mở GitLab",
            "<b>/review type=mr|commit [id=…] [repo=…] [branch=…] [user=…] [range=…] [all=1]</b>",
            "  type=commit [repo=…] - danh sách commit 7 ngày qua của mọi người, tick rồi review",
            "  type=mr [repo=…] - danh sách MR đang mở để chọn",
            "  type=mr id=412 repo=global-api - review MR !412 của repo …global-api",
            "  type=commit user=tamtd - commit gần nhất của tamtd",
            "  type=commit user=tamtd range=7d - commit 7 ngày qua của tamtd",
            "  type=mr user=an - MR gần nhất của An (bỏ user = của bạn)",
            "  repo/user viết tắt được; bot tự chọn, hỏi lại khi còn mơ hồ",
            "Dạng cũ vẫn dùng được: /review mr &lt;số|link&gt; [repo],",
            "  /review commit &lt;sha&gt; [repo], /review last|lastmr [repo] [người],",
            "  /review today [repo] [người...], /review open [repo] (lead)",
            "",
            "<b>Xem</b>",
            "/status - review gần đây của bạn · /critical [repo] - lỗi nghiêm trọng đang mở",
            "/score [người] [week|month] - điểm kỳ gần nhất · /repos - repo trong phạm vi",
            "/sync [repo] - lấy dữ liệu GitLab ngay (lead) · /whoami · /unlink",
            "",
            "Repo: đường dẫn GitLab hoặc phần cuối (api, omicrm/api).",
            "Người: @username GitLab, email, một phần tên, hoặc me.",
            "Bạn review được việc của mình; lead review được người trong team mình.",
        ]
        if not linked:
            lines += ["", self._not_linked()]
        return "\n".join(lines)

    def _whoami(self, account: Account, me: Principal) -> str:
        user = self._s.get(User, me.user_id)
        role = (
            "admin"
            if me.is_admin
            else "lead"
            if me.is_lead
            else "manager"
            if me.is_manager
            else "thành viên"
        )
        name = user.full_name if user and user.full_name else (user.primary_email if user else "")
        return (
            f"<b>{esc(name or '')}</b> ({esc(user.primary_email or '') if user else ''}) · {role}\n"
            f"Telegram {esc(account.display_name or account.external_key)}"
            f" · bot: {esc(self._scope_label())}"
        )

    # -- scope ---------------------------------------------------------------------------------

    def _scope(self) -> list[Project]:
        query = select(Project).where(
            Project.business_project_id == self._bot.business_project_id, Project.tracked
        )
        if self._bot.project_id is not None:
            query = query.where(Project.id == self._bot.project_id)
        return list(self._s.scalars(query.order_by(Project.path_with_namespace)))

    def _scope_label(self) -> str:
        bp = self._s.get(BusinessProject, self._bot.business_project_id)
        label = bp.name if bp else f"dự án #{self._bot.business_project_id}"
        if self._bot.project_id is not None:
            project = self._s.get(Project, self._bot.project_id)
            if project:
                label += f" / {project.path_with_namespace}"
        return label

    def _repos(self) -> str:
        repos = self._scope()
        if not repos:
            return "Dự án này chưa có repo nào đang theo dõi."
        rows = [
            f"• <code>{esc(p.path_with_namespace)}</code>"
            + ("" if p.review_enabled else " (tắt review)")
            + ("" if p.allow_external_llm is not False else " (không gửi LLM)")
            for p in repos
        ]
        return f"<b>Repo của {esc(self._scope_label())}</b>\n" + "\n".join(rows)

    def _pick_repos(self, hint: str | None, *, one: bool) -> list[Project]:
        scope = self._scope()
        repos = match_repo(scope, hint, lambda p: p.path_with_namespace)
        if not repos:
            raise Denied(
                f"Không tìm thấy repo '{esc(hint)}' trong {esc(self._scope_label())}. Gõ /repos."
                if hint
                else "Dự án này chưa có repo nào đang theo dõi."
            )
        if one and len(repos) > 1 and hint:
            # Several repos fit the abbreviation: let the resolver choose among them.
            picked = self._resolver.pick("repo", hint, [p.path_with_namespace for p in repos])
            if picked is not None:
                return [repos[picked]]
            raise Denied(f"Có nhiều repo khớp '{esc(hint)}', hãy nói rõ: {repo_names(repos)}")
        if one and len(repos) > 1:
            raise Denied(f"Có nhiều repo khớp, hãy nói rõ: {repo_names(repos)}")
        return repos

    def _people(self, hints: tuple[str, ...], me: Principal) -> list[User]:
        if not hints:
            hints = ("me",)
        users: dict[int, User] = {}
        for hint in hints:
            user = self._find_user(hint, me)
            if user is None:
                raise Denied(
                    f"Không tìm thấy người '{esc(hint)}'. Dùng @username GitLab, email hoặc tên."
                )
            if not (me.can_see_user(user.id) or self._manages(me)):
                raise Denied(
                    f"Bạn không review được việc của {esc(user.full_name or hint)}:"
                    " chỉ lead của người đó, người quản lý dự án hoặc admin."
                )
            users[user.id] = user
        return list(users.values())

    def _find_user(self, hint: str, me: Principal) -> User | None:
        """A person from "me", @gitlab-username, email, an email/username prefix ("tamtd"), or
        part of the name; several fits -> the resolver picks, else the sender is asked."""
        word = hint.strip()
        if word.casefold() == "me":
            return self._s.get(User, me.user_id)
        bare = word[1:] if word.startswith("@") else word
        if word.startswith("@") or "@" not in word:
            account = self._s.scalars(
                select(Account).where(
                    Account.kind == "gitlab_user",
                    func.lower(Account.display_name) == bare.casefold(),
                    Account.user_id.is_not(None),
                )
            ).first()
            if account and account.user_id is not None:
                return self._s.get(User, account.user_id)
        if "@" in bare:
            return self._s.scalars(
                select(User).where(func.lower(User.primary_email) == bare.casefold())
            ).first()
        if len(bare) < 2:
            return None
        needle = bare.casefold()
        # Email local part ("tamtd" -> tamtd@company.vn), then name parts, then any account.
        by_email = list(
            self._s.scalars(
                select(User)
                .where(
                    User.active.is_not(False),
                    func.lower(User.primary_email).startswith(f"{needle}@", autoescape=True),
                )
                .limit(3)
            )
        )
        if len(by_email) == 1:
            return by_email[0]
        matches = by_email or list(
            self._s.scalars(
                select(User)
                .where(
                    User.active.is_not(False),
                    func.lower(User.full_name).contains(needle, autoescape=True),
                )
                .order_by(User.id)
                .limit(8)
            )
        )
        if not matches:
            accounts = list(
                self._s.scalars(
                    select(Account).where(
                        Account.user_id.is_not(None),
                        func.lower(Account.external_key).contains(needle, autoescape=True)
                        | func.lower(Account.display_name).contains(needle, autoescape=True),
                    )
                )
            )
            seen: dict[int, User] = {}
            for account in accounts:
                if account.user_id is not None and account.user_id not in seen:
                    user = self._s.get(User, account.user_id)
                    if user is not None:
                        seen[user.id] = user
            matches = list(seen.values())[:8]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            visible = me.visible_users()
            shown = [u for u in matches if visible is None or u.id in visible]
            picked = self._resolver.pick(
                "user", hint, [f"{u.full_name or ''} · {u.primary_email or ''}" for u in shown]
            )
            if picked is not None:
                return shown[picked]
            # Only names the sender may see are spelled out.
            names = ", ".join(esc(u.full_name or "") for u in shown)
            raise Denied(
                f"'{esc(hint)}' khớp nhiều người{f' ({names})' if names else ''};"
                " dùng @username GitLab hoặc email."
            )
        return None

    def _user_of_account(self, account_id: int | None) -> int | None:
        if account_id is None:
            return None
        account = self._s.get(Account, account_id)
        return account.user_id if account else None

    def _author_from_reviews(
        self, *, mr_id: int | None = None, sha: str | None = None
    ) -> int | None:
        """The author CodePulse recorded on an earlier review of the same target, if any."""
        query = select(Review.author_user_id).where(Review.author_user_id.is_not(None))
        if mr_id is not None:
            query = query.where(Review.mr_id == mr_id)
        elif sha is not None:
            query = query.where(Review.commit_sha == sha)
        else:
            return None
        return self._s.scalar(query.order_by(Review.id.desc()).limit(1))

    def _manages(self, me: Principal) -> bool:
        """A manager of the bot's dự án acts on everyone's work there (docs/specs/09)."""
        return me.can_manage_project(self._bot.business_project_id)

    def _may_review_author(self, author_user_id: int | None, me: Principal, what: str) -> None:
        if me.may_review_work(author_user_id, self._bot.business_project_id):
            return
        raise Denied(
            f"Bạn chỉ review được {what} của mình hoặc của người mình lead"
            " (tác giả chưa gắn người thì cần lead, người quản lý dự án hoặc admin)."
        )

    # -- review commands -----------------------------------------------------------------------

    def _review(self, command: Command, msg: Incoming, me: Principal) -> str:
        if not command.args:
            # Bare /review on a phone: buttons, not syntax.
            self.pending_markup = menu_keyboard()
            return "Bạn muốn review gì?"
        parsed = parse_review(command.args)
        if isinstance(parsed, str):
            return esc(parsed)
        if not self._limiter.allow(f"user:{me.user_id}"):
            return "Bạn gửi quá nhiều lệnh review trong 10 phút; thử lại sau ít phút."
        if parsed.pick:
            return self._start_picker(parsed, msg, me, command.raw)
        targets: list[Target]
        notes: list[str] = []
        match parsed.action:
            case "mr":
                targets, notes = self._target_mr(parsed, me)
            case "commit":
                targets, notes = self._target_commit(parsed, me)
            case "last":
                targets, notes = self._target_last(parsed, me)
            case "lastmr":
                targets, notes = self._target_lastmr(parsed, me)
            case "today":
                targets, notes = self._target_today(parsed, me)
            case _:
                targets, notes = self._target_open(parsed, me)
        if not targets:
            return "\n".join(notes) if notes else "Không có gì để review."
        queued = self._enqueue(targets, me, msg, command.raw)
        lines = [
            *notes,
            f"Đã đưa <b>{len(queued)}</b> review vào hàng đợi; tôi sẽ báo kết quả ở đây khi xong.",
        ]
        lines += [f"• {esc(t.label)}" for t in queued[:10]]
        if len(queued) > 10:
            lines.append(f"… và {len(queued) - 10} mục nữa")
        return "\n".join(lines)

    def _target_mr(self, args: ReviewArgs, me: Principal) -> tuple[list[Target], list[str]]:
        project = self._pick_repos(args.repo_hint, one=True)[0]
        iid = int(args.ref or 0)
        mr = self._s.scalars(
            select(MergeRequest).where(
                MergeRequest.project_id == project.id, MergeRequest.iid == iid
            )
        ).first()
        head: str | None = mr.head_sha if mr else None
        author = self._user_of_account(mr.author_account_id) if mr else None
        if author is None and mr is not None:
            author = self._author_from_reviews(mr_id=mr.id)
        title = mr.title if mr else None
        # GitLab is the truth for the current head; the DB may lag until the next sync.
        info = self._gitlab_mr(project, iid)
        if info is not None:
            head = str(info.get("sha") or head or "")
            title = str(info.get("title") or title or "")
            if author is None:
                author = self._gitlab_author(info)
        if not head:
            raise Denied(
                f"Không thấy MR !{iid} trong <code>{esc(project.path_with_namespace)}</code>."
            )
        self._may_review_author(author, me, "merge request")
        label = f"{project.path_with_namespace} !{iid}" + (f" - {title[:60]}" if title else "")
        target = Target("mr", project.id, str(iid), head, label)
        existing = self._existing(target, mr.id if mr else None)
        if existing is not None:
            return [], [self._already(existing, target)]
        return [target], []

    def _target_commit(self, args: ReviewArgs, me: Principal) -> tuple[list[Target], list[str]]:
        project = self._pick_repos(args.repo_hint, one=True)[0]
        prefix = (args.ref or "").lower()
        commit = self._s.scalars(
            select(Commit)
            .where(Commit.project_id == project.id, Commit.sha.startswith(prefix))
            .order_by(Commit.authored_at.desc())
            .limit(2)
        ).all()
        if len(commit) > 1:
            raise Denied("Mã commit ngắn khớp nhiều commit; gõ dài hơn.")
        sha = commit[0].sha if commit else None
        author = self._user_of_account(commit[0].author_account_id) if commit else None
        if author is None and sha is not None:
            author = self._author_from_reviews(sha=sha)
        if sha is None:
            info = self._gitlab_commit(project, prefix)
            if info is None:
                raise Denied(
                    f"Không thấy commit <code>{esc(prefix)}</code> trong"
                    f" <code>{esc(project.path_with_namespace)}</code>."
                )
            sha = str(info.get("id") or "")
            email = str(info.get("author_email") or "").lower()
            if email:
                acc = self._s.scalars(
                    select(Account).where(
                        Account.kind == "git_email", Account.external_key == email
                    )
                ).first()
                author = acc.user_id if acc else None
        self._may_review_author(author, me, "commit")
        target = Target("commit", project.id, sha, sha, f"{project.path_with_namespace} {sha[:8]}")
        existing = self._existing(target, None)
        if existing is not None:
            return [], [self._already(existing, target)]
        return [target], []

    def _accounts_of(self, users: list[User]) -> list[int]:
        ids = [u.id for u in users]
        return list(self._s.scalars(select(Account.id).where(Account.user_id.in_(ids))))

    def _target_last(self, args: ReviewArgs, me: Principal) -> tuple[list[Target], list[str]]:
        repos = self._pick_repos(args.repo_hint, one=False)
        people = self._people(args.people, me)
        accounts = self._accounts_of(people)
        commit = self._s.scalars(
            select(Commit)
            .where(
                Commit.project_id.in_([p.id for p in repos]),
                Commit.author_account_id.in_(accounts),
                Commit.is_merge.is_not(True),
            )
            .order_by(Commit.authored_at.desc())
            .limit(1)
        ).first()
        if commit is None or commit.project_id is None:
            return [], [self._nothing_for(people, "commit")]
        project = self._s.get(Project, commit.project_id)
        if project is None:
            return [], ["Repo của commit không còn."]
        target = Target(
            "commit",
            project.id,
            commit.sha,
            commit.sha,
            f"{project.path_with_namespace} {commit.sha[:8]}",
        )
        existing = self._existing(target, None)
        if existing is not None:
            return [], [self._already(existing, target)]
        return [target], []

    def _target_lastmr(self, args: ReviewArgs, me: Principal) -> tuple[list[Target], list[str]]:
        repos = self._pick_repos(args.repo_hint, one=False)
        people = self._people(args.people, me)
        accounts = self._accounts_of(people)
        mr = self._s.scalars(
            select(MergeRequest)
            .where(
                MergeRequest.project_id.in_([p.id for p in repos]),
                MergeRequest.author_account_id.in_(accounts),
                MergeRequest.head_sha.is_not(None),
            )
            .order_by(MergeRequest.created_at_src.desc().nulls_last(), MergeRequest.id.desc())
            .limit(1)
        ).first()
        if mr is None or mr.project_id is None or mr.head_sha is None:
            return [], [self._nothing_for(people, "merge request")]
        project = self._s.get(Project, mr.project_id)
        if project is None:
            return [], ["Repo của MR không còn."]
        label = f"{project.path_with_namespace} !{mr.iid}" + (
            f" - {mr.title[:60]}" if mr.title else ""
        )
        target = Target("mr", project.id, str(mr.iid), mr.head_sha, label)
        existing = self._existing(target, mr.id)
        if existing is not None:
            return [], [self._already(existing, target)]
        return [target], []

    def _target_today(self, args: ReviewArgs, me: Principal) -> tuple[list[Target], list[str]]:
        repos = self._pick_repos(args.repo_hint, one=False)
        people = self._people(args.people, me)
        accounts = self._accounts_of(people)
        today = self._clock().astimezone(self._tz).date()
        end = datetime.combine(today, datetime.min.time(), self._tz) + timedelta(days=1)
        start = end - timedelta(days=max(args.days, 1))
        active = select(Review.commit_sha).where(
            Review.commit_sha.is_not(None), Review.status.in_(TAKEN)
        )
        commits = self._s.scalars(
            select(Commit)
            .where(
                Commit.project_id.in_([p.id for p in repos]),
                Commit.author_account_id.in_(accounts),
                Commit.authored_at >= start,
                Commit.authored_at < end,
                Commit.is_merge.is_not(True),
                Commit.sha.not_in(active),
            )
            .order_by(Commit.authored_at)
        ).all()
        by_id = {p.id: p for p in repos}
        targets = [
            Target(
                "commit",
                c.project_id,
                c.sha,
                c.sha,
                f"{by_id[c.project_id].path_with_namespace} {c.sha[:8]}",
            )
            for c in commits
            if c.project_id in by_id
        ]
        notes: list[str] = []
        if not targets:
            notes.append(
                self._nothing_for(
                    people,
                    f"commit mới hôm nay ({today:%d/%m}, chưa review)"
                    if args.days <= 1
                    else f"commit mới trong {args.days} ngày qua (chưa review)",
                )
                + "\nDữ liệu theo lần sync gần nhất; /sync để lấy mới."
            )
        return self._cap(targets, notes)

    def _target_open(self, args: ReviewArgs, me: Principal) -> tuple[list[Target], list[str]]:
        if not (me.is_admin or me.is_lead or self._manages(me)):
            raise Denied("/review open dành cho lead, người quản lý dự án hoặc admin.")
        repos = self._pick_repos(args.repo_hint, one=False)
        reviewed = select(Review.mr_id).where(Review.mr_id.is_not(None), Review.status.in_(TAKEN))
        query = select(MergeRequest).where(
            MergeRequest.project_id.in_([p.id for p in repos]),
            MergeRequest.state == "opened",
            MergeRequest.head_sha.is_not(None),
            MergeRequest.id.not_in(reviewed),
        )
        visible = None if self._manages(me) else me.visible_users()
        if visible is not None:
            # A lead sees their own team: MRs by people they lead, plus unmapped authors.
            mine = select(Account.id).where(Account.user_id.in_(visible))
            unmapped = select(Account.id).where(Account.user_id.is_(None))
            query = query.where(
                MergeRequest.author_account_id.is_(None)
                | MergeRequest.author_account_id.in_(mine)
                | MergeRequest.author_account_id.in_(unmapped)
            )
        mrs = self._s.scalars(query.order_by(MergeRequest.created_at_src.desc().nulls_last())).all()
        by_id = {p.id: p for p in repos}
        targets = [
            Target(
                "mr",
                mr.project_id,
                str(mr.iid),
                mr.head_sha,
                f"{by_id[mr.project_id].path_with_namespace} !{mr.iid}"
                + (f" - {mr.title[:60]}" if mr.title else ""),
            )
            for mr in mrs
            if mr.project_id in by_id and mr.head_sha
        ]
        notes: list[str] = []
        if not targets:
            notes.append("Không có MR đang mở nào chưa được review (theo lần sync gần nhất).")
        return self._cap(targets, notes)

    def _cap(self, targets: list[Target], notes: list[str]) -> tuple[list[Target], list[str]]:
        limit = self._settings.telegram_max_targets
        if len(targets) > limit:
            notes.append(f"Có {len(targets)} mục; chỉ lấy {limit} mục đầu để giữ ngân sách LLM.")
            targets = targets[:limit]
        return targets, notes

    def _nothing_for(self, people: list[User], what: str) -> str:
        names = ", ".join(esc(u.full_name or u.primary_email or "") for u in people)
        return f"Không thấy {what} nào của {names} trong phạm vi bot."

    # -- GitLab lookups (optional: the DB answer is used when GitLab is unreachable) ----------

    def _connection(self, project: Project) -> Connection | None:
        return self._s.get(Connection, project.connection_id) if project.connection_id else None

    def _gitlab_mr(self, project: Project, iid: int) -> dict[str, Any] | None:
        connection = self._connection(project)
        if connection is None:
            return None
        try:
            with self._make_client(connection) as client:
                info = client.merge_request(project.external_id, iid)
        except GitLabError as exc:
            if exc.status_code == 404:
                return None
            raise
        return info if isinstance(info, dict) else None

    def _gitlab_commit(self, project: Project, sha: str) -> dict[str, Any] | None:
        connection = self._connection(project)
        if connection is None:
            return None
        try:
            with self._make_client(connection) as client:
                info = client.get(f"/projects/{project.external_id}/repository/commits/{sha}")
        except GitLabError as exc:
            if exc.status_code == 404:
                return None
            raise
        return info if isinstance(info, dict) else None

    def _gitlab_author(self, info: dict[str, Any]) -> int | None:
        author = info.get("author") or {}
        if not author.get("id"):
            return None
        account = self._s.scalars(
            select(Account).where(
                Account.kind == "gitlab_user", Account.external_key == str(author["id"])
            )
        ).first()
        return account.user_id if account else None

    # -- existing reviews and queueing ---------------------------------------------------------

    def _existing(self, target: Target, mr_id: int | None) -> Review | None:
        query = select(Review).where(
            Review.target_type == target.kind,
            Review.reviewed_sha == target.sha,
            Review.rubric_version == RUBRIC_VERSION,
        )
        if target.kind == "mr":
            if mr_id is None:
                return None
            query = query.where(Review.mr_id == mr_id)
        else:
            query = query.where(Review.commit_sha == target.sha)
        review = self._s.scalars(query.order_by(Review.id.desc())).first()
        if review is None:
            return None
        if review.status in ("failed", "skipped"):
            # Like POST /reviews/{id}/rerun: a failed or skipped review may run again.
            review.status, review.error = "queued", None
            return None
        return review

    def _already(self, review: Review, target: Target) -> str:
        if review.status in ("queued", "running"):
            return f"{esc(target.label)}: review đang chạy, tôi sẽ báo khi xong."
        return f"{esc(target.label)}: đã được review.\n{self._result_line(review)}"

    def _enqueue(
        self, targets: list[Target], me: Principal, msg: Incoming, raw: str
    ) -> list[Target]:
        queued: list[Target] = []
        for t in targets:
            if t.kind == "mr":
                self._queue.enqueue(
                    "app.workers.review_jobs.review_mr_job",
                    t.project_id,
                    int(t.ref),
                    t.sha,
                    trigger="telegram",
                    requested_by=me.user_id,
                    force=t.force,
                    expected_round=t.round if t.force else None,
                    job_id=f"review-mr-{t.project_id}-{t.ref}-{t.sha[:12]}"
                    + (f"-again-{t.round}" if t.force else ""),
                    job_timeout=review_job_timeout(self._settings),
                    retry=REVIEW_RETRY,
                    at_front=True,
                )
            else:
                self._queue.enqueue(
                    "app.workers.review_jobs.review_commit_job",
                    t.project_id,
                    t.sha,
                    trigger="telegram",
                    requested_by=me.user_id,
                    force=t.force,
                    expected_round=t.round if t.force else None,
                    job_id=f"review-commit-{t.sha[:12]}" + (f"-again-{t.round}" if t.force else ""),
                    job_timeout=review_job_timeout(self._settings),
                    retry=REVIEW_RETRY,
                    at_front=True,
                )
            queued.append(t)
        request = TelegramRequest(
            bot_id=self._bot.id,
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            user_id=me.user_id,
            command=raw[:500],
            targets=[t.as_dict() for t in queued],
        )
        self._s.add(request)
        audit(
            self._s,
            me.user_id,
            "telegram.review",
            f"telegram_bot:{self._bot.id}",
            command=raw[:200],
            targets=[t.label for t in queued],
        )
        self._s.flush()
        return queued

    # -- picker: choose commits / MRs with buttons (docs/specs/15 §Chọn bằng nút) ---------------

    def _start_picker(self, args: ReviewArgs, msg: Incoming, me: Principal, raw: str) -> str:
        kind = "commit" if args.action in ("today", "last", "commit") else "mr"
        project = self._pick_repos(args.repo_hint, one=True)[0]
        people = self._people(args.people, me) if args.people else []
        state = self._build_picker(kind, project, args, people, me, msg, raw)
        if state is None:
            if kind == "commit":
                branch = review_branch(project, args.branch)
                if branch != ALL_BRANCHES and len(people) <= 1:
                    who = people[0].id if people else 0
                    self.pending_markup = {
                        "inline_keyboard": [all_branches_row(project.id, who, args.days)]
                    }
                return (
                    f"{esc(project.path_with_namespace)}: không có commit nào"
                    f" trong {args.days} ngày qua trên {esc(branch_label(branch))}.\n" + BRANCH_HINT
                )
            return "Không có mục nào trong phạm vi này."
        self._picker.save(state)
        self.pending_markup = keyboard(state)
        return render_text(state)

    def _build_picker(
        self,
        kind: str,
        project: Project,
        args: ReviewArgs,
        people: list[User],
        me: Principal,
        msg: Incoming,
        raw: str,
    ) -> PickerState | None:
        only = {u.id for u in people} or None
        if kind == "commit":
            branch = review_branch(project, args.branch)
            items = self._commit_items(project, branch, args.days, only, me)
            scope = f"{project.path_with_namespace} · {branch_label(branch)} · {args.days} ngày"
        else:
            items = self._mr_items(project, only, me)
            scope = project.path_with_namespace
        if not items:
            return None
        if people:
            scope += " · " + ", ".join((u.full_name or u.primary_email or "?") for u in people[:3])
        return PickerState(
            token=new_token(),
            owner_from_id=msg.from_id,
            user_id=me.user_id,
            chat_id=msg.chat_id,
            message_id=None,
            kind="commit" if kind == "commit" else "mr",
            scope_label=scope,
            items=items[:MAX_ITEMS],
            raw=raw,
            project_id=project.id,
            person_id=people[0].id if len(people) == 1 else None,
            branch=args.branch or "",
            days=args.days,
            may_force=me.can_manage_project(self._bot.business_project_id),
        )

    def _state_of(self, kind: str, project_id: int, ref: str, sha: str) -> tuple[str, str]:
        target = Target(kind, project_id, ref, sha, "")
        mr_id = None
        if kind == "mr":
            mr_id = self._s.scalar(
                select(MergeRequest.id).where(
                    MergeRequest.project_id == project_id, MergeRequest.iid == int(ref)
                )
            )
        review = self._existing(target, mr_id)
        if review is None:
            return "open", ""
        if review.status in ("queued", "running"):
            return "running", ""
        if review.status == "failed":
            return "failed", ""
        score = f"{review.score:.0f}" if review.score is not None else ""
        verdict = VERDICT_LABEL.get(review.verdict or "", "")
        return "done", " ".join(x for x in (verdict, score) if x)

    def _user_by_email(self, email: str) -> tuple[int | None, str]:
        account = self._s.scalars(
            select(Account).where(
                Account.kind == "git_email", Account.external_key == email.casefold()
            )
        ).first()
        if account is None or account.user_id is None:
            # Not linked yet (e.g. only seen on a branch the sync does not walk): the person
            # whose primary email it is.
            user = (
                self._s.scalars(
                    select(User).where(func.lower(User.primary_email) == email.casefold())
                ).first()
                if email
                else None
            )
            return (user.id, user.full_name or "") if user else (None, "")
        user = self._s.get(User, account.user_id)
        return (user.id, user.full_name or "") if user else (None, "")

    def _commit_items(
        self, project: Project, branch: str, days: int, only: set[int] | None, me: Principal
    ) -> list[Item]:
        since = self._clock() - timedelta(days=max(days, 1))
        raw: list[dict[str, Any]] = []
        fetched = False
        connection = self._connection(project)
        if connection is not None:
            try:
                with self._make_client(connection) as client:
                    # One person's commits: scan deeper, else a busy branch's newest MAX_ITEMS
                    # (everyone's) push theirs out before the filter below.
                    limit = MAX_ITEMS if only is None else MAX_SCAN
                    raw = client.commits_on_ref(project.external_id, branch, since, None, limit)
                fetched = True
            except GitLabError as exc:
                log.warning("picker: gitlab commits failed", extra={"status": exc.status_code})
        items: list[Item] = []
        if fetched:  # GitLab answered: its list is the truth for this branch, even when empty
            for c in raw:
                if len(c.get("parent_ids") or []) > 1:
                    continue
                sha = str(c.get("id") or "")
                uid, name = self._user_by_email(str(c.get("author_email") or ""))
                items.append(
                    self._item(
                        "commit",
                        project,
                        sha,
                        sha,
                        str(c.get("title") or (c.get("message") or "").splitlines()[:1] or [""])[
                            :80
                        ]
                        if isinstance(c.get("title"), str)
                        else "",
                        uid,
                        name or str(c.get("author_name") or ""),
                        parse_ts(c.get("authored_date") or c.get("created_at")),
                        me,
                    )
                )
        else:  # GitLab unreachable: what the last sync saw (every branch; no branch info)
            for commit in self._s.scalars(
                select(Commit)
                .where(
                    Commit.project_id == project.id,
                    Commit.authored_at >= since,
                    Commit.is_merge.is_not(True),
                )
                .order_by(Commit.authored_at.desc())
                .limit(MAX_ITEMS if only is None else MAX_SCAN)
            ):
                uid = self._user_of_account(commit.author_account_id)
                user = self._s.get(User, uid) if uid else None
                title = (commit.message or "").strip().splitlines()[:1] or [commit.sha[:8]]
                items.append(
                    self._item(
                        "commit",
                        project,
                        commit.sha,
                        commit.sha,
                        title[0][:80],
                        uid,
                        (user.full_name if user else None) or "",
                        commit.authored_at,
                        me,
                    )
                )
        if only is not None:
            items = [i for i in items if i.author_user_id in only]
        return items

    def _mr_items(self, project: Project, only: set[int] | None, me: Principal) -> list[Item]:
        raw: list[dict[str, Any]] = []
        connection = self._connection(project)
        if connection is not None:
            try:
                with self._make_client(connection) as client:
                    raw = client.open_merge_requests(project.external_id, MAX_ITEMS)
            except GitLabError as exc:
                log.warning("picker: gitlab mrs failed", extra={"status": exc.status_code})
        items: list[Item] = []
        if raw:
            for m in raw:
                head = str(m.get("sha") or "")
                author = m.get("author") or {}
                account = self._s.scalars(
                    select(Account).where(
                        Account.kind == "gitlab_user", Account.external_key == str(author.get("id"))
                    )
                ).first()
                uid = account.user_id if account else None
                user = self._s.get(User, uid) if uid else None
                items.append(
                    self._item(
                        "mr",
                        project,
                        str(m.get("iid")),
                        head,
                        f"!{m.get('iid')} {str(m.get('title') or '')[:70]}",
                        uid,
                        (user.full_name if user else None) or str(author.get("name") or ""),
                        parse_ts(m.get("updated_at")),
                        me,
                    )
                )
        else:
            for mr in self._s.scalars(
                select(MergeRequest)
                .where(
                    MergeRequest.project_id == project.id,
                    MergeRequest.state == "opened",
                    MergeRequest.head_sha.is_not(None),
                )
                .order_by(MergeRequest.id.desc())
                .limit(MAX_ITEMS)
            ):
                uid = self._user_of_account(mr.author_account_id)
                user = self._s.get(User, uid) if uid else None
                items.append(
                    self._item(
                        "mr",
                        project,
                        str(mr.iid),
                        mr.head_sha or "",
                        f"!{mr.iid} {(mr.title or '')[:70]}",
                        uid,
                        (user.full_name if user else None) or "",
                        mr.created_at_src,
                        me,
                    )
                )
        if only is not None:
            items = [i for i in items if i.author_user_id in only]
        return items

    def _item(
        self,
        kind: str,
        project: Project,
        ref: str,
        sha: str,
        title: str,
        author_user_id: int | None,
        author: str,
        when: datetime | None,
        me: Principal,
    ) -> Item:
        state, result = self._state_of(kind, project.id, ref, sha) if sha else ("open", "")
        if state in ("open", "failed") and not me.may_review_work(
            author_user_id, self._bot.business_project_id
        ):
            state = "blocked"
        short = ref[:8] if kind == "commit" else f"!{ref}"
        return Item(
            kind="commit" if kind == "commit" else "mr",
            project_id=project.id,
            ref=ref,
            sha=sha,
            label=f"{project.path_with_namespace} {short}",
            title=title or short,
            author=author,
            author_user_id=author_user_id,
            when=relative_time(when, self._clock()),
            state=state,
            result=result,
        )

    def handle_callback(self, cb: Callback) -> CallbackOutcome:
        """A button tap: toggle / page / go / cancel on a picker, or the menu and repo choice."""
        allowed = self._bot.allowed_chat_ids or []
        if allowed and cb.chat_id not in allowed:
            return CallbackOutcome(answer="Bot không phục vụ chat này.", alert=True)
        action = decode(cb.data)
        if action is None:
            return CallbackOutcome(answer="Nút này không còn hiệu lực.")
        account = self._s.scalars(
            select(Account).where(
                Account.kind == TELEGRAM_KIND, Account.external_key == str(cb.from_id)
            )
        ).first()
        if account is None or account.user_id is None:
            return CallbackOutcome(answer="Bạn chưa liên kết tài khoản: gõ /link MÃ.", alert=True)
        user = self._s.get(User, account.user_id)
        if user is None or user.active is False:
            return CallbackOutcome(answer="Tài khoản của bạn đang tắt.", alert=True)
        me = load_principal(self._s, user.id, self._settings)
        if not me.can_view_project(self._bot.business_project_id):
            return CallbackOutcome(answer=NOT_GRANTED, alert=True)
        try:
            if action.kind == "menu":
                page = int(action.arg) if action.arg.isdigit() else 0
                return self._menu_callback(action.op, cb, me, page)
            if action.kind == "repo":
                return self._repo_callback(action.op, int(action.arg or 0), cb, me)
            if action.kind == "person":
                project_part, user_part, branch_part, days_part = (
                    [*action.arg.split(":"), "", "", "", ""]
                )[:4]
                return self._person_callback(
                    action.op,
                    int(project_part or 0),
                    int(user_part or 0),
                    cb,
                    me,
                    all_branches=branch_part == ALL_BRANCHES,
                    days=max(1, min(int(days_part), MAX_RANGE_DAYS)) if days_part.isdigit() else 7,
                )
            return self._picker_callback(action.token, action.op, action.arg, cb, me)
        except Denied as exc:
            return CallbackOutcome(answer=str(exc)[:200], alert=True)
        except GitLabError as exc:
            log.warning("telegram callback: gitlab error", extra={"status": exc.status_code})
            return CallbackOutcome(answer="GitLab không trả lời được; thử lại sau.", alert=True)

    def _menu_callback(
        self, op: str, cb: Callback, me: Principal, page: int = 0
    ) -> CallbackOutcome:
        if op == "x":
            return CallbackOutcome(answer="Đã huỷ.", text="Đã huỷ.")
        if op == "noop":
            return CallbackOutcome(answer="Gõ /review type=… user=tên để lọc người khác.")
        if op not in ("commit", "mr"):
            return CallbackOutcome(answer="Nút này không còn hiệu lực.")
        repos = self._scope()
        if not repos:
            return CallbackOutcome(answer="Dự án chưa có repo nào.", alert=True)
        if len(repos) > 1:
            what = "commit gần đây" if op == "commit" else "MR đang mở"
            choices = [(p.id, p.path_with_namespace) for p in repos]
            pages = repo_pages(choices)
            page = min(max(page, 0), pages - 1)
            paging = f", trang {page + 1}/{pages}" if pages > 1 else ""
            return CallbackOutcome(
                text=f"Chọn repo để xem {what} ({len(repos)} repo{paging}):",
                markup=repo_keyboard("commit" if op == "commit" else "mr", choices, page),
            )
        return self._repo_callback(op, repos[0].id, cb, me)

    def _repo_callback(
        self, op: str, project_id: int, cb: Callback, me: Principal
    ) -> CallbackOutcome:
        project = next((p for p in self._scope() if p.id == project_id), None)
        if project is None or op not in ("commit", "mr"):
            return CallbackOutcome(answer="Repo không còn trong phạm vi bot.", alert=True)
        people, more = self._people_for_picker(me)
        if len(people) > 1:
            # Whose work? Everyone, or one of the people this person may review.
            what = "commit gần đây" if op == "commit" else "MR đang mở"
            return CallbackOutcome(
                text=f"{esc(project.path_with_namespace)} · {what}: của ai?",
                markup=people_keyboard(
                    "commit" if op == "commit" else "mr", project.id, people, more
                ),
            )
        return self._person_callback(op, project.id, 0, cb, me)

    def _people_for_picker(self, me: Principal) -> tuple[list[tuple[int, str]], int]:
        """(user_id, name) the caller may review, at most 12; the rest is a count."""
        query = select(User).where(User.active.is_not(False)).order_by(User.full_name, User.id)
        visible = me.visible_users()
        if visible is not None:
            query = query.where(User.id.in_(visible))
        users = list(self._s.scalars(query))
        shown = [(u.id, u.full_name or u.primary_email or f"#{u.id}") for u in users[:12]]
        return shown, max(0, len(users) - 12)

    def _person_callback(
        self,
        op: str,
        project_id: int,
        user_id: int,
        cb: Callback,
        me: Principal,
        *,
        all_branches: bool = False,
        days: int = 7,
    ) -> CallbackOutcome:
        project = next((p for p in self._scope() if p.id == project_id), None)
        if project is None or op not in ("commit", "mr"):
            return CallbackOutcome(answer="Repo không còn trong phạm vi bot.", alert=True)
        people: list[User] = []
        if user_id:
            person = self._s.get(User, user_id)
            if person is None or not me.can_see_user(person.id):
                return CallbackOutcome(answer="Bạn không review được người này.", alert=True)
            people = [person]
        every = all_branches and op == "commit"
        args = ReviewArgs(
            "today" if op == "commit" else "open",
            days=days,
            branch=ALL_BRANCHES if every else None,
            pick=True,
        )
        msg = Incoming(cb.update_id, cb.chat_id, "", cb.message_id, cb.from_id, cb.username, "", "")
        who = f" user={people[0].primary_email}" if people else ""
        on = " branch=all" if every else ""
        state = self._build_picker(
            op,
            project,
            args,
            people,
            me,
            msg,
            f"/review (menu) {op} {project.path_with_namespace}{who}{on}",
        )
        if state is None:
            branch = review_branch(project, args.branch)
            what = (
                f"commit nào trong {days} ngày qua trên {esc(branch_label(branch))}"
                if op == "commit"
                else "MR nào đang mở"
            )
            whose = f" của {esc(people[0].full_name or '')}" if people else ""
            hint = f"\n{BRANCH_HINT}" if op == "commit" and not every else ""
            markup = people_keyboard(
                "commit" if op == "commit" else "mr",
                project.id,
                *self._people_for_picker(me),
                all_branches=every,
            )
            if op == "commit" and not every:
                # The usual reason: the work is on a feature branch. One tap to look there.
                markup["inline_keyboard"].insert(0, all_branches_row(project.id, user_id, days))
            return CallbackOutcome(
                text=f"{esc(project.path_with_namespace)}: không có {what}{whose}.{hint}",
                markup=markup,
            )
        state.message_id = cb.message_id
        self._picker.save(state)
        return CallbackOutcome(text=render_text(state), markup=keyboard(state))

    def _picker_callback(
        self, token: str, op: str, arg: str, cb: Callback, me: Principal
    ) -> CallbackOutcome:
        state = self._picker.load(token)
        if state is None:
            return CallbackOutcome(
                answer="Danh sách đã hết hạn; gõ lại lệnh /review.",
                alert=True,
                text="Danh sách đã hết hạn; gõ lại lệnh /review.",
            )
        if cb.from_id != state.owner_from_id:
            return CallbackOutcome(
                answer="Danh sách này của người khác; gõ /review để có danh sách của bạn.",
                alert=True,
            )
        state.message_id = cb.message_id
        if op == "x":
            self._picker.delete(token)
            return CallbackOutcome(answer="Đã huỷ.", text="Đã huỷ.")
        if op == "who":
            people, more = self._people_for_picker(me)
            self._picker.delete(token)
            what = "commit gần đây" if state.kind == "commit" else "MR đang mở"
            return CallbackOutcome(
                text=f"{esc(state.scope_label)} · {what}: của ai?",
                markup=people_keyboard(
                    state.kind,
                    state.project_id,
                    people,
                    more,
                    all_branches=state.branch == ALL_BRANCHES,
                ),
            )
        if op == "br":
            # Same repo, person and window; the other branch scope (every branch <-> review).
            self._picker.delete(token)
            return self._person_callback(
                state.kind,
                state.project_id,
                state.person_id or 0,
                cb,
                me,
                all_branches=state.branch != ALL_BRANCHES,
                days=state.days,
            )
        if op == "t":
            try:
                idx = int(arg)
            except ValueError:
                return CallbackOutcome(answer="Nút lạ.")
            item = state.items[idx] if 0 <= idx < len(state.items) else None
            if item is None or not item.tickable(state.again):
                return CallbackOutcome(answer="Mục này không review được.")
            if idx in state.selected:
                state.selected.remove(idx)
            else:
                state.selected.append(idx)
            self._picker.save(state)
            return CallbackOutcome(text=render_text(state), markup=keyboard(state))
        if op == "again":
            # Deliberate: finished items become tickable (a new round each, old rounds kept).
            if not state.may_force:
                return CallbackOutcome(answer="Chỉ lead/admin được review lại.", alert=True)
            state.again = not state.again
            if not state.again:
                state.selected = [i for i in state.selected if state.items[i].selectable]
            self._picker.save(state)
            return CallbackOutcome(
                answer="Có thể chọn mục đã review." if state.again else "Chỉ chọn mục chưa review.",
                text=render_text(state),
                markup=keyboard(state),
            )
        if op == "all":
            state.selected = [i for i, it in enumerate(state.items) if it.tickable(state.again)][
                : self._settings.telegram_max_targets
            ]
            self._picker.save(state)
            return CallbackOutcome(
                answer=f"Đã chọn {len(state.selected)} mục.",
                text=render_text(state),
                markup=keyboard(state),
            )
        if op == "pg":
            try:
                state.page = max(0, min(int(arg), state.pages - 1))
            except ValueError:
                return CallbackOutcome(answer="Nút lạ.")
            self._picker.save(state)
            return CallbackOutcome(text=render_text(state), markup=keyboard(state))
        if op == "go":
            return self._picker_go(state, cb, me)
        return CallbackOutcome(answer="Nút này không còn hiệu lực.")

    def _latest_round(self, item: Item) -> int | None:
        """The newest round of a done item now (a forced rerun replaces exactly that one)."""
        query = select(Review.round).where(
            Review.target_type == item.kind,
            Review.reviewed_sha == item.sha,
            Review.status.in_(("done", "queued", "running")),
        )
        if item.kind == "mr":
            mr_id = self._s.scalar(
                select(MergeRequest.id).where(
                    MergeRequest.project_id == item.project_id, MergeRequest.iid == int(item.ref)
                )
            )
            query = query.where(Review.mr_id == mr_id)
        else:
            query = query.where(Review.commit_sha == item.sha)
        return self._s.scalar(query.order_by(Review.round.desc()).limit(1))

    def _picker_go(self, state: PickerState, cb: Callback, me: Principal) -> CallbackOutcome:
        chosen = [state.items[i] for i in sorted(set(state.selected)) if 0 <= i < len(state.items)]
        chosen = [it for it in chosen if it.tickable(state.again and state.may_force)]
        if not chosen:
            return CallbackOutcome(answer="Chưa chọn mục nào.", alert=True)
        if not self._limiter.allow(f"user:{me.user_id}"):
            return CallbackOutcome(
                answer="Bạn gửi quá nhiều lệnh review trong 10 phút.", alert=True
            )
        limit = self._settings.telegram_max_targets
        notes: list[str] = []
        if len(chosen) > limit:
            notes.append(f"Chỉ lấy {limit} mục đầu để giữ ngân sách LLM.")
            chosen = chosen[:limit]
        targets = [
            Target(
                it.kind,
                it.project_id,
                it.ref,
                it.sha,
                it.label,
                force=it.state == "done",
                round=self._latest_round(it) if it.state == "done" else None,
            )
            for it in chosen
            if me.may_review_work(it.author_user_id, self._bot.business_project_id)
        ]
        if not targets:
            return CallbackOutcome(
                answer="Các mục đã chọn nằm ngoài quyền review của bạn.", alert=True
            )
        msg = Incoming(
            cb.update_id, cb.chat_id, "", cb.message_id, cb.from_id, cb.username, "", state.raw
        )
        queued = self._enqueue(targets, me, msg, state.raw or "/review (picker)")
        self._picker.delete(state.token)
        lines = [
            *notes,
            f"Đã đưa <b>{len(queued)}</b> review vào hàng đợi; tôi sẽ báo kết quả ở đây khi xong.",
            *[f"• {esc(t.label)}" for t in queued[:10]],
        ]
        if len(queued) > 10:
            lines.append(f"… và {len(queued) - 10} mục nữa")
        return CallbackOutcome(answer=f"Đã xếp hàng {len(queued)} review.", text="\n".join(lines))

    # -- read-only commands --------------------------------------------------------------------

    def _review_url(self, review_id: int) -> str:
        return f"{self._settings.dashboard_url.rstrip('/')}/reviews/{review_id}"

    def _severity_counts(self, review_id: int) -> dict[str, int]:
        rows = self._s.execute(
            select(ReviewFinding.severity, func.count())
            .where(ReviewFinding.review_id == review_id)
            .group_by(ReviewFinding.severity)
        ).all()
        return {str(sev): int(n) for sev, n in rows}

    def _result_line(self, review: Review) -> str:
        if review.status != "done":
            reason = f" - {esc(review.error)}" if review.error else ""
            return f"Trạng thái: <b>{review.status}</b>{reason}"
        counts = self._severity_counts(review.id)
        parts = [f"{counts[s]} {s}" for s in ("critical", "high", "medium", "low") if counts.get(s)]
        findings = ", ".join(parts) if parts else "không có finding"
        score = f"{review.score:.0f}" if review.score is not None else "—"
        return f"Điểm <b>{score}</b>/100 · {findings}\n{esc(self._review_url(review.id))}"

    def _status(self, me: Principal) -> str:
        reviews = self._s.scalars(
            select(Review)
            .where(Review.author_user_id == me.user_id)
            .order_by(Review.id.desc())
            .limit(5)
        ).all()
        pending = self._s.scalar(
            select(func.count()).where(
                TelegramRequest.user_id == me.user_id, TelegramRequest.status == "queued"
            )
        )
        lines = [f"<b>Review gần đây của bạn</b> · {int(pending or 0)} yêu cầu đang chờ"]
        if not reviews:
            lines.append("Chưa có review nào.")
        for r in reviews:
            score = f"{r.score:.0f}" if r.score is not None else "—"
            when = (
                r.finished_at.astimezone(self._tz).strftime("%d/%m %H:%M") if r.finished_at else ""
            )
            lines.append(f"• {when} · {r.status} · {score} · {esc(self._review_url(r.id))}")
        return "\n".join(lines)

    def _critical(self, me: Principal, repo_hint: str | None) -> str:
        repos = self._pick_repos(repo_hint, one=False)
        repo_ids = [p.id for p in repos]
        mr_ids = select(MergeRequest.id).where(MergeRequest.project_id.in_(repo_ids))
        shas = select(Commit.sha).where(Commit.project_id.in_(repo_ids))
        query = (
            select(ReviewFinding, Review)
            .join(Review, Review.id == ReviewFinding.review_id)
            .where(
                Review.status == "done",
                ReviewFinding.status == "open",
                ReviewFinding.severity.in_(SEVERE),
                Review.mr_id.in_(mr_ids) | Review.commit_sha.in_(shas),
            )
        )
        visible = None if self._manages(me) else me.visible_users()
        if visible is not None:
            query = query.where(Review.author_user_id.in_(visible))
        counts = dict(
            self._s.execute(
                select(ReviewFinding.severity, func.count())
                .select_from(query.subquery())
                .group_by(ReviewFinding.severity)
            ).all()
        )
        total = int(sum(counts.values()))
        if not total:
            return "Không có finding critical/high nào đang mở trong phạm vi bạn xem được."
        crit = int(counts.get("critical", 0))
        rows = self._s.execute(query.order_by(Review.finished_at).limit(8)).all()
        lines = [f"<b>{crit} critical · {total - crit} high</b> đang mở (cũ nhất trước)"]
        now = self._clock()
        for finding, review in rows:
            age = (now - (review.finished_at or now)).days
            lines.append(
                f"• [{finding.severity}] {esc(finding.title or '')}"
                f" · <code>{esc(finding.file_path or '')}</code>"
                f" · {age} ngày · {esc(self._review_url(review.id))}"
            )
        if total > 8:
            lines.append(f"… và {total - 8} mục nữa trên Critical Board.")
        return "\n".join(lines)

    def _score(self, me: Principal, args: tuple[str, ...]) -> str:
        period = "week"
        hints = []
        for word in args:
            if word.casefold() in ("week", "month", "tuan", "tuần", "thang", "tháng"):
                period = "month" if word.casefold().startswith(("m", "th")) else "week"
            else:
                hints.append(word)
        user = self._people(tuple(hints), me)[0]
        rows = self._s.scalars(
            select(MetricsUserPeriod)
            .where(MetricsUserPeriod.user_id == user.id, MetricsUserPeriod.period_type == period)
            .order_by(MetricsUserPeriod.period_start.desc())
            .limit(2)
        ).all()
        if not rows:
            unit = "tháng" if period == "month" else "tuần"
            return f"Chưa có số liệu {unit} của {esc(user.full_name or '')}."
        cur = rows[0]
        prev = rows[1] if len(rows) > 1 else None

        def num(v: Any, digits: int = 1) -> str:
            return "—" if v is None else f"{float(v):.{digits}f}"

        def delta(key: str) -> str:
            if prev is None or getattr(cur, key) is None or getattr(prev, key) is None:
                return ""
            d = float(getattr(cur, key)) - float(getattr(prev, key))
            return f" ({'+' if d >= 0 else ''}{d:.1f})"

        label = "Tuần từ" if period == "week" else "Tháng"
        fix_pct = float(cur.fix_rate) * 100 if cur.fix_rate is not None else None
        return "\n".join(
            [
                f"<b>{esc(user.full_name or '')}</b> · {label} {cur.period_start:%d/%m/%Y}",
                f"Quality Index <b>{num(cur.quality_index)}</b>{delta('quality_index')}"
                f" · Điểm TB {num(cur.avg_score)}{delta('avg_score')}",
                f"Reviews {cur.reviews_count or 0} · critical {cur.critical or 0}"
                f" · high {cur.high or 0} · fix rate {num(fix_pct, 0)}%",
                "Điểm là tín hiệu coaching, không phải căn cứ duy nhất để đánh giá.",
            ]
        )

    def _sync(self, me: Principal, repo_hint: str | None) -> str:
        if not (me.is_admin or me.is_lead or self._manages(me)):
            raise Denied("/sync dành cho lead, người quản lý dự án hoặc admin.")
        if not self._limiter.allow(f"user:{me.user_id}"):
            return "Bạn gửi quá nhiều lệnh trong 10 phút; thử lại sau ít phút."
        repos = self._pick_repos(repo_hint, one=False)
        for p in repos:
            self._queue.enqueue(
                "app.collector.sync.sync_project_job",
                p.id,
                job_id=f"sync-project-{p.id}",
                job_timeout=SYNC_JOB_TIMEOUT,
            )
        audit(
            self._s,
            me.user_id,
            "telegram.sync",
            f"telegram_bot:{self._bot.id}",
            repos=[p.id for p in repos],
        )
        return f"Đã đưa {len(repos)} repo vào hàng đợi sync; vài phút nữa dữ liệu sẽ mới."


# -- answering finished requests ------------------------------------------------------------------


def finished_requests(
    session: Session, bot: TelegramBot, dashboard_url: str, now: datetime, max_age: timedelta
) -> list[tuple[TelegramRequest, str]]:
    """Queued requests whose targets all finished (or aged out), with the message to send."""
    out: list[tuple[TelegramRequest, str]] = []
    requests = session.scalars(
        select(TelegramRequest)
        .where(TelegramRequest.bot_id == bot.id, TelegramRequest.status == "queued")
        .order_by(TelegramRequest.id)
    ).all()
    for request in requests:
        lines: list[str] = []
        all_done = True
        for t in request.targets:
            review = _review_for(session, t)
            if review is None or review.status not in FINISHED:
                all_done = False
                continue
            lines.append(
                f"• {esc(str(t.get('label', '')))}\n  {_result(session, review, dashboard_url)}"
            )
        expired = request.created_at is not None and now - request.created_at > max_age
        if not all_done and not expired:
            continue
        if not all_done:
            lines.append("• Một số mục chưa xong sau 3 giờ; xem trạng thái bằng /status.")
        out.append((request, "<b>Kết quả review</b>\n" + "\n".join(lines)))
    return out


def _review_for(session: Session, target: dict[str, Any]) -> Review | None:
    sha = str(target.get("sha") or "")
    if target.get("kind") == "mr":
        mr_id = session.scalar(
            select(MergeRequest.id).where(
                MergeRequest.project_id == int(target.get("project_id") or 0),
                MergeRequest.iid == int(target.get("ref") or 0),
            )
        )
        if mr_id is None:
            return None
        query = select(Review).where(Review.mr_id == mr_id, Review.reviewed_sha == sha)
    else:
        query = select(Review).where(Review.commit_sha == sha)
    return session.scalars(query.order_by(Review.id.desc())).first()


def _result(session: Session, review: Review, dashboard_url: str) -> str:
    if review.status != "done":
        reason = f" - {esc(review.error)}" if review.error else ""
        return f"<b>{review.status}</b>{reason}"
    rows = session.execute(
        select(ReviewFinding.severity, func.count())
        .where(ReviewFinding.review_id == review.id)
        .group_by(ReviewFinding.severity)
    ).all()
    counts = {str(s): int(n) for s, n in rows}
    parts = [f"{counts[s]} {s}" for s in ("critical", "high", "medium", "low") if counts.get(s)]
    score = f"{review.score:.0f}" if review.score is not None else "—"
    verdict = ""
    if review.verdict:
        icon = VERDICT_ICON.get(review.verdict, "")
        verdict = f"{icon} <b>{VERDICT_LABEL.get(review.verdict, review.verdict)}</b> · "
    return (
        f"{verdict}Điểm <b>{score}</b>/100 · {', '.join(parts) if parts else 'không có finding'}"
        f"\n  {esc(dashboard_url.rstrip('/'))}/reviews/{review.id}"
    )
