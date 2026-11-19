"""Long database transactions (user 2026-11-19), from two sides that share one memory:

- **Ended** (app/dbtiming.py): every transaction of the app is timed where it runs; one longer
  than N seconds is queued here (`report_slow_job`) with its flow, the line of app code that ended
  it and its slowest statement.
- **Still open** (`check`, the scheduler every minute): `pg_stat_activity` shows a transaction
  open longer than N (hung, a worker killed mid-way, a connection from outside the app), which the
  first side cannot see until it ends. Rings when it passes N, again ("vẫn chạy") past 10 x N.

N is LONG_TX_ALERT_S (Admin > Hệ thống; 0 = off). A transaction the minute watch rang for gets a
follow-up when it ends ("đã xong sau …, tại …") instead of a second alert; one flow at one place
rings at most every 30 minutes. Every ring is kept 24 hours for Admin > Hệ thống.
"""

import json
import logging
import re
import time
from collections import deque
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
# One flow (say every Jira sync) at one place rings at most this often.
FLOW_COOLDOWN_S = 30 * 60
HISTORY_KEY = "codepulse:dbwatch:history"
HISTORY_MAX = 300
HISTORY_S = 24 * 3600
MATCH_S = 5.0  # an ended transaction is the one the watch saw if they started this close
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


def flow_text(application_name: str | None) -> str:
    """The flow as app/dblabel.py named it ("worker · sync.sync_project_job #12"), or the client
    program of a connection from outside the app."""
    name = (application_name or "").strip()
    if name.startswith("cp-"):
        process, _, label = name[3:].partition(" ")
        return f"{process} · {label}" if label else process
    return name or "không rõ (kết nối ngoài ứng dụng)"


def family(flow: str) -> str:
    """A flow without its ids: every run of one job, every call of one route."""
    return _ID.sub(":id", flow)


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
        return flow_text(self.application_name)

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
        return family(self.flow)

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
    """(title, body) of the bell for a transaction still open."""
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


def ended_message(p: dict[str, Any], follow_up: bool) -> tuple[str, str]:
    """(title, body) of the bell for a transaction that ended (app/dbtiming.py payload)."""
    flow = flow_text(p.get("flow"))
    lead = "Transaction DB đã xong sau" if follow_up else "Transaction DB chạy lâu"
    title = f"{lead} {_duration(float(p.get('seconds') or 0))}: {flow}"
    parts = [f"kết thúc bằng {p.get('how') or 'commit'}"]
    if p.get("where"):
        parts.append(f"tại {p['where']}")
    parts.append(f"{p.get('statements') or 0} câu lệnh")
    if p.get("pid"):
        parts.append(f"pid {p['pid']}")
    body = " · ".join(parts)
    if p.get("slowest_sql"):
        body += f". Câu chậm nhất {_duration(float(p.get('slowest_s') or 0))}: {p['slowest_sql']}"
    return title, body


class Seen:
    """What already rang (a transaction at a level, a flow in its cooldown, the transactions the
    minute watch rang for) and the 24-hour history: Redis when there is one (several processes,
    restarts), else this process."""

    def __init__(self, redis: Any | None) -> None:
        self._redis = redis
        self._local: dict[str, tuple[float, str]] = {}
        self._history: deque[str] = deque(maxlen=HISTORY_MAX)

    def get(self, key: str, now: float) -> str | None:
        if self._redis is not None:
            try:
                value = self._redis.get(f"codepulse:dbwatch:{key}")
                return value.decode() if isinstance(value, bytes) else value
            except Exception:
                log.warning("dbwatch: redis unavailable, remembering in memory")
        found = self._local.get(key)
        return found[1] if found and found[0] > now else None

    def has(self, key: str, now: float) -> bool:
        return self.get(key, now) is not None

    def mark(self, key: str, ttl_s: int, now: float, value: str = "1") -> None:
        if self._redis is not None:
            try:
                self._redis.set(f"codepulse:dbwatch:{key}", value, ex=ttl_s)
                return
            except Exception:
                log.warning("dbwatch: redis unavailable, remembering in memory")
        self._local = {k: v for k, v in self._local.items() if v[0] > now}
        self._local[key] = (now + ttl_s, value)

    def claim(self, key: str, ttl_s: int, now: float) -> bool:
        """Mark `key` unless it is marked already (atomic in Redis): True for the one caller
        that got it, so two workers never ring for the same thing."""
        if self._redis is not None:
            try:
                return bool(self._redis.set(f"codepulse:dbwatch:{key}", 1, nx=True, ex=ttl_s))
            except Exception:
                log.warning("dbwatch: redis unavailable, remembering in memory")
        if self.has(key, now):
            return False
        self._local[key] = (now + ttl_s, "1")
        return True

    def forget(self, key: str) -> None:
        if self._redis is not None:
            try:
                self._redis.delete(f"codepulse:dbwatch:{key}")
            except Exception:
                log.warning("dbwatch: redis unavailable, key not cleared")
        self._local.pop(key, None)

    def record(self, entry: dict[str, Any]) -> None:
        raw = json.dumps(entry, default=str, ensure_ascii=False)
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.lpush(HISTORY_KEY, raw)
                pipe.ltrim(HISTORY_KEY, 0, HISTORY_MAX - 1)
                pipe.execute()
                return
            except Exception:
                log.warning("dbwatch: redis unavailable, history kept in memory")
        self._history.appendleft(raw)

    def history(self, now: float) -> list[dict[str, Any]]:
        raws: list[Any] = []
        if self._redis is not None:
            try:
                raws = list(self._redis.lrange(HISTORY_KEY, 0, HISTORY_MAX - 1))
            except Exception:
                raws = list(self._history)
        else:
            raws = list(self._history)
        out = []
        for raw in raws:
            try:
                entry = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if float(entry.get("at") or 0) >= now - HISTORY_S:
                out.append(entry)
        return out

    def publish_threshold(self, value: int) -> None:
        """For app/dbtiming.py in every process: the threshold an admin set on the web."""
        if self._redis is not None:
            from app.dbtiming import THRESHOLD_KEY

            try:
                self._redis.set(THRESHOLD_KEY, value, ex=300)
            except Exception:
                log.warning("dbwatch: threshold not published")


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
    """Ring the admins for transactions still open past the threshold, not rung yet."""
    from app.notifications.inbox import notify

    limit = threshold_s(session, settings)
    seen.publish_threshold(limit)
    if limit <= 0:
        session.commit()
        return 0
    found = long_transactions(session, limit, limit=MAX_PER_RUN)
    admins = admin_ids(session, settings) if found else []
    now = time.time()
    marks: list[tuple[str, int, str]] = []
    entries: list[dict[str, Any]] = []
    rung = 0
    for tx in found:
        escalated = tx.seconds >= limit * ESCALATE
        level = 2 if escalated else 1
        key = _key(tx, level)
        flow_key = f"flow:{level}:{tx.family}"
        if seen.has(key, now) or seen.has(flow_key, now):
            continue
        marks += [(key, SEEN_TTL_S, "1"), (flow_key, FLOW_COOLDOWN_S, "1")]
        # The ended side answers this one with a follow-up, not a second alert.
        marks.append((f"live:{tx.pid}", SEEN_TTL_S, str(tx.xact_start.timestamp())))
        if escalated:
            marks.append((_key(tx, 1), SEEN_TTL_S, "1"))  # past both: one ring, not two
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
        entries.append(
            {
                "at": now,
                "source": "open",
                "seconds": round(tx.seconds, 1),
                "flow": tx.flow,
                "where": None,
                "state": tx.state_text,
                "waiting": tx.waiting,
                "sql": tx.query,
                "pid": tx.pid,
                "title": title,
            }
        )
        rung += 1
    session.commit()
    for key, ttl, value in marks:  # only what reached the bell counts as rung
        seen.mark(key, ttl, now, value)
    for entry in entries:
        seen.record(entry)
    return rung


def report_slow(session: Session, settings: Settings, seen: Seen, p: dict[str, Any]) -> bool:
    """A transaction that ended past the threshold (app/dbtiming.py): ring, or follow up on the
    minute watch's ring for it. True when the bell rang."""

    limit = threshold_s(session, settings)
    if limit <= 0 or float(p.get("seconds") or 0) <= limit:
        return False  # turned off, or raised, since the process timed it
    now = time.time()
    pid = p.get("pid")
    live = seen.get(f"live:{pid}", now) if pid else None
    follow_up = False
    if live is not None:
        try:
            follow_up = abs(float(live) - float(p.get("started") or 0)) <= MATCH_S
        except ValueError:
            follow_up = False
    flow = flow_text(p.get("flow"))
    cool = f"ended:{family(flow)}:{p.get('where') or '?'}"
    if not follow_up and not seen.claim(cool, FLOW_COOLDOWN_S, now):
        return False
    title, body = ended_message(p, follow_up)
    try:
        _ring_ended(session, settings, p, title, body, follow_up)
    except BaseException:
        if not follow_up:
            seen.forget(cool)  # not rung: the next one may
        raise
    if follow_up:
        seen.forget(f"live:{pid}")
    seen.record(
        {
            "at": now,
            "source": "ended",
            "seconds": p.get("seconds"),
            "flow": flow,
            "where": p.get("where"),
            "state": f"kết thúc bằng {p.get('how') or 'commit'}",
            "waiting": None,
            "sql": p.get("slowest_sql"),
            "slowest_s": p.get("slowest_s"),
            "statements": p.get("statements"),
            "pid": pid,
            "title": title,
        }
    )
    return True


def _ring_ended(
    session: Session,
    settings: Settings,
    p: dict[str, Any],
    title: str,
    body: str,
    follow_up: bool,
) -> None:
    from app.notifications.inbox import notify

    pid = p.get("pid")
    notify(
        session,
        admin_ids(session, settings),
        kind=KIND,
        title=title,
        body=body,
        link="/admin#system",
        tone="info" if follow_up else "warn",
        ref=f"pid:{pid}" if pid else None,
    )
    session.commit()


_seen: Seen | None = None


def shared_seen() -> Seen:
    global _seen
    if _seen is None:
        try:
            from app.db import get_redis

            redis: Any | None = get_redis()
        except Exception:
            redis = None
        _seen = Seen(redis)
    return _seen


def watch_job() -> int:
    """Scheduler entry point (every minute): runs in the scheduler itself, so it still rings
    while every worker is busy."""
    from app import dblabel
    from app.config import get_settings
    from app.db import get_sessionmaker

    with dblabel.labelled("dbwatch"), get_sessionmaker()() as session:
        try:
            return check(session, get_settings(), shared_seen())
        except Exception:
            log.exception("dbwatch failed")
            return 0


def report_slow_job(payload: dict[str, Any]) -> bool:
    """RQ entry point, queued by app/dbtiming.py when a transaction ended past the threshold."""
    from app.config import get_settings
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        try:
            return report_slow(session, get_settings(), shared_seen(), payload)
        except Exception:
            log.exception("slow transaction report failed")
            return False
