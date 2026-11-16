"""Task automation on a clock (docs/specs/31 §19.7): the scheduled rules (reminders: overdue, due
soon, stuck in review, code with open critical findings) and the held task notifications
(digests, quiet hours). Plus the Telegram post of a "highest" bug, queued after its commit."""

import logging
from datetime import UTC, datetime

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
    return result


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
