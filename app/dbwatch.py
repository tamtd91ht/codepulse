"""Long database transactions (user 2026-11-19): every minute the scheduler looks at
`pg_stat_activity` and rings every admin's bell for a transaction of this database open longer
than N seconds (Admin > Hệ thống, LONG_TX_ALERT_S; 0 = off), naming the flow that holds it
(`application_name`, app/dblabel.py), its state, what it waits on and its last statement.

Each transaction rings twice at most: when it passes N, and again ("vẫn chạy") past 10 x N.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings

log = logging.getLogger(__name__)

KIND = "system.long_transaction"
ESCALATE = 10  # the second ring, at this many times the threshold
MAX_PER_RUN = 10  # a pile-up rings for the oldest ones only
SEEN_TTL_S = 2 * 24 * 3600
# One flow (say every Jira sync) rings at most this often, whatever transaction of it.
FLOW_COOLDOWN_S = 30 * 60
_ID = re.compile(r"(?<=[/#])\d+")
QUERY_CHARS = 300

_LONG = text(
    """
    SELECT pid, backend_start, xact_start, state, wait_event_type, wait_event,
           application_name, host(client_addr) AS client,
           EXTRACT(EPOCH FROM clock_timestamp() - xact_start) AS seconds,
           left(query, :chars) AS query
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND backend_type = 'client backend'
      AND pid <> pg_backend_pid()
      AND xact_start IS NOT NULL
      AND clock_timestamp() - xact_start > make_interval(secs => :min_s)
    ORDER BY xact_start
    LIMIT :limit
    """
)

STATES = {
    "active": "đang chạy câu lệnh",
    "idle in transaction": "mở transaction nhưng đang chờ ứng dụng",
    "idle in transaction (aborted)": "transaction lỗi chưa rollback",
    "fastpath function call": "đang gọi hàm",
}


@dataclass
class LongTx:
    pid: int
    backend_start: datetime | None
    xact_start: datetime
    state: str | None
    wait_event_type: str | None
    wait_event: str | None
    application_name: str | None
    client: str | None
    seconds: float
    query: str | None

    @property
    def flow(self) -> str:
        """The flow as app/dblabel.py named it ("worker · sync.sync_project_job #12"), or the
        client program for a connection from outside the app."""
        name = (self.application_name or "").strip()
        if name.startswith("cp-"):
            process, _, label = name[3:].partition(" ")
            return f"{process} · {label}" if label else process
        return name or "không rõ (kết nối ngoài ứng dụng)"

    @property
    def state_text(self) -> str:
        return STATES.get(self.state or "", self.state or "không rõ")

    @property
    def waiting(self) -> str | None:
        if self.wait_event_type in (None, "Client", "Activity"):
            return None  # waiting for the app or idle: not a database wait
        return f"{self.wait_event_type}/{self.wait_event}"

    @property
    def family(self) -> str:
        """The flow without ids: every run of one job, every call of one route."""
        return _ID.sub(":id", self.flow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "xact_start": self.xact_start,
            "seconds": round(self.seconds, 1),
            "state": self.state,
            "state_text": self.state_text,
            "waiting": self.waiting,
            "flow": self.flow,
            "application_name": self.application_name,
            "client": self.client,
            "query": self.query,
        }


def long_transactions(session: Session, min_s: float, limit: int = 50) -> list[LongTx]:
    rows = session.execute(_LONG, {"chars": QUERY_CHARS, "min_s": min_s, "limit": limit})
    return [
        LongTx(
            pid=int(r.pid),
            backend_start=r.backend_start,
            xact_start=r.xact_start,
            state=r.state,
            wait_event_type=r.wait_event_type,
            wait_event=r.wait_event,
            application_name=r.application_name,
            client=r.client,
            seconds=float(r.seconds or 0),
            query=r.query,
        )
        for r in rows
    ]


def threshold_s(session: Session, settings: Settings) -> int:
    from app.admin_settings import effective

    return int(effective(session, settings)["long_tx_alert_s"])


def _duration(seconds: float) -> str:
    s = int(seconds)
    if s < 120:
        return f"{s}s"
    if s < 7200:
        return f"{s // 60} phút {s % 60}s"
    return f"{s // 3600} giờ {(s % 3600) // 60} phút"


def message(tx: LongTx, escalated: bool) -> tuple[str, str]:
    """(title, body) of the bell."""
    lead = "Transaction DB vẫn chạy" if escalated else "Transaction DB chạy lâu"
    title = f"{lead} {_duration(tx.seconds)}: {tx.flow}"
    parts = [f"pid {tx.pid}", tx.state_text]
    if tx.waiting:
        parts.append(f"chờ {tx.waiting}")
    if tx.client:
        parts.append(f"từ {tx.client}")
    started = tx.xact_start.astimezone(UTC).strftime("%H:%M:%S UTC")
    parts.append(f"bắt đầu {started}")
    body = " · ".join(parts)
    query = " ".join((tx.query or "").split())
    if query:
        body += f". Câu lệnh {'đang chạy' if tx.state == 'active' else 'cuối'}: {query}"
    return title, body


class Seen:
    """What already rang (a transaction at a level, a flow in its cooldown): Redis when there is
    one (the scheduler may restart), else this process. Marked once the ring is committed."""

    def __init__(self, redis: Any | None) -> None:
        self._redis = redis
        self._local: dict[str, float] = {}

    def has(self, key: str, now: float) -> bool:
        if self._redis is not None:
            try:
                return bool(self._redis.exists(f"codepulse:dbwatch:{key}"))
            except Exception:
                log.warning("dbwatch: redis unavailable, remembering in memory")
        until = self._local.get(key)
        return until is not None and until > now

    def mark(self, key: str, ttl_s: int, now: float) -> None:
        if self._redis is not None:
            try:
                self._redis.set(f"codepulse:dbwatch:{key}", 1, ex=ttl_s)
                return
            except Exception:
                log.warning("dbwatch: redis unavailable, remembering in memory")
        self._local = {k: v for k, v in self._local.items() if v > now}
        self._local[key] = now + ttl_s


def _key(tx: LongTx, level: int) -> str:
    started = int(tx.xact_start.timestamp() * 1_000_000)
    backend = int(tx.backend_start.timestamp()) if tx.backend_start else 0
    return f"{tx.pid}:{backend}:{started}:{level}"


def admin_ids(session: Session, settings: Settings) -> list[int]:
    from app.models import User

    emails = {e.lower() for e in settings.admin_emails}
    if not emails:
        return []
    return [
        uid
        for uid, email in session.execute(
            select(User.id, User.primary_email).where(User.active.is_not(False))
        ).tuples()
        if email and email.lower() in emails
    ]


def check(session: Session, settings: Settings, seen: Seen) -> int:
    """Ring the admins for transactions past the threshold not rung yet; the count rung."""
    from app.notifications.inbox import notify

    limit = threshold_s(session, settings)
    if limit <= 0:
        return 0
    found = long_transactions(session, limit, limit=MAX_PER_RUN)
    admins = admin_ids(session, settings) if found else []
    now = datetime.now(UTC).timestamp()
    marks: list[tuple[str, int]] = []
    rung = 0
    for tx in found:
        escalated = tx.seconds >= limit * ESCALATE
        level = 2 if escalated else 1
        key = _key(tx, level)
        flow_key = f"flow:{level}:{tx.family}"
        if seen.has(key, now) or seen.has(flow_key, now):
            continue
        marks += [(key, SEEN_TTL_S), (flow_key, FLOW_COOLDOWN_S)]
        if escalated:
            marks.append((_key(tx, 1), SEEN_TTL_S))  # past both: one ring, not two
        title, body = message(tx, escalated)
        log.warning(
            "long database transaction",
            extra={
                "pid": tx.pid,
                "seconds": round(tx.seconds, 1),
                "flow": tx.flow,
                "state": tx.state,
                "wait": tx.waiting,
                "query": (tx.query or "")[:200],
            },
        )
        notify(
            session,
            admins,
            kind=KIND,
            title=title,
            body=body,
            link="/admin#system",
            tone="bad" if escalated else "warn",
            ref=f"pid:{tx.pid}",
        )
        rung += 1
    session.commit()
    for key, ttl in marks:  # only what reached the bell counts as rung
        seen.mark(key, ttl, now)
    return rung


_seen: Seen | None = None


def watch_job() -> int:
    """Scheduler entry point (every minute): runs in the scheduler itself, so it still rings
    while every worker is busy."""
    global _seen
    from app import dblabel
    from app.config import get_settings
    from app.db import get_redis, get_sessionmaker

    if _seen is None:
        try:
            redis: Any | None = get_redis()
        except Exception:
            redis = None
        _seen = Seen(redis)
    with dblabel.labelled("dbwatch"), get_sessionmaker()() as session:
        try:
            return check(session, get_settings(), _seen)
        except Exception:
            log.exception("dbwatch failed")
            return 0
