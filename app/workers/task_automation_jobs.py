"""Task automation on a clock (docs/specs/31 §19.7): the scheduled rules (reminders: overdue, due
soon, stuck in review, code with open critical findings) and the held task notifications
(digests, quiet hours). Plus the Telegram post of a "highest" bug, queued after its commit."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import Task, TelegramBot
from app.tasks import automation

log = logging.getLogger(__name__)

# The scheduler's tick (app/scheduler): reminders are once a day each, digests once a day per
# person, quiet hours end on the hour; 15 minutes is precise enough and cheap.
TICK_MIN = 15


def task_automation_tick_job() -> dict[str, int]:
    """Scheduler entry point: one transaction for the rules, one for the digests (a failing
    one does not hold the other back)."""
    from app.db import get_sessionmaker

    now = datetime.now(UTC)
    result: dict[str, int] = {}
    try:
        with get_sessionmaker()() as session:
            result.update(automation.run_schedule(session, now))
            session.commit()
    except Exception:
        log.exception("task automation rules failed")
    try:
        with get_sessionmaker()() as session:
            result["digests"] = automation.send_digests(session, now)
            session.commit()
    except Exception:
        log.exception("task digests failed")
    try:  # §20.5: the daily summary at each person's time
        with get_sessionmaker()() as session:
            result["summaries"] = automation.send_summaries(session, now)
            session.commit()
    except Exception:
        log.exception("task summaries failed")
    return result


def telegram_note_job(note_ids: list[int]) -> int:
    """RQ entry point (§20.5): inbox notifications whose person chose Telegram, sent to the
    private chat of each linked Telegram account, with Mở / Đã xem / Ẩn 1 ngày. A bot can only
    write to someone who started it: the bots are tried in order (the task's dự án first)."""
    from app.config import get_settings
    from app.crypto import secret_box
    from app.db import get_sessionmaker
    from app.models import UserNotification
    from app.telegram.client import TelegramClient, TelegramError

    settings = get_settings()
    box = secret_box()
    sent = 0
    with get_sessionmaker()() as session:
        for note_id in note_ids:
            note = session.get(UserNotification, note_id)
            if note is None or note.read_at is not None:
                continue
            chats = automation.telegram_chats_of(session, note.user_id)
            if not chats:
                continue
            key = automation._task_key(note.link)
            bp_id = session.scalar(select(Task.business_project_id).where(Task.key == key))
            text, markup = automation.telegram_note(note, settings.dashboard_url)
            for chat_id in chats:
                for bot_id in automation.note_bots(session, bp_id):
                    bot = session.get(TelegramBot, bot_id)
                    if bot is None:
                        continue
                    try:
                        with TelegramClient(box.decrypt(bot.token_encrypted)) as client:
                            client.send_message(chat_id, text, reply_markup=markup)
                        sent += 1
                        break
                    except TelegramError:  # not started by the person, blocked: the next bot
                        log.info("task note not sent by bot", extra={"bot_id": bot_id})
                    except Exception:
                        log.exception("task note bot failed", extra={"bot_id": bot_id})
    return sent


def telegram_alert_job(task_id: int) -> int:
    """RQ entry point: post a new "highest" bug to the chats its dự án's bots name."""
    from app.config import get_settings
    from app.crypto import secret_box
    from app.db import get_sessionmaker
    from app.telegram.client import TelegramClient, TelegramError

    settings = get_settings()
    box = secret_box()
    sent = 0
    with get_sessionmaker()() as session:
        task = session.get(Task, task_id)
        if task is None:
            return 0
        text = automation.telegram_text(session, task, settings.dashboard_url)
        for bot_id, chats in automation._telegram_chats(session, task.business_project_id):
            bot = session.get(TelegramBot, bot_id)
            if bot is None:
                continue
            try:
                with TelegramClient(box.decrypt(bot.token_encrypted)) as client:
                    for chat_id in chats:
                        try:
                            client.send_message(chat_id, text)
                            sent += 1
                        except TelegramError:
                            log.warning("task alert not posted", extra={"chat_id": chat_id})
            except Exception:  # one bad bot (token, network) does not stop the others
                log.exception("task alert bot failed", extra={"bot_id": bot_id})
    return sent
