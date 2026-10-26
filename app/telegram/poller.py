"""Scheduler job: poll every enabled bot, answer commands, post finished results.

Short polling (getUpdates with offset) every few seconds from the scheduler process; one
advisory lock per bot so two schedulers never answer the same message twice. A failing bot
records `last_error` and never blocks the others.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import Redis
from rq import Queue
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.admin_settings import with_overrides
from app.api.audit import audit
from app.collector.gitlab.client import GitLabClient
from app.collector.sync import ClientFactory, Enqueuer
from app.config import Settings, get_settings
from app.crypto import SecretBox, secret_box
from app.db import get_redis, get_sessionmaker
from app.models import Connection, TelegramBot
from app.review.providers import build_provider
from app.telegram.client import TelegramClient, TelegramError
from app.telegram.picker import PickerStore, RedisPickerStore
from app.telegram.resolve import LLMResolver, NoResolver, Resolver
from app.telegram.service import (
    AllowAll,
    BotService,
    Callback,
    CallbackOutcome,
    CommandLimiter,
    LinkCodes,
    MemoryLinkCodes,
    MemoryWebhookGuard,
    RedisCommandLimiter,
    RedisLinkCodes,
    RedisWebhookGuard,
    WebhookGuard,
    callback,
    finished_requests,
    incoming,
)

log = logging.getLogger(__name__)

ADVISORY_LOCK_NAMESPACE = 0x7E
RESULT_MAX_AGE = timedelta(hours=3)
MAX_UPDATES_PER_TICK = 50
# Interactive lookups must not hold the scheduler: short timeout, one retry, no long 429 waits.
INTERACTIVE_TIMEOUT_S = 10.0
MAX_RETRY_AFTER_S = 5.0


def interactive_client_factory(box: SecretBox) -> ClientFactory:
    def make(connection: Connection) -> GitLabClient:
        return GitLabClient(
            connection.base_url,
            box.decrypt(connection.token_encrypted),
            timeout=INTERACTIVE_TIMEOUT_S,
            max_retries=1,
        )

    return make


def _fetch_updates(
    session: Session,
    client: TelegramClient,
    bot: TelegramBot,
    offset: int | None,
    guard: WebhookGuard,
) -> list[Any]:
    """getUpdates; a webhook left on the token by another system (HTTP 409) is removed.

    Telegram serves a token either by webhook or by getUpdates, never both. CodePulse only
    polls, so a webhook can only come from somewhere else; it is deleted (pending updates
    kept) with a warning and an audit row. The guard allows that once per bot per TTL: if
    the webhook comes back within it, the other system is re-registering it, and fighting
    over the token every tick would break both sides silently, so the 409 is raised instead
    and lands in `last_error` where the admin can see the conflict.
    """
    try:
        return client.get_updates(offset=offset, limit=MAX_UPDATES_PER_TICK)
    except TelegramError as exc:
        if exc.status_code != 409 or "webhook" not in str(exc).lower():
            raise
        if not guard.claim(bot.id):
            raise TelegramError(
                f"{exc} (webhook came back after CodePulse deleted it; "
                "another system shares this bot token)",
                409,
            ) from None
        log.warning(
            "telegram webhook found on a polled bot; deleting it so getUpdates works",
            extra={"bot_id": bot.id, "error": str(exc)},
        )
        client.delete_webhook()
        audit(session, None, "telegram.bot.webhook_deleted", f"telegram_bot:{bot.id}")
        session.commit()
        return client.get_updates(offset=offset, limit=MAX_UPDATES_PER_TICK)


def _apply_callback(
    client: TelegramClient, tap: Callback, outcome: CallbackOutcome, bot_id: int
) -> None:
    """Answer the tap first (the spinner), then rewrite the list; failures only get logged."""
    try:
        client.answer_callback(tap.callback_id, outcome.answer, outcome.alert)
    except TelegramError as exc:
        log.warning(
            "telegram answerCallbackQuery failed", extra={"bot_id": bot_id, "error": str(exc)}
        )
    if outcome.text is not None:
        try:
            client.edit_message(tap.chat_id, tap.message_id, outcome.text, outcome.markup)
        except TelegramError as exc:
            log.warning(
                "telegram editMessageText failed", extra={"bot_id": bot_id, "error": str(exc)}
            )
    if outcome.send:
        try:
            client.send_message(tap.chat_id, outcome.send)
        except TelegramError as exc:
            log.warning("telegram reply failed", extra={"bot_id": bot_id, "error": str(exc)})


def poll_bot(
    session: Session,
    bot: TelegramBot,
    client: TelegramClient,
    settings: Settings,
    queue: Enqueuer,
    make_client: ClientFactory,
    link_codes: LinkCodes,
    limiter: CommandLimiter,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    webhook_guard: WebhookGuard | None = None,
    resolver: Resolver | None = None,
    picker: PickerStore | None = None,
) -> int:
    """Handle new messages of one bot and post finished results; returns updates handled."""
    offset = (bot.last_update_id or 0) + 1 if bot.last_update_id is not None else None
    updates = _fetch_updates(session, client, bot, offset, webhook_guard or MemoryWebhookGuard())
    handled = 0
    for update in updates:
        update_id = int(update.get("update_id") or 0)
        message = incoming(update)
        tap = callback(update) if message is None else None
        reply: str | None = None
        # The command's effects and the acknowledged offset land in one transaction, so a
        # crash cannot replay the message (a lost reply is the cheaper failure).
        bot.last_update_id = max(bot.last_update_id or 0, update_id)
        if message is not None:
            service = BotService(
                session,
                bot,
                settings,
                queue,
                make_client,
                link_codes,
                limiter,
                clock,
                resolver=resolver,
                picker=picker,
            )
            try:
                reply = service.handle(message)
                session.commit()
            except Exception:
                # Still acknowledged, so one bad command cannot wedge the bot.
                session.rollback()
                bot.last_update_id = max(bot.last_update_id or 0, update_id)
                session.commit()
                log.exception(
                    "telegram command failed",
                    extra={"bot_id": bot.id, "update_id": update_id},
                )
                reply = "Có lỗi khi xử lý lệnh; admin đã được ghi log. Thử lại sau."
            if reply:
                try:
                    client.send_message(
                        message.chat_id,
                        reply,
                        message.message_id,
                        reply_markup=service.pending_markup,
                    )
                except TelegramError as exc:
                    log.warning(
                        "telegram reply failed", extra={"bot_id": bot.id, "error": str(exc)}
                    )
        elif tap is not None:
            service = BotService(
                session,
                bot,
                settings,
                queue,
                make_client,
                link_codes,
                limiter,
                clock,
                resolver=resolver,
                picker=picker,
            )
            try:
                outcome = service.handle_callback(tap)
                session.commit()
            except Exception:
                session.rollback()
                bot.last_update_id = max(bot.last_update_id or 0, update_id)
                session.commit()
                log.exception("telegram callback failed", extra={"bot_id": bot.id})
                outcome = CallbackOutcome(answer="Có lỗi khi xử lý; thử lại sau.", alert=True)
            _apply_callback(client, tap, outcome, bot.id)
        else:
            session.commit()
        handled += 1
    now = clock()
    for request, text in finished_requests(
        session, bot, settings.dashboard_url, now, RESULT_MAX_AGE
    ):
        try:
            client.send_message(request.chat_id, text, request.message_id)
        except TelegramError as exc:
            log.warning("telegram result failed", extra={"bot_id": bot.id, "error": str(exc)})
            expired = request.created_at is not None and now - request.created_at > RESULT_MAX_AGE
            if (exc.status_code is not None and 400 <= exc.status_code < 500) or expired:
                # Chat gone, bot kicked, or too old: stop trying, keep the reason.
                request.status, request.error = "failed", str(exc)[:300]
                session.commit()
            continue
        request.status, request.notified_at = "notified", now
        session.commit()
    bot.last_polled_at = clock()
    bot.last_error = None
    session.commit()
    return handled


def tick(
    session: Session,
    settings: Settings,
    box: SecretBox,
    queue: Enqueuer,
    make_client: ClientFactory,
    link_codes: LinkCodes,
    limiter: CommandLimiter,
    telegram_client: Callable[[str], TelegramClient],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    webhook_guard: WebhookGuard | None = None,
    resolver: Resolver | None = None,
    picker: PickerStore | None = None,
) -> dict[int, int]:
    """Poll every enabled bot once; {bot_id: updates handled}. Errors are per bot."""
    handled: dict[int, int] = {}
    bot_ids = list(
        session.scalars(select(TelegramBot.id).where(TelegramBot.enabled).order_by(TelegramBot.id))
    )
    bind = session.get_bind()
    engine: Engine = bind if isinstance(bind, Engine) else bind.engine
    for bot_id in bot_ids:
        # Session-level advisory locks belong to a connection; the ORM session returns its
        # connection to the pool on every commit, so the lock lives on a dedicated one.
        with engine.connect() as lock_conn:
            locked = lock_conn.execute(
                select(func.pg_try_advisory_lock(ADVISORY_LOCK_NAMESPACE, bot_id))
            ).scalar()
            if not locked:
                continue
            try:
                _poll_one(
                    session,
                    bot_id,
                    settings,
                    box,
                    queue,
                    make_client,
                    link_codes,
                    limiter,
                    telegram_client,
                    clock,
                    handled,
                    webhook_guard,
                    resolver,
                    picker,
                )
            finally:
                lock_conn.execute(select(func.pg_advisory_unlock(ADVISORY_LOCK_NAMESPACE, bot_id)))
    return handled


def _poll_one(
    session: Session,
    bot_id: int,
    settings: Settings,
    box: SecretBox,
    queue: Enqueuer,
    make_client: ClientFactory,
    link_codes: LinkCodes,
    limiter: CommandLimiter,
    telegram_client: Callable[[str], TelegramClient],
    clock: Callable[[], datetime],
    handled: dict[int, int],
    webhook_guard: WebhookGuard | None,
    resolver: Resolver | None = None,
    picker: PickerStore | None = None,
) -> None:
    """One bot's turn; any failure lands in `last_error` and never escapes to the other bots."""
    bot = session.get(TelegramBot, bot_id)
    if bot is None or not bot.enabled:
        return
    try:
        with telegram_client(box.decrypt(bot.token_encrypted)) as client:
            handled[bot_id] = poll_bot(
                session,
                bot,
                client,
                # Links in replies use the Admin screen's public URL, if one is set.
                with_overrides(session, settings),
                queue,
                make_client,
                link_codes,
                limiter,
                clock,
                webhook_guard,
                resolver=resolver,
                picker=picker,
            )
    except Exception as exc:
        session.rollback()
        bot = session.get(TelegramBot, bot_id)
        if bot is not None:
            # Class name plus a short message; never a URL (it would carry the token).
            bot.last_error = f"{exc.__class__.__name__}: {str(exc)[:300]}"
            bot.last_polled_at = clock()
            session.commit()
        log.exception("telegram bot poll failed", extra={"bot_id": bot_id})
    finally:
        session.commit()


def default_resolver(settings: Settings) -> Resolver:
    """The LLM picks among several repos/people that fit an abbreviation; no LLM = ask back."""
    try:
        return LLMResolver(build_provider(settings, timeout_s=20.0, max_retries=0))
    except Exception:  # provider not configured: the bot still works, it just asks
        log.warning("telegram resolver unavailable; ambiguous hints will be asked back")
        return NoResolver()


def telegram_tick_job() -> None:
    """Scheduler entry point."""
    settings = get_settings()
    redis: Redis = get_redis()
    with get_sessionmaker()() as session:
        if (
            session.scalar(select(func.count()).select_from(TelegramBot).where(TelegramBot.enabled))
            == 0
        ):
            return
        box = secret_box()
        tick(
            session,
            settings,
            box,
            Queue("default", connection=redis),
            interactive_client_factory(box),
            RedisLinkCodes(redis),
            RedisCommandLimiter(redis, settings.telegram_commands_per_10min),
            lambda token: TelegramClient(
                token, api_base=settings.telegram_api_base, max_retry_after=MAX_RETRY_AFTER_S
            ),
            webhook_guard=RedisWebhookGuard(redis),
            resolver=default_resolver(settings),
            picker=RedisPickerStore(redis),
        )


__all__ = ["AllowAll", "MemoryLinkCodes", "poll_bot", "telegram_tick_job", "tick"]
