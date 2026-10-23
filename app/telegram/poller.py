"""Scheduler job: poll every enabled bot, answer commands, post finished results.

Short polling (getUpdates with offset) every few seconds from the scheduler process; one
advisory lock per bot so two schedulers never answer the same message twice. A failing bot
records `last_error` and never blocks the others.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from redis import Redis
from rq import Queue
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.collector.sync import ClientFactory, Enqueuer
from app.config import Settings, get_settings
from app.crypto import SecretBox, secret_box
from app.db import get_redis, get_sessionmaker
from app.models import Connection, TelegramBot
from app.telegram.client import TelegramClient, TelegramError
from app.telegram.service import (
    AllowAll,
    BotService,
    CommandLimiter,
    LinkCodes,
    MemoryLinkCodes,
    RedisCommandLimiter,
    RedisLinkCodes,
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
) -> int:
    """Handle new messages of one bot and post finished results; returns updates handled."""
    offset = (bot.last_update_id or 0) + 1 if bot.last_update_id is not None else None
    updates = client.get_updates(offset=offset, limit=MAX_UPDATES_PER_TICK)
    handled = 0
    for update in updates:
        update_id = int(update.get("update_id") or 0)
        message = incoming(update)
        reply: str | None = None
        # The command's effects and the acknowledged offset land in one transaction, so a
        # crash cannot replay the message (a lost reply is the cheaper failure).
        bot.last_update_id = max(bot.last_update_id or 0, update_id)
        if message is not None:
            service = BotService(
                session, bot, settings, queue, make_client, link_codes, limiter, clock
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
                    client.send_message(message.chat_id, reply, message.message_id)
                except TelegramError as exc:
                    log.warning(
                        "telegram reply failed", extra={"bot_id": bot.id, "error": str(exc)}
                    )
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
) -> None:
    """One bot's turn; any failure lands in `last_error` and never escapes to the other bots."""
    bot = session.get(TelegramBot, bot_id)
    if bot is None or not bot.enabled:
        return
    try:
        with telegram_client(box.decrypt(bot.token_encrypted)) as client:
            handled[bot_id] = poll_bot(
                session, bot, client, settings, queue, make_client, link_codes, limiter, clock
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
        )


__all__ = ["AllowAll", "MemoryLinkCodes", "poll_bot", "telegram_tick_job", "tick"]
