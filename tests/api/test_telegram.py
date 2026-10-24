"""Telegram bots: admin API, account link, command handling, result posting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.main import app
from app.api.routers.telegram import get_link_codes, get_telegram_client_factory
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.config import Settings
from app.models import (
    Account,
    AuditLog,
    BusinessProject,
    Commit,
    MergeRequest,
    Review,
    ReviewFinding,
    TelegramBot,
    TelegramRequest,
    User,
)
from app.telegram.client import TelegramClient, TelegramError
from app.telegram.poller import poll_bot
from app.telegram.service import (
    AllowAll,
    BotService,
    Incoming,
    MemoryLinkCodes,
    MemoryWebhookGuard,
    incoming,
)
from tests.api.conftest import BOX, FakeQueue, World

pytestmark = pytest.mark.integration

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"  # fake token for tests


class FakeTelegram(TelegramClient):
    """Records what the bot sends; serves canned updates."""

    def __init__(self, token: str = TOKEN, username: str = "CodePulseBot") -> None:
        super().__init__(token, transport=None)
        self.token = token
        self.username = username
        self.sent: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    def get_me(self) -> dict[str, Any]:
        return {"username": self.username, "can_join_groups": True}

    def get_updates(self, offset: int | None, timeout: int = 0, limit: int = 50) -> list[Any]:
        return [u for u in self.updates if offset is None or u["update_id"] >= offset][:limit]

    def send_message(
        self, chat_id: int, text: str, reply_to_message_id: int | None = None
    ) -> dict[str, Any]:
        self.sent.append({"chat_id": chat_id, "text": text, "reply_to": reply_to_message_id})
        return {"message_id": len(self.sent)}


class FakeGitLab(GitLabClient):
    """GitLab that knows nothing: every lookup is a 404, so the DB answers."""

    def __init__(self) -> None:
        super().__init__("https://gitlab.example.com", "t", max_retries=0)

    def merge_request(self, project_id: int, iid: int) -> Any:
        raise GitLabError("GET mr -> HTTP 404", 404)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        raise GitLabError("GET -> HTTP 404", 404)


def no_gitlab(connection: Any) -> GitLabClient:
    return FakeGitLab()


def message(
    text: str, from_id: int = 777, chat_id: int = -100, username: str | None = "tam"
) -> Incoming:
    return Incoming(
        update_id=1,
        chat_id=chat_id,
        chat_type="group",
        message_id=5,
        from_id=from_id,
        username=username,
        first_name="Tâm",
        text=text,
    )


@pytest.fixture
def bot(db_session: Session, world: World) -> TelegramBot:
    bp = BusinessProject(name="OmiCRM")
    db_session.add(bp)
    db_session.flush()
    world.project.business_project_id = bp.id
    bot = TelegramBot(
        name="OmiCRM bot",
        bot_username="CodePulseBot",
        token_encrypted=BOX.encrypt(TOKEN),
        business_project_id=bp.id,
    )
    db_session.add(bot)
    db_session.flush()
    return bot


def service(
    db_session: Session,
    bot: TelegramBot,
    settings: Settings,
    queue: FakeQueue,
    codes: MemoryLinkCodes | None = None,
    clock: datetime | None = None,
) -> BotService:
    return BotService(
        db_session,
        bot,
        settings,
        queue,
        no_gitlab,
        codes or MemoryLinkCodes(),
        AllowAll(),
        clock=lambda: clock or datetime(2026, 10, 24, 9, tzinfo=UTC),
    )


def link(db_session: Session, user: User, telegram_id: int = 777) -> Account:
    account = Account(kind="telegram", external_key=str(telegram_id), user_id=user.id)
    db_session.add(account)
    db_session.flush()
    return account


# -- identity -------------------------------------------------------------------------------------


def test_unknown_sender_gets_link_instructions_and_nothing_is_stored(
    db_session: Session, bot: TelegramBot, settings: Settings, queue: FakeQueue
) -> None:
    reply = service(db_session, bot, settings, queue).handle(message("/review mr 7"))
    assert reply and "chưa gắn" in reply and "/link" in reply
    # Anyone can message a bot: strangers never become rows the admin has to look at.
    assert db_session.scalars(select(Account).where(Account.kind == "telegram")).first() is None
    assert queue.jobs == []


def test_link_code_links_the_account_once(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    codes = MemoryLinkCodes()
    code = codes.issue(world.an.id)
    svc = service(db_session, bot, settings, queue, codes)
    assert "Đã liên kết" in (svc.handle(message(f"/link {code.lower()}")) or "")
    account = db_session.scalars(select(Account).where(Account.kind == "telegram")).one()
    assert account.user_id == world.an.id and account.display_name == "@tam"
    assert "không đúng" in (svc.handle(message(f"/link {code}", from_id=778)) or "")
    whoami = svc.handle(message("/whoami")) or ""
    assert "An" in whoami and "thành viên" in whoami and "OmiCRM" in whoami


def test_help_is_silent_for_plain_text_in_groups(
    db_session: Session, bot: TelegramBot, settings: Settings, queue: FakeQueue
) -> None:
    svc = service(db_session, bot, settings, queue)
    assert svc.handle(message("hello")) is None
    private = Incoming(1, 777, "private", 5, 777, "tam", "Tâm", "hello")
    assert "/review" in (svc.handle(private) or "")


def test_chat_allowlist_silences_other_chats(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    bot.allowed_chat_ids = [-100]
    link(db_session, world.an)
    svc = service(db_session, bot, settings, queue)
    assert svc.handle(message("/help", chat_id=-200)) is None
    assert svc.handle(message("/help", chat_id=-100))


# -- review commands -------------------------------------------------------------------------------


def test_member_reviews_their_own_mr_and_a_request_is_recorded(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    an_account = Account(kind="gitlab_user", external_key="501", user_id=world.an.id)
    db_session.add(an_account)
    db_session.flush()
    mr = MergeRequest(
        project_id=world.project.id,
        iid=8,
        head_sha="c" * 40,
        author_account_id=an_account.id,
        title="Add parser",
        state="opened",
    )
    db_session.add(mr)
    db_session.flush()
    link(db_session, world.an)
    reply = service(db_session, bot, settings, queue).handle(message("/review mr !8 demo/p")) or ""
    assert "Đã đưa <b>1</b> review" in reply and "demo/p !8" in reply
    func, args, kwargs = queue.jobs[0]
    assert func == "app.workers.review_jobs.review_mr_job"
    assert args == (world.project.id, 8, "c" * 40)
    assert kwargs["job_id"] == f"review-mr-{world.project.id}-8-{'c' * 12}"
    request = db_session.scalars(select(TelegramRequest)).one()
    assert request.user_id == world.an.id and request.chat_id == -100
    assert request.targets[0]["kind"] == "mr" and request.targets[0]["ref"] == "8"
    assert (
        db_session.scalar(select(AuditLog).where(AuditLog.action == "telegram.review")) is not None
    )


def test_member_cannot_review_someone_elses_work_but_lead_can(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    binh_account = Account(kind="git_email", external_key="binh@x.vn", user_id=world.binh.id)
    db_session.add(binh_account)
    db_session.flush()
    commit = Commit(sha="e" * 40, project_id=world.project.id, author_account_id=binh_account.id)
    db_session.add(commit)
    db_session.flush()
    link(db_session, world.an, 777)
    link(db_session, world.lead, 888)
    link(db_session, world.outsider, 999)
    svc = service(db_session, bot, settings, queue)
    denied = svc.handle(message(f"/review commit {'e' * 8}")) or ""
    assert "chỉ review được" in denied and queue.jobs == []
    # A lead of another team is not Binh's lead either.
    assert "chỉ review được" in (
        svc.handle(message(f"/review commit {'e' * 8}", from_id=999)) or ""
    )
    ok = svc.handle(message(f"/review commit {'e' * 8}", from_id=888)) or ""
    assert "Đã đưa <b>1</b> review" in ok
    assert queue.jobs[0][0] == "app.workers.review_jobs.review_commit_job"
    assert queue.jobs[0][1] == (world.project.id, "e" * 40)


def test_already_reviewed_target_returns_the_result_instead_of_queueing(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link(db_session, world.an)
    reply = service(db_session, bot, settings, queue).handle(message("/review mr 7")) or ""
    assert "đã được review" in reply and "Điểm <b>50</b>" in reply and "1 critical" in reply
    assert f"/reviews/{world.an_review.id}" in reply
    assert queue.jobs == []


def test_failed_review_is_requeued(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link(db_session, world.binh)
    reply = (
        service(db_session, bot, settings, queue).handle(message(f"/review commit {'b' * 10}"))
        or ""
    )
    assert "Đã đưa <b>1</b> review" in reply
    db_session.refresh(world.binh_review)
    assert world.binh_review.status == "queued"


def test_today_reviews_todays_commits_of_the_people_a_lead_names(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    accounts = {
        name: Account(
            kind="git_email", external_key=f"{name}@x.vn", user_id=getattr(world, name).id
        )
        for name in ("an", "binh")
    }
    db_session.add_all(accounts.values())
    db_session.flush()
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)  # 16:00 in Asia/Ho_Chi_Minh
    rows = [
        Commit(
            sha="1" * 40,
            project_id=world.project.id,
            author_account_id=accounts["an"].id,
            authored_at=now,
        ),
        Commit(
            sha="2" * 40,
            project_id=world.project.id,
            author_account_id=accounts["binh"].id,
            authored_at=now,
        ),
        Commit(  # yesterday: not today's work
            sha="3" * 40,
            project_id=world.project.id,
            author_account_id=accounts["an"].id,
            authored_at=now - timedelta(days=1),
        ),
        Commit(  # merge commits are skipped
            sha="4" * 40,
            project_id=world.project.id,
            author_account_id=accounts["an"].id,
            authored_at=now,
            is_merge=True,
        ),
    ]
    db_session.add_all(rows)
    db_session.flush()
    link(db_session, world.lead, 888)
    svc = service(db_session, bot, settings, queue, clock=now)
    reply = svc.handle(message("/review today an@x.vn binh@x.vn", from_id=888)) or ""
    assert "Đã đưa <b>2</b> review" in reply
    assert sorted(j[1][1] for j in queue.jobs) == ["1" * 40, "2" * 40]
    # Using "@an" requires a gitlab username; fall back to a name part.
    assert "Không tìm thấy người '@nobody'" in (
        svc.handle(message("/review today @nobody", from_id=888)) or ""
    )


def test_open_is_lead_only_scoped_to_the_leads_people_and_skips_reviewed_mrs(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    outsider_acc = Account(kind="gitlab_user", external_key="900", user_id=world.outsider.id)
    db_session.add(outsider_acc)
    db_session.flush()
    db_session.add_all(
        [
            # Unmapped author, hostile title: queued for the lead, title escaped in the reply.
            MergeRequest(
                project_id=world.project.id,
                iid=9,
                head_sha="9" * 40,
                state="opened",
                title="<b>x</b> & co",
            ),
            # Authored by someone the Backend lead does not lead: not theirs to review.
            MergeRequest(
                project_id=world.project.id,
                iid=10,
                head_sha="1" * 40,
                state="opened",
                author_account_id=outsider_acc.id,
            ),
        ]
    )
    world.mr.state = "opened"  # already has a done review for its head
    db_session.flush()
    link(db_session, world.an, 777)
    link(db_session, world.lead, 888)
    link(db_session, world.admin, 999)
    svc = service(db_session, bot, settings, queue)
    assert "dành cho lead" in (svc.handle(message("/review open")) or "")
    reply = svc.handle(message("/review open", from_id=888)) or ""
    assert "Đã đưa <b>1</b> review" in reply and "!9" in reply and "!10" not in reply
    assert "&lt;b&gt;x&lt;/b&gt; &amp; co" in reply and "<b>x</b>" not in reply
    # The admin sees everyone: !9 and !10 (the fake queue wrote no review row for !9 yet).
    reply = svc.handle(message("/review open", from_id=999)) or ""
    assert "Đã đưa <b>2</b> review" in reply and "!10" in reply


def test_status_critical_score_and_repos(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link(db_session, world.an, 777)
    link(db_session, world.lead, 888)
    svc = service(db_session, bot, settings, queue)
    status = svc.handle(message("/status")) or ""
    assert "Review gần đây" in status and f"/reviews/{world.an_review.id}" in status
    critical = svc.handle(message("/critical", from_id=888)) or ""
    assert "1 critical" in critical and "SQL injection" in critical
    score = svc.handle(message("/score an@x.vn", from_id=888)) or ""
    assert "Quality Index <b>75.0</b>" in score
    assert "chỉ lead" in (svc.handle(message("/score binh")) or "")  # An cannot see Binh
    assert "demo/p" in (svc.handle(message("/repos")) or "")
    assert "dành cho lead" in (svc.handle(message("/sync")) or "")
    assert "1 repo" in (svc.handle(message("/sync", from_id=888)) or "")
    assert queue.jobs[-1][0] == "app.collector.sync.sync_project_job"


# -- poller ----------------------------------------------------------------------------------------


def test_poll_bot_answers_updates_advances_offset_and_posts_results(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link(db_session, world.an)
    tg = FakeTelegram()
    tg.updates = [
        {
            "update_id": 10,
            "message": {
                "message_id": 1,
                "chat": {"id": -100, "type": "group"},
                "from": {"id": 777, "username": "tam"},
                "text": "/review mr 7",
            },
        },
        {
            "update_id": 11,
            "message": {
                "message_id": 2,
                "chat": {"id": -100, "type": "group"},
                "from": {"id": 777},
                "text": "photo caption without text",
            },
        },
        {"update_id": 12, "edited_message": {"text": "/help"}},
    ]
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)
    handled = poll_bot(
        db_session, bot, tg, settings, queue, no_gitlab, MemoryLinkCodes(), AllowAll(), lambda: now
    )
    assert handled == 3 and bot.last_update_id == 12 and bot.last_error is None
    assert len(tg.sent) == 1  # plain text in a group gets no answer; edits are not messages
    assert tg.sent[0]["reply_to"] == 1 and "đã được review" in tg.sent[0]["text"]

    # A queued request whose review finished is answered on the next tick.
    commit = Commit(sha="f" * 40, project_id=world.project.id)
    db_session.add(commit)
    db_session.flush()
    request = TelegramRequest(
        bot_id=bot.id,
        chat_id=-100,
        message_id=3,
        user_id=world.an.id,
        command="/review commit f",
        targets=[
            {
                "kind": "commit",
                "project_id": world.project.id,
                "ref": "f" * 40,
                "sha": "f" * 40,
                "label": "demo/p ffffffff",
            }
        ],
    )
    review = Review(
        target_type="commit",
        commit_sha="f" * 40,
        reviewed_sha="f" * 40,
        status="done",
        author_user_id=world.an.id,
        score=Decimal(88),
        rubric_version="v1",
    )
    db_session.add_all([request, review])
    db_session.flush()
    db_session.add(ReviewFinding(review_id=review.id, severity="high", category="bug", title="x"))
    tg.updates = []
    poll_bot(
        db_session, bot, tg, settings, queue, no_gitlab, MemoryLinkCodes(), AllowAll(), lambda: now
    )
    assert request.status == "notified"
    assert "Kết quả review" in tg.sent[-1]["text"] and "Điểm <b>88</b>" in tg.sent[-1]["text"]
    assert "1 high" in tg.sent[-1]["text"]


def test_poll_bot_survives_a_crashing_command_and_marks_undeliverable_results_failed(
    db_session: Session,
    bot: TelegramBot,
    world: World,
    settings: Settings,
    queue: FakeQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link(db_session, world.an)
    tg = FakeTelegram()
    tg.updates = [
        {
            "update_id": 20,
            "message": {
                "message_id": 1,
                "chat": {"id": -100, "type": "group"},
                "from": {"id": 777, "username": "tam"},
                "text": "/status",
            },
        }
    ]
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)

    def boom(self: BotService, msg: Incoming) -> str | None:
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(BotService, "handle", boom)
    db_session.commit()  # release the fixture savepoint: the poller's rollback must not undo setup
    poll_bot(
        db_session, bot, tg, settings, queue, no_gitlab, MemoryLinkCodes(), AllowAll(), lambda: now
    )
    assert bot.last_update_id == 20  # acknowledged, so the bot is not wedged on this message
    assert "Có lỗi" in tg.sent[-1]["text"]
    monkeypatch.undo()

    # A finished result whose chat refuses delivery (bot kicked) stops being retried.
    commit = Commit(sha="e" * 40, project_id=world.project.id)
    db_session.add(commit)
    db_session.flush()
    request = TelegramRequest(
        bot_id=bot.id,
        chat_id=-100,
        message_id=3,
        user_id=world.an.id,
        command="/review commit e",
        targets=[
            {
                "kind": "commit",
                "project_id": world.project.id,
                "ref": "e" * 40,
                "sha": "e" * 40,
                "label": "demo/p eeeeeeee",
            }
        ],
    )
    review = Review(
        target_type="commit",
        commit_sha="e" * 40,
        reviewed_sha="e" * 40,
        status="done",
        author_user_id=world.an.id,
        score=Decimal(70),
        rubric_version="v1",
    )
    db_session.add_all([request, review])
    db_session.flush()
    tg.updates = []

    def kicked(
        self: FakeTelegram, chat_id: int, text: str, reply_to_message_id: int | None = None
    ) -> dict[str, Any]:
        raise TelegramError("sendMessage -> HTTP 403: Forbidden: bot was kicked", 403)

    monkeypatch.setattr(FakeTelegram, "send_message", kicked)
    poll_bot(
        db_session, bot, tg, settings, queue, no_gitlab, MemoryLinkCodes(), AllowAll(), lambda: now
    )
    assert request.status == "failed" and request.error and "403" in request.error
    assert bot.last_error is None


def webhook_409() -> TelegramError:
    return TelegramError(
        "getUpdates -> HTTP 409: Conflict: can't use getUpdates method while webhook is "
        "active; use deleteWebhook",
        409,
    )


class WebhookedTelegram(FakeTelegram):
    """Refuses getUpdates until the webhook is deleted, like Telegram does."""

    def __init__(
        self, errors: list[TelegramError], delete_error: TelegramError | None = None
    ) -> None:
        super().__init__()
        self.errors = errors
        self.delete_error = delete_error
        self.webhook_deleted = 0

    def get_updates(self, offset: int | None, timeout: int = 0, limit: int = 50) -> list[Any]:
        if self.errors:
            raise self.errors.pop(0)
        return super().get_updates(offset, timeout, limit)

    def delete_webhook(self) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.webhook_deleted += 1


def test_poll_bot_deletes_a_foreign_webhook_and_keeps_polling(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link(db_session, world.an)
    tg = WebhookedTelegram([webhook_409()])
    tg.updates = [
        {
            "update_id": 30,
            "message": {
                "message_id": 1,
                "chat": {"id": -100, "type": "group"},
                "from": {"id": 777, "username": "tam"},
                "text": "/whoami",
            },
        }
    ]
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)
    handled = poll_bot(
        db_session, bot, tg, settings, queue, no_gitlab, MemoryLinkCodes(), AllowAll(), lambda: now
    )
    assert tg.webhook_deleted == 1
    assert handled == 1 and bot.last_update_id == 30 and bot.last_error is None
    assert tg.sent and tg.sent[0]["reply_to"] == 1
    row = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "telegram.bot.webhook_deleted")
    ).one()
    assert row.target == f"telegram_bot:{bot.id}" and row.actor_user_id is None

    # A second tick is a plain poll: no webhook call.
    tg.updates = []
    poll_bot(
        db_session, bot, tg, settings, queue, no_gitlab, MemoryLinkCodes(), AllowAll(), lambda: now
    )
    assert tg.webhook_deleted == 1


@pytest.mark.parametrize(
    "error",
    [
        TelegramError("getUpdates -> HTTP 409: Conflict: terminated by other getUpdates", 409),
        TelegramError("getUpdates -> HTTP 401: Unauthorized", 401),
    ],
)
def test_poll_bot_leaves_other_errors_to_the_caller(
    db_session: Session,
    bot: TelegramBot,
    settings: Settings,
    queue: FakeQueue,
    error: TelegramError,
) -> None:
    tg = WebhookedTelegram([error])
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)
    with pytest.raises(TelegramError):
        poll_bot(
            db_session,
            bot,
            tg,
            settings,
            queue,
            no_gitlab,
            MemoryLinkCodes(),
            AllowAll(),
            lambda: now,
        )
    assert tg.webhook_deleted == 0


def test_poll_bot_gives_up_when_the_webhook_comes_back(
    db_session: Session, bot: TelegramBot, settings: Settings, queue: FakeQueue
) -> None:
    tg = WebhookedTelegram([webhook_409(), webhook_409()])
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)
    with pytest.raises(TelegramError) as info:
        poll_bot(
            db_session,
            bot,
            tg,
            settings,
            queue,
            no_gitlab,
            MemoryLinkCodes(),
            AllowAll(),
            lambda: now,
        )
    assert info.value.status_code == 409 and tg.webhook_deleted == 1


def test_poll_bot_deletes_a_returning_webhook_only_once_per_ttl(
    db_session: Session, bot: TelegramBot, settings: Settings, queue: FakeQueue
) -> None:
    """The other system re-registers its webhook: no tug of war, the admin sees the conflict."""
    ticks = iter([0.0, 5.0, 3700.0])
    guard = MemoryWebhookGuard(clock=lambda: next(ticks), ttl_s=3600)
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)

    def poll(tg: WebhookedTelegram) -> int:
        return poll_bot(
            db_session,
            bot,
            tg,
            settings,
            queue,
            no_gitlab,
            MemoryLinkCodes(),
            AllowAll(),
            lambda: now,
            guard,
        )

    first = WebhookedTelegram([webhook_409()])
    assert poll(first) == 0 and first.webhook_deleted == 1

    second = WebhookedTelegram([webhook_409()])
    with pytest.raises(TelegramError) as info:
        poll(second)
    assert second.webhook_deleted == 0 and info.value.status_code == 409
    assert "webhook came back" in str(info.value) and "while webhook is active" in str(info.value)

    third = WebhookedTelegram([webhook_409()])  # the TTL has passed: one more attempt
    assert poll(third) == 0 and third.webhook_deleted == 1


def test_poll_bot_propagates_a_failing_delete_webhook(
    db_session: Session, bot: TelegramBot, settings: Settings, queue: FakeQueue
) -> None:
    tg = WebhookedTelegram(
        [webhook_409()], delete_error=TelegramError("deleteWebhook -> HTTP 401: Unauthorized", 401)
    )
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)
    with pytest.raises(TelegramError) as info:
        poll_bot(
            db_session,
            bot,
            tg,
            settings,
            queue,
            no_gitlab,
            MemoryLinkCodes(),
            AllowAll(),
            lambda: now,
        )
    assert info.value.status_code == 401


def test_incoming_ignores_bots_and_non_text() -> None:
    assert (
        incoming(
            {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 1},
                    "from": {"id": 2, "is_bot": True},
                    "text": "/x",
                },
            }
        )
        is None
    )
    assert incoming({"update_id": 1, "channel_post": {}}) is None
    msg = incoming(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 2, "first_name": "A"},
                "text": "/help",
            },
        }
    )
    assert msg is not None and msg.chat_type == "private" and msg.username is None


# -- admin API -------------------------------------------------------------------------------------


@pytest.fixture
def api(client: TestClient) -> tuple[TestClient, FakeTelegram, MemoryLinkCodes]:
    tg = FakeTelegram()
    codes = MemoryLinkCodes()
    app.dependency_overrides[get_telegram_client_factory] = lambda: lambda token: tg
    app.dependency_overrides[get_link_codes] = lambda: codes
    return client, tg, codes


def test_admin_creates_tests_updates_and_deletes_a_bot(
    api: tuple[TestClient, FakeTelegram, MemoryLinkCodes],
    db_session: Session,
    world: World,
    as_user: Any,
) -> None:
    client = api[0]
    bp = BusinessProject(name="OmiCRM")
    db_session.add(bp)
    db_session.flush()
    body = {"name": "OmiCRM bot", "token": TOKEN, "business_project_id": bp.id}
    assert (
        client.post("/api/v1/telegram/bots", json=body, headers=as_user(world.lead)).status_code
        == 403
    )
    created = client.post("/api/v1/telegram/bots", json=body, headers=as_user(world.admin))
    assert created.status_code == 201, created.text
    out = created.json()
    assert (
        out["bot_username"] == "CodePulseBot" and "token" not in out and TOKEN not in created.text
    )
    bot_id = out["id"]
    # One bot per dự án.
    dup = client.post("/api/v1/telegram/bots", json=body, headers=as_user(world.admin))
    assert dup.status_code == 409
    # Pinning to a repo of another dự án is refused.
    bad = client.patch(
        f"/api/v1/telegram/bots/{bot_id}",
        json={"project_id": world.project.id},
        headers=as_user(world.admin),
    )
    assert bad.status_code == 422
    world.project.business_project_id = bp.id
    db_session.flush()
    ok = client.patch(
        f"/api/v1/telegram/bots/{bot_id}",
        json={"project_id": world.project.id, "enabled": False},
        headers=as_user(world.admin),
    )
    assert ok.status_code == 200 and ok.json()["enabled"] is False
    test = client.post(f"/api/v1/telegram/bots/{bot_id}/test", headers=as_user(world.admin))
    assert test.status_code == 200 and test.json()["ok"] is True
    assert (
        client.get(f"/api/v1/telegram/bots/{bot_id}/requests", headers=as_user(world.admin)).json()
        == []
    )
    listed = client.get("/api/v1/telegram/bots", headers=as_user(world.admin)).json()
    assert [b["id"] for b in listed] == [bot_id]
    assert (
        client.delete(f"/api/v1/telegram/bots/{bot_id}", headers=as_user(world.admin)).status_code
        == 204
    )
    actions = set(db_session.scalars(select(AuditLog.action)))
    assert {
        "telegram.bot.create",
        "telegram.bot.update",
        "telegram.bot.test",
        "telegram.bot.delete",
    } <= actions


def test_bot_token_is_validated_with_getme(
    api: tuple[TestClient, FakeTelegram, MemoryLinkCodes],
    db_session: Session,
    world: World,
    as_user: Any,
) -> None:
    client, _, _ = api
    bp = BusinessProject(name="OmiCRM")
    db_session.add(bp)
    db_session.flush()
    bad_shape = {"name": "x", "token": "not-a-token", "business_project_id": bp.id}
    assert (
        client.post(
            "/api/v1/telegram/bots", json=bad_shape, headers=as_user(world.admin)
        ).status_code
        == 422
    )


def test_person_gets_a_link_code_and_can_unlink(
    api: tuple[TestClient, FakeTelegram, MemoryLinkCodes],
    db_session: Session,
    world: World,
    as_user: Any,
) -> None:
    client, _, codes = api
    issued = client.post("/api/v1/me/telegram/link-code", headers=as_user(world.an)).json()
    assert len(issued["code"]) == 6 and issued["expires_in_s"] == 900
    assert codes.redeem(issued["code"]) == world.an.id
    account = link(db_session, world.an, 4242)
    mine = client.get("/api/v1/me/telegram", headers=as_user(world.an)).json()
    assert [a["external_key"] for a in mine] == ["4242"]
    # Someone else cannot unlink my account.
    assert (
        client.delete(f"/api/v1/me/telegram/{account.id}", headers=as_user(world.binh)).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/me/telegram/{account.id}", headers=as_user(world.an)).status_code
        == 204
    )
    assert client.get("/api/v1/me/telegram", headers=as_user(world.an)).json() == []
