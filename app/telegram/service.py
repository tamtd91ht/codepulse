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
from app.review.rubric import RUBRIC_VERSION
from app.telegram.commands import Command, ReviewArgs, match_repo, parse, parse_review
from app.webhook.jobs import REVIEW_JOB_TIMEOUT, REVIEW_RETRY

log = logging.getLogger(__name__)

TELEGRAM_KIND = "telegram"
LINK_CODE_TTL_S = 15 * 60
LINK_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
FINISHED = ("done", "failed", "skipped", "superseded")
ACTIVE = ("queued", "running", "done")
# Targets a bulk command (today / open) leaves alone: handled, or skipped on purpose.
TAKEN = ("queued", "running", "done", "skipped")
SEVERE = ("critical", "high")

esc = html.escape


# -- small stores (Redis in production, memory in tests) ------------------------------------------


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


# -- targets --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    kind: str  # mr | commit
    project_id: int
    ref: str  # iid for an MR, full sha for a commit
    sha: str  # reviewed sha (MR head or the commit itself)
    label: str  # "omicrm/api !12" / "omicrm/api a1b2c3d4"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "project_id": self.project_id,
            "ref": self.ref,
            "sha": self.sha,
            "label": self.label,
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
    ) -> None:
        self._s = session
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
            "/review mr &lt;số|link&gt; [repo] - review một merge request",
            "/review commit &lt;sha|link&gt; [repo] - review một commit",
            "/review last [repo] [người] - commit gần nhất của một người",
            "/review lastmr [repo] [người] - MR gần nhất của một người",
            "/review today [repo] [người...] - các commit hôm nay của một hoặc nhiều người",
            "/review open [repo] - các MR đang mở chưa được review (lead)",
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
        repos = match_repo(self._scope(), hint, lambda p: p.path_with_namespace)
        if not repos:
            raise Denied(
                f"Không tìm thấy repo '{esc(hint)}' trong {esc(self._scope_label())}. Gõ /repos."
                if hint
                else "Dự án này chưa có repo nào đang theo dõi."
            )
        if one and len(repos) > 1:
            names = ", ".join(f"<code>{esc(p.path_with_namespace)}</code>" for p in repos[:8])
            raise Denied(f"Có nhiều repo khớp, hãy nói rõ: {names}")
        return repos

    # -- people --------------------------------------------------------------------------------

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
            if not me.can_see_user(user.id):
                raise Denied(
                    f"Bạn không review được việc của {esc(user.full_name or hint)}:"
                    " chỉ lead của người đó hoặc admin."
                )
            users[user.id] = user
        return list(users.values())

    def _find_user(self, hint: str, me: Principal) -> User | None:
        word = hint.strip()
        if word.casefold() == "me":
            return self._s.get(User, me.user_id)
        if word.startswith("@"):
            account = self._s.scalars(
                select(Account).where(
                    Account.kind == "gitlab_user",
                    func.lower(Account.display_name) == word[1:].casefold(),
                    Account.user_id.is_not(None),
                )
            ).first()
            if account and account.user_id is not None:
                return self._s.get(User, account.user_id)
            word = word[1:]
        if "@" in word:
            return self._s.scalars(
                select(User).where(func.lower(User.primary_email) == word.casefold())
            ).first()
        if len(word) < 2:
            return None
        matches = list(
            self._s.scalars(
                select(User)
                .where(
                    User.active.is_not(False),
                    func.lower(User.full_name).contains(word.casefold(), autoescape=True),
                )
                .order_by(User.id)
                .limit(3)
            )
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Only names the sender may see are spelled out.
            visible = me.visible_users()
            shown = [u for u in matches if visible is None or u.id in visible]
            names = ", ".join(esc(u.full_name or "") for u in shown)
            raise Denied(
                f"'{esc(hint)}' khớp nhiều người{f' ({names})' if names else ''};"
                " dùng email hoặc @username."
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

    def _may_review_author(self, author_user_id: int | None, me: Principal, what: str) -> None:
        if author_user_id is not None and me.can_see_user(author_user_id):
            return
        if author_user_id is None and (me.is_admin or me.is_lead):
            return
        raise Denied(
            f"Bạn chỉ review được {what} của mình hoặc của người mình lead"
            " (tác giả chưa gắn người thì cần lead/admin)."
        )

    # -- review commands -----------------------------------------------------------------------

    def _review(self, command: Command, msg: Incoming, me: Principal) -> str:
        parsed = parse_review(command.args)
        if isinstance(parsed, str):
            return esc(parsed)
        if not self._limiter.allow(f"user:{me.user_id}"):
            return "Bạn gửi quá nhiều lệnh review trong 10 phút; thử lại sau ít phút."
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
        start = datetime.combine(today, datetime.min.time(), self._tz)
        end = start + timedelta(days=1)
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
                self._nothing_for(people, f"commit mới hôm nay ({today:%d/%m}, chưa review)")
                + "\nDữ liệu theo lần sync gần nhất; /sync để lấy mới."
            )
        return self._cap(targets, notes)

    def _target_open(self, args: ReviewArgs, me: Principal) -> tuple[list[Target], list[str]]:
        if not (me.is_admin or me.is_lead):
            raise Denied("/review open dành cho lead hoặc admin.")
        repos = self._pick_repos(args.repo_hint, one=False)
        reviewed = select(Review.mr_id).where(Review.mr_id.is_not(None), Review.status.in_(TAKEN))
        query = select(MergeRequest).where(
            MergeRequest.project_id.in_([p.id for p in repos]),
            MergeRequest.state == "opened",
            MergeRequest.head_sha.is_not(None),
            MergeRequest.id.not_in(reviewed),
        )
        visible = me.visible_users()
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
                    job_id=f"review-mr-{t.project_id}-{t.ref}-{t.sha[:12]}",
                    job_timeout=REVIEW_JOB_TIMEOUT,
                    retry=REVIEW_RETRY,
                )
            else:
                self._queue.enqueue(
                    "app.workers.review_jobs.review_commit_job",
                    t.project_id,
                    t.sha,
                    job_id=f"review-commit-{t.sha[:12]}",
                    job_timeout=REVIEW_JOB_TIMEOUT,
                    retry=REVIEW_RETRY,
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
        visible = me.visible_users()
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
        if not (me.is_admin or me.is_lead):
            raise Denied("/sync dành cho lead hoặc admin.")
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
    return (
        f"Điểm <b>{score}</b>/100 · {', '.join(parts) if parts else 'không có finding'}"
        f"\n  {esc(dashboard_url.rstrip('/'))}/reviews/{review.id}"
    )
