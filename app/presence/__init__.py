"""Who is online, away or offline, and for how long (docs/specs/33; the approach, for any
project: docs/PRESENCE_PATTERN.md).

Presence belongs to a connection (one browser tab), not to a person. Each tab keeps a record in
the live store (Redis) that dies when it is not refreshed; a person's state is the best of their
living tabs: "online" (some tab in use), "away" (tabs open, none in use), "offline" (no tab).
The state is always worked out again from the tabs, never counted up and down, so two calls at
once agree and a lost "leave" leaves nothing behind.

History is `presence_segments`: one row per stretch in one state, written only when the state
changes (`transition`). Offline is the gap between rows.

Three entry points, all safe to repeat:
- `beat`: a tab is alive (the WebSocket of app/api/routers/presence.py refreshes it; a browser
  without one posts it);
- `leave`: a tab is gone;
- `sweep` (scheduler, every SWEEP_S): tabs that died without saying so, the checkpoint, and
  stretches left open by a lost store.
"""

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import PresenceSegment, User

log = logging.getLogger(__name__)

ONLINE = "online"
AWAY = "away"
OFFLINE = "offline"

# How often a living tab is refreshed (by the socket's server side, or by the browser's POST).
BEAT_S = 30
# A tab silent this long is dead. Not below 60 s + BEAT_S: browsers slow a background tab's
# timers to about one a minute, and a posting tab must survive that.
TAB_TTL_S = 100
# No mouse, key, scroll or touch for this long: the tab is not in use (the browser decides).
IDLE_S = 300
SWEEP_S = 15
CHECKPOINT_S = 60
KEEP_DAYS = 180
# Stretches closer than this count as one session (a page reload leaves a gap of a second).
SESSION_GAP_S = 10
# More tabs than anyone opens; the oldest go first.
MAX_TABS = 30
# The per-person lock of the live store: its own expiry, and how long a caller waits for it.
LOCK_S = 15
LOCK_WAIT_S = 5
# pg_advisory_xact_lock(LOCK, user_id): one transition per person at a time.
LOCK = 0x50524553  # "PRES"

Sessions = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class Conn:
    """One living connection. `id` is "<tab>.<suffix>": a tab that reconnects gets a new
    suffix, so the old socket's goodbye never removes the new one."""

    id: str
    t: float  # last refresh, unix seconds (server clock)
    active: bool
    device: str
    kind: str  # "desktop" | "mobile"
    # When the connection first appeared, and when `active` last changed (its first
    # appearance, when it never did): `_replay` dates a silent tab's death with them.
    born: float = 0.0
    changed: float = 0.0

    @property
    def tab(self) -> str:
        return self.id.split(".", 1)[0]


class Store(Protocol):
    """The live state. Redis in production (`RedisStore`), a dict in tests (`MemoryStore`)."""

    def conns(self, user_id: int) -> list[Conn]: ...
    def many(self, user_ids: Sequence[int]) -> dict[int, list[Conn]]: ...
    def put(self, user_id: int, conn: Conn) -> None: ...
    def drop(self, user_id: int, conn_ids: Iterable[str]) -> None: ...
    def forget(self, user_id: int) -> None: ...
    def live_users(self) -> list[int]: ...
    def state(self, user_id: int) -> str | None: ...
    def set_state(self, user_id: int, state: str | None) -> None: ...
    def due(self, name: str, every_s: int, now: float) -> bool: ...
    def lock(self, user_id: int) -> AbstractContextManager[Any]: ...


class MemoryStore:
    def __init__(self) -> None:
        self.tabs: dict[int, dict[str, Conn]] = {}
        self.states: dict[int, str] = {}
        self.ran: dict[str, float] = {}
        self._lock = threading.RLock()

    def lock(self, user_id: int) -> AbstractContextManager[Any]:
        return self._lock

    def conns(self, user_id: int) -> list[Conn]:
        return list(self.tabs.get(user_id, {}).values())

    def many(self, user_ids: Sequence[int]) -> dict[int, list[Conn]]:
        return {uid: self.conns(uid) for uid in user_ids}

    def put(self, user_id: int, conn: Conn) -> None:
        self.tabs.setdefault(user_id, {})[conn.id] = conn

    def drop(self, user_id: int, conn_ids: Iterable[str]) -> None:
        for conn_id in conn_ids:
            self.tabs.get(user_id, {}).pop(conn_id, None)

    def forget(self, user_id: int) -> None:
        self.tabs.pop(user_id, None)
        self.states.pop(user_id, None)

    def live_users(self) -> list[int]:
        return sorted(self.tabs)

    def state(self, user_id: int) -> str | None:
        return self.states.get(user_id)

    def set_state(self, user_id: int, state: str | None) -> None:
        if state is None:
            self.states.pop(user_id, None)
        else:
            self.states[user_id] = state

    def due(self, name: str, every_s: int, now: float) -> bool:
        if now - self.ran.get(name, float("-inf")) < every_s:
            return False
        self.ran[name] = now
        return True


def _encode(conn: Conn) -> str:
    return json.dumps(
        {
            "t": conn.t,
            "a": int(conn.active),
            "d": conn.device,
            "k": conn.kind,
            "b": conn.born,
            "c": conn.changed,
        }
    )


def _decode(conn_id: Any, raw: Any) -> Conn | None:
    try:
        data = json.loads(raw)
        name = conn_id.decode() if isinstance(conn_id, bytes) else str(conn_id)
        at = float(data["t"])
        return Conn(
            name,
            at,
            bool(data["a"]),
            str(data.get("d", "")),
            str(data.get("k", "")),
            float(data.get("b", at)),
            float(data.get("c", at)),
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


class RedisStore:
    """`cp:presence:tabs:{user}` (hash: connection -> record),
    `cp:presence:live` (sorted set: the people with a tab, by last refresh, so the sweeper never
    scans keys), `cp:presence:state` (hash: the state last written to the history)."""

    TABS = "cp:presence:tabs:"
    LIVE = "cp:presence:live"
    STATE = "cp:presence:state"
    DUE = "cp:presence:due:"
    LOCK = "cp:presence:lock:"

    def __init__(self, client: Any) -> None:
        self._r = client

    def conns(self, user_id: int) -> list[Conn]:
        raw = self._r.hgetall(f"{self.TABS}{user_id}")
        return [c for c in (_decode(k, v) for k, v in raw.items()) if c is not None]

    def many(self, user_ids: Sequence[int]) -> dict[int, list[Conn]]:
        if not user_ids:
            return {}
        pipe = self._r.pipeline(transaction=False)
        for uid in user_ids:
            pipe.hgetall(f"{self.TABS}{uid}")
        return {
            uid: [c for c in (_decode(k, v) for k, v in raw.items()) if c is not None]
            for uid, raw in zip(user_ids, pipe.execute(), strict=True)
        }

    def put(self, user_id: int, conn: Conn) -> None:
        key = f"{self.TABS}{user_id}"
        pipe = self._r.pipeline(transaction=False)
        pipe.hset(key, conn.id, _encode(conn))
        # The sweeper removes dead records (it needs their last refresh to date the departure);
        # the expiry only bounds what is left behind were it never to run.
        pipe.expire(key, 24 * 3600)
        pipe.zadd(self.LIVE, {str(user_id): conn.t})
        pipe.execute()

    def drop(self, user_id: int, conn_ids: Iterable[str]) -> None:
        ids = list(conn_ids)
        if ids:
            self._r.hdel(f"{self.TABS}{user_id}", *ids)

    def forget(self, user_id: int) -> None:
        pipe = self._r.pipeline(transaction=False)
        pipe.delete(f"{self.TABS}{user_id}")
        pipe.zrem(self.LIVE, str(user_id))
        pipe.hdel(self.STATE, str(user_id))
        pipe.execute()

    def live_users(self) -> list[int]:
        return sorted(int(m) for m in self._r.zrange(self.LIVE, 0, -1))

    def state(self, user_id: int) -> str | None:
        raw = self._r.hget(self.STATE, str(user_id))
        return raw.decode() if isinstance(raw, bytes) else raw

    def set_state(self, user_id: int, state: str | None) -> None:
        if state is None:
            self._r.hdel(self.STATE, str(user_id))
        else:
            self._r.hset(self.STATE, str(user_id), state)

    def due(self, name: str, every_s: int, now: float) -> bool:
        return bool(self._r.set(f"{self.DUE}{name}", "1", nx=True, ex=every_s))

    def lock(self, user_id: int) -> AbstractContextManager[Any]:
        """One read-change-write of a person's tabs at a time, across processes: a reloading
        tab's old socket closing must not undo the new one opening. Held for milliseconds; it
        frees itself after LOCK_S were its holder to die. Not acquired in LOCK_WAIT_S: LockError
        (a RedisError), and the caller's next refresh tries again."""
        lock: AbstractContextManager[Any] = self._r.lock(
            f"{self.LOCK}{user_id}", timeout=LOCK_S, blocking_timeout=LOCK_WAIT_S
        )
        return lock


_store: Store | None = None


def get_store() -> Store:
    """One per process; short timeouts, as every Redis client a request waits on."""
    global _store
    if _store is None:
        from redis import Redis

        from app.config import get_settings

        _store = RedisStore(
            Redis.from_url(
                str(get_settings().redis_url), socket_connect_timeout=2, socket_timeout=2
            )
        )
    return _store


def default_sessions() -> AbstractContextManager[Session]:
    from app.db import get_sessionmaker

    return get_sessionmaker()()  # type: ignore[no-any-return]


# -- the state of one person -----------------------------------------------------------------------


def state_of(conns: Iterable[Conn]) -> str:
    found = OFFLINE
    for conn in conns:
        if conn.active:
            return ONLINE
        found = AWAY
    return found


def alive(conns: Iterable[Conn], now: float) -> list[Conn]:
    return [c for c in conns if c.t > now - TAB_TTL_S]


def _at(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def transition(session: Session, user_id: int, state: str, at: datetime | None) -> bool:
    """The person is in `state` from `at`: closes the open stretch and opens the next one
    (none for "offline"). Nothing when they are in that state already, so it may be repeated.
    `at` None (only for "offline"): nobody knows when they left, so the stretch ends at its
    checkpoint."""
    session.execute(select(func.pg_advisory_xact_lock(LOCK, user_id)))
    seg = session.scalar(
        select(PresenceSegment).where(
            PresenceSegment.user_id == user_id, PresenceSegment.ended_at.is_(None)
        )
    )
    if seg is None and state == OFFLINE:
        return False
    if seg is not None and seg.state == state:
        return False
    if at is None:
        if state != OFFLINE:
            raise ValueError("only a departure may be dated by the checkpoint")
        at = seg.last_seen_at if seg is not None else datetime.now(UTC)
    start = at
    if seg is not None:
        start = max(at, seg.started_at)
        if start == seg.started_at:
            session.delete(seg)  # a stretch of no length says nothing
        else:
            seg.ended_at = start
            seg.last_seen_at = start
        session.flush()  # before the insert: one open stretch per person
    if state != OFFLINE:
        if seg is None:
            # Stretches of one person never overlap (the reads count on it): a change dated
            # back before their last stretch ended begins where that one ended.
            ended = session.scalar(
                select(PresenceSegment.ended_at)
                .where(PresenceSegment.user_id == user_id)
                .order_by(PresenceSegment.started_at.desc())
                .limit(1)
            )
            if ended is not None:
                start = max(start, ended)
        session.add(
            PresenceSegment(user_id=user_id, state=state, started_at=start, last_seen_at=start)
        )
        session.flush()
    return True


def _settle(
    sessions: Sessions,
    store: Store,
    user_id: int,
    now: float,
    add: Conn | None = None,
    gone: str | None = None,
) -> str:
    """Bring the person's history in line with their tabs, in two steps: first the tabs that
    died of silence (dated from their last refresh, not from now: a laptop closed at noon left
    at noon; `_replay` works out what the other tabs made of the time since), then the tab that
    came (`add`) or went (`gone`), at `now`.

    One at a time per person (`Store.lock`): it reads the tabs, writes the history, then writes
    the tabs, and a reloading tab's old socket leaving while its new one arrives would
    otherwise remove the newcomer."""
    with store.lock(user_id):
        return _settle_locked(sessions, store, user_id, now, add, gone)


def _replay(dead: Sequence[Conn], living: Sequence[Conn], now: float) -> list[tuple[str, float]]:
    """What the person's state was from the first of the `dead` tabs' last refreshes until
    `now`, had each dead tab gone when it last spoke: [(state, since), ...].

    While a silent tab was still counted (up to TAB_TTL_S), the other tabs' comings and changes
    were judged with it in the room: someone whose phone fell silent at 12:00 and who stopped
    using their desktop at 12:01 was "online" until the phone's record expired. Dating the
    change at the phone's last word (12:00) would take a minute of online from them; dating
    it at the sweep would give them one. Each tab remembers when it appeared and when its
    `active` last changed, which is enough to tell: online until 12:01, away from then.
    (A tab that changed twice in the window is read as if it changed once: the earlier change
    is not kept.)"""

    def flag(conn: Conn, at: float) -> bool | None:
        if at < conn.born:
            return None  # not there yet
        return conn.active if at >= conn.changed else not conn.active

    def state_at(at: float) -> str:
        there = [flag(c, at) for c in living] + [flag(c, at) for c in dead if at < c.t]
        flags = [f for f in there if f is not None]
        return ONLINE if any(flags) else AWAY if flags else OFFLINE

    start = min(c.t for c in dead)
    moments = {c.t for c in dead}
    moments |= {m for c in (*living, *dead) for m in (c.born, c.changed) if start < m <= now}
    out: list[tuple[str, float]] = []
    for at in sorted(moments):
        state = state_at(at)
        if not out or out[-1][0] != state:
            out.append((state, at))
    return out


def _settle_locked(
    sessions: Sessions,
    store: Store,
    user_id: int,
    now: float,
    add: Conn | None,
    gone: str | None,
) -> str:
    held = store.conns(user_id)
    living = alive(held, now)
    dead = [c for c in held if c not in living]
    steps: list[tuple[str, float | None]] = []
    if dead:
        steps.extend(_replay(dead, living, now))
    elif not held and add is None:
        # Recorded as present, yet the store holds no tab of theirs (its records were lost):
        # when they left is unknown, the checkpoint is the best guess.
        steps.append((OFFLINE, None))
    if add is not None:
        before = next((c for c in living if c.id == add.id), None)
        if before is not None:  # a refresh: it was born then, and changed then unless it does now
            changed = before.changed if before.active == add.active else now
            add = replace(add, born=before.born, changed=changed)
        living = [c for c in living if c.id != add.id] + [add]
    if gone is not None:
        living = [c for c in living if c.id != gone]
    extra = sorted(living, key=lambda c: c.t)[:-MAX_TABS] if len(living) > MAX_TABS else []
    living = [c for c in living if c not in extra]
    final = state_of(living)
    steps.append((final, now))

    recorded = store.state(user_id) or OFFLINE
    changes: list[tuple[str, float | None]] = []
    for state, ts in steps:
        if state != recorded:
            changes.append((state, ts))
            recorded = state
    if changes:
        with sessions() as session:
            for state, ts in changes:
                transition(session, user_id, state, None if ts is None else _at(ts))
            session.commit()

    # The store after the history: a failure above leaves the old state, and the next call
    # tries again.
    drop = {c.id for c in dead} | {c.id for c in extra} | ({gone} if gone is not None else set())
    if add is not None:
        store.put(user_id, add)
        drop.discard(add.id)
    if living:
        store.drop(user_id, sorted(drop))
        if changes:
            store.set_state(user_id, final)
    else:
        store.forget(user_id)
    return final


def beat(
    sessions: Sessions,
    store: Store,
    user_id: int,
    conn_id: str,
    *,
    active: bool,
    device: str,
    kind: str,
    now: float | None = None,
) -> str:
    ts = time.time() if now is None else now
    conn = Conn(conn_id, ts, active, device, kind, born=ts, changed=ts)
    return _settle(sessions, store, user_id, ts, add=conn)


def leave(
    sessions: Sessions, store: Store, user_id: int, conn_id: str, now: float | None = None
) -> str:
    return _settle(sessions, store, user_id, time.time() if now is None else now, gone=conn_id)


def sweep(sessions: Sessions, store: Store, now: float | None = None) -> dict[str, int]:
    """Scheduler tick. Tabs that died without a goodbye; once a minute the checkpoint of the
    open stretches, and the stretches whose person the store no longer knows (a flushed Redis,
    a killed API) closed at their checkpoint."""
    ts = time.time() if now is None else now
    result = {"live": 0, "left": 0, "orphans": 0}
    live: list[int] = []
    for user_id in store.live_users():
        try:
            state = _settle(sessions, store, user_id, ts)
        except IntegrityError:
            # The person was deleted while a tab of theirs was open: nothing left to record.
            store.forget(user_id)
            result["left"] += 1
            continue
        except SQLAlchemyError:
            # One person's failure must not stop everyone else's sweep; they come again next tick.
            log.exception("presence sweep failed for one person", extra={"user_id": user_id})
            live.append(user_id)
            continue
        if state == OFFLINE:
            result["left"] += 1
        else:
            live.append(user_id)
    result["live"] = len(live)
    if not store.due("checkpoint", CHECKPOINT_S, ts):
        return result
    moment = _at(ts)
    with sessions() as session:
        kept: set[int] = set()
        if live:
            kept = set(
                session.scalars(
                    update(PresenceSegment)
                    .where(PresenceSegment.ended_at.is_(None), PresenceSegment.user_id.in_(live))
                    .values(last_seen_at=moment)
                    .returning(PresenceSegment.user_id)
                )
            )
        orphans = session.scalars(
            select(PresenceSegment).where(
                PresenceSegment.ended_at.is_(None),
                PresenceSegment.user_id.not_in(live),
                PresenceSegment.last_seen_at < moment - timedelta(seconds=TAB_TTL_S),
            )
        ).all()
        for seg in orphans:
            if seg.last_seen_at <= seg.started_at:
                session.delete(seg)
            else:
                seg.ended_at = seg.last_seen_at
        result["orphans"] = len(orphans)
        session.commit()
    # A living person with no open stretch (it was closed under them): forget the recorded
    # state, so their next refresh writes it again.
    for user_id in live:
        if user_id not in kept:
            store.set_state(user_id, None)
    return result


def cleanup(session: Session, now: datetime | None = None) -> int:
    """Stretches that ended more than KEEP_DAYS ago (daily, app/retention.py)."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=KEEP_DAYS)
    result = session.execute(delete(PresenceSegment).where(PresenceSegment.ended_at < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)


_paused_until = 0.0


def sweep_job() -> dict[str, int]:
    """Scheduler entry point. A Redis that is down is said once a minute, not every tick."""
    global _paused_until
    from redis.exceptions import RedisError

    if time.monotonic() < _paused_until:
        return {}
    try:
        return sweep(default_sessions, get_store())
    except RedisError:
        _paused_until = time.monotonic() + 60
        log.warning("presence sweep skipped: Redis does not answer", exc_info=True)
        return {}


# -- durations -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stretch:
    state: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Totals:
    online_s: int = 0
    away_s: int = 0
    offline_s: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    sessions: int = 0


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """A calendar day of the business time zone, as UTC instants."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    return start.astimezone(UTC), datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(UTC)


def clip(
    segments: Iterable[PresenceSegment], start: datetime, end: datetime, now: datetime
) -> list[Stretch]:
    """The parts of `segments` inside [start, end), the open one running to `now`."""
    upto = min(end, now)
    out: list[Stretch] = []
    for seg in segments:
        a = max(seg.started_at, start)
        b = min(seg.ended_at or now, upto)
        if b > a:
            out.append(Stretch(seg.state, a, b))
    return sorted(out, key=lambda s: s.start)


def totals(stretches: Sequence[Stretch]) -> Totals:
    """Offline = the gaps between the first and the last moment seen in the window (spec 33 §6):
    the hours before someone starts and after they stop are not "offline time"."""
    if not stretches:
        return Totals()
    seconds = {ONLINE: 0.0, AWAY: 0.0}
    sessions = 0
    previous: datetime | None = None
    for s in stretches:
        seconds[s.state] = seconds.get(s.state, 0.0) + (s.end - s.start).total_seconds()
        # A page reload closes one socket and opens the next a moment later: one session.
        if previous is None or (s.start - previous).total_seconds() > SESSION_GAP_S:
            sessions += 1
        previous = s.end if previous is None else max(previous, s.end)
    first, last = stretches[0].start, max(s.end for s in stretches)
    span = (last - first).total_seconds()
    return Totals(
        online_s=round(seconds[ONLINE]),
        away_s=round(seconds[AWAY]),
        offline_s=max(0, round(span - seconds[ONLINE] - seconds[AWAY])),
        first_seen=first,
        last_seen=last,
        sessions=sessions,
    )


def segments_between(
    session: Session, user_ids: Sequence[int], start: datetime, end: datetime
) -> dict[int, list[PresenceSegment]]:
    """The stretches touching [start, end), per person. Two index range reads on
    (user_id, started_at), never the person's whole history (the monitor polls this): those
    that began inside the window, and the one that began last before it, when it runs into it
    (a person's stretches never overlap, so no earlier one can)."""
    if not user_ids:
        return {}
    inside = select(PresenceSegment).where(
        PresenceSegment.user_id.in_(user_ids),
        PresenceSegment.started_at >= start,
        PresenceSegment.started_at < end,
    )
    before_id = (
        select(PresenceSegment.id)
        .where(PresenceSegment.user_id == User.id, PresenceSegment.started_at < start)
        .order_by(PresenceSegment.started_at.desc())
        .limit(1)
        .correlate(User)
        .scalar_subquery()
    )
    running_in = select(PresenceSegment).where(
        PresenceSegment.id.in_(select(before_id).where(User.id.in_(user_ids))),
        (PresenceSegment.ended_at.is_(None)) | (PresenceSegment.ended_at > start),
    )
    found = [*session.scalars(running_in), *session.scalars(inside)]
    out: dict[int, list[PresenceSegment]] = {}
    for seg in sorted(found, key=lambda s: s.started_at):
        out.setdefault(seg.user_id, []).append(seg)
    return out


def open_stretches(session: Session, user_ids: Sequence[int]) -> dict[int, tuple[str, datetime]]:
    """The stretch each person is in now, as the history has it: (state, since)."""
    rows = session.execute(
        select(PresenceSegment.user_id, PresenceSegment.state, PresenceSegment.started_at).where(
            PresenceSegment.ended_at.is_(None), PresenceSegment.user_id.in_(user_ids)
        )
    )
    return {int(uid): (str(state), since) for uid, state, since in rows}


def last_seen(session: Session, user_ids: Sequence[int]) -> dict[int, datetime]:
    """When each person's last finished stretch ended (within KEEP_DAYS): one index probe per
    person, asked only for the people who are offline."""
    if not user_ids:
        return {}
    latest = (
        select(PresenceSegment.ended_at)
        .where(PresenceSegment.user_id == User.id, PresenceSegment.ended_at.is_not(None))
        .order_by(PresenceSegment.started_at.desc())
        .limit(1)
        .correlate(User)
        .scalar_subquery()
    )
    rows = session.execute(select(User.id, latest).where(User.id.in_(user_ids)))
    return {int(uid): at for uid, at in rows if at is not None}


def kind_of(user_agent: str | None) -> str:
    agent = (user_agent or "").lower()
    return "mobile" if any(w in agent for w in ("mobi", "android", "iphone", "ipad")) else "desktop"
