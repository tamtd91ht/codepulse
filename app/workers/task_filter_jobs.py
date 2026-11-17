"""Digests of subscribed saved filters (docs/specs/31 §21.4): from 08:00 local time, once a day
(daily) or on Monday (weekly), the matching tasks to the filter's owner."""

import logging
from datetime import UTC, datetime

log = logging.getLogger(__name__)

# Each filter sends once a day at most (subscription.last_sent): a 15-minute tick is enough.
TICK_MIN = 15


def filter_digest_job() -> int:
    """Scheduler entry point: one transaction; one filter failing does not stop the others."""
    from app.db import get_sessionmaker
    from app.tasks.search import send_filter_digests

    try:
        with get_sessionmaker()() as session:
            sent = send_filter_digests(session, datetime.now(UTC))
            session.commit()
            return sent
    except Exception:
        log.exception("filter digests failed")
        return 0
