"""docs/specs/33: presence per tab folded into one state per person, the history written only
when the state changes, the sweeper, the durations, who sees whom, and the socket."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import jwt
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app import presence
from app.api.routers import presence as presence_api
from app.api.security import ALGORITHM, SESSION_COOKIE, issue_token
from app.config import Settings
from app.models import AuditLog, PresenceSegment, TeamMember, User
from app.retention import purge
from tests.api.conftest import World

pytestmark = pytest.mark.integration

API = "/api/v1"
T0 = datetime(2026, 11, 23, 2, 0, tzinfo=UTC).timestamp()  # 09:00 in Asia/Ho_Chi_Minh
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TAB_A, TAB_B, DEVICE, PHONE = "tab-aaaaaaaa", "tab-bbbbbbbb", "dev-11111111", "dev-22222222"


@pytest.fixture
def store() -> presence.MemoryStore:
    return presence.MemoryStore()


@pytest.fixture
def sessions(db_session: Session) -> presence.Sessions:
    @contextmanager
    def factory() -> Iterator[Session]:
        yield db_session

    return factory


@pytest.fixture(autouse=True)
def _wired(
    monkeypatch: pytest.MonkeyPatch,
    store: presence.MemoryStore,
    sessions: presence.Sessions,
    settings: Settings,
) -> None:
    monkeypatch.setattr(presence_api, "sessions", sessions)
    monkeypatch.setattr(presence_api, "store_of", lambda: store)
    monkeypatch.setattr(presence_api, "settings_of", lambda: settings)


def rows(db: Session, user: User) -> list[tuple[str, float, float | None]]:
    """(state, seconds after T0 it began, seconds after T0 it ended)."""
    db.expire_all()
    found = db.scalars(
        select(PresenceSegment)
        .where(PresenceSegment.user_id == user.id)
        .order_by(PresenceSegment.started_at)
    )
    return [
        (
            s.state,
            s.started_at.timestamp() - T0,
            None if s.ended_at is None else s.ended_at.timestamp() - T0,
        )
        for s in found
    ]


def beat(
    sessions: presence.Sessions,
    store: presence.Store,
    user: User,
    conn: str,
    at: float,
    active: bool = True,
    device: str = DEVICE,
) -> str:
    return presence.beat(
        sessions, store, user.id, conn, active=active, device=device, kind="desktop", now=T0 + at
    )


# -- many tabs, one state --------------------------------------------------------------------------


def test_two_tabs_one_leaves_the_person_stays_online(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    assert beat(sessions, store, world.an, "a.1", 0) == "online"
    assert beat(sessions, store, world.an, "b.1", 10, device=PHONE) == "online"
    assert presence.leave(sessions, store, world.an.id, "a.1", T0 + 20) == "online"
    assert rows(db_session, world.an) == [("online", 0, None)]
    assert presence.leave(sessions, store, world.an.id, "b.1", T0 + 50) == "offline"
    assert rows(db_session, world.an) == [("online", 0, 50)]
    assert store.live_users() == [] and store.state(world.an.id) is None


def test_away_when_no_tab_is_in_use_and_back(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, "a.1", 0)
    beat(sessions, store, world.an, "b.1", 0, active=False)
    assert beat(sessions, store, world.an, "a.1", 30, active=False) == "away"
    assert beat(sessions, store, world.an, "b.1", 60, active=True) == "online"
    assert rows(db_session, world.an) == [
        ("online", 0, 30),
        ("away", 30, 60),
        ("online", 60, None),
    ]


def test_a_refresh_of_the_same_state_writes_nothing(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, "a.1", 0)

    def boom() -> Any:
        raise AssertionError("no session for an unchanged state")

    for at in (30, 60, 90):
        assert beat(boom, store, world.an, "a.1", at) == "online"
    assert rows(db_session, world.an) == [("online", 0, None)]


def test_a_reconnecting_tab_is_not_removed_by_its_old_socket(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, f"{TAB_A}.old", 0)
    beat(sessions, store, world.an, f"{TAB_A}.new", 5)
    assert presence.leave(sessions, store, world.an.id, f"{TAB_A}.old", T0 + 6) == "online"
    assert {c.tab for c in store.conns(world.an.id)} == {TAB_A}
    assert rows(db_session, world.an) == [("online", 0, None)]


def test_transition_is_repeatable_and_one_stretch_is_open(
    world: World, db_session: Session
) -> None:
    at = datetime.fromtimestamp(T0, UTC)
    assert presence.transition(db_session, world.an.id, "online", at) is True
    assert (
        presence.transition(db_session, world.an.id, "online", at + timedelta(seconds=9)) is False
    )
    assert presence.transition(db_session, world.an.id, "offline", at + timedelta(seconds=20))
    assert (
        presence.transition(db_session, world.an.id, "offline", at + timedelta(seconds=30)) is False
    )
    assert rows(db_session, world.an) == [("online", 0, 20)]
    # A change dated before the stretch began leaves no stretch of no length behind.
    presence.transition(db_session, world.an.id, "away", at + timedelta(seconds=40))
    presence.transition(db_session, world.an.id, "online", at + timedelta(seconds=35))
    assert rows(db_session, world.an) == [("online", 0, 20), ("online", 40, None)]


def test_a_change_dated_back_never_overlaps_the_stretch_before(
    world: World, db_session: Session
) -> None:
    at = datetime.fromtimestamp(T0, UTC)
    presence.transition(db_session, world.an.id, "online", at)
    presence.transition(db_session, world.an.id, "offline", at + timedelta(seconds=60))
    presence.transition(db_session, world.an.id, "away", at + timedelta(seconds=20))
    assert rows(db_session, world.an) == [("online", 0, 60), ("away", 60, None)]


# -- the sweeper -----------------------------------------------------------------------------------


def test_one_persons_failure_does_not_stop_the_sweep(
    world: World,
    db_session: Session,
    sessions: presence.Sessions,
    store: presence.MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beat(sessions, store, world.an, "a.1", 0)
    beat(sessions, store, world.binh, "b.1", 0)
    beat(sessions, store, world.lead, "c.1", 0)
    settle = presence._settle

    def flaky(sess: Any, st: Any, user_id: int, now: float, *a: Any, **k: Any) -> str:
        if user_id == world.an.id:
            raise IntegrityError("insert", {}, Exception("the person was deleted"))
        if user_id == world.binh.id:
            raise OperationalError("select", {}, Exception("connection lost"))
        return settle(sess, st, user_id, now, *a, **k)

    monkeypatch.setattr(presence, "_settle", flaky)
    found = presence.sweep(sessions, store, T0 + 500)
    # An is forgotten, Bình is kept for the next tick, the lead is swept as usual.
    assert found["left"] == 2 and found["live"] == 1
    assert store.live_users() == [world.binh.id]
    assert rows(db_session, world.lead) == []


def test_a_silent_tab_left_when_it_last_spoke_not_when_swept(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, "a.1", 0)
    beat(sessions, store, world.an, "a.1", 30)
    assert presence.sweep(sessions, store, T0 + 100)["left"] == 0  # 70 s of silence: alive
    found = presence.sweep(sessions, store, T0 + 30 + presence.TAB_TTL_S + 40)
    assert found["left"] == 1 and found["live"] == 0
    assert rows(db_session, world.an) == [("online", 0, 30)]
    assert store.live_users() == []


def test_the_tab_in_use_dies_the_idle_one_makes_it_away_from_then(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, "a.1", 0)
    beat(sessions, store, world.an, "a.1", 30)  # the last word of the tab in use
    for at in (0, 60, 120, 150):
        beat(sessions, store, world.an, "b.1", at, active=False)
    assert rows(db_session, world.an) == [("online", 0, 30), ("away", 30, None)]
    assert [c.id for c in store.conns(world.an.id)] == ["b.1"]
    beat(sessions, store, world.an, "a.2", 200)
    assert rows(db_session, world.an)[-1] == ("online", 200, None)


def test_a_silent_tab_does_not_take_the_time_the_other_tab_was_in_use(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    """The phone falls silent at 30; the desktop is used until 120. The phone's record still
    counted then, so nothing was written; when it expires, "away" dates from 120, not 30."""
    beat(sessions, store, world.an, "phone.1", 0, device=PHONE)
    beat(sessions, store, world.an, "phone.1", 30, device=PHONE)
    for at in (0, 30, 60, 90):
        beat(sessions, store, world.an, "desk.1", at)
    beat(sessions, store, world.an, "desk.1", 120, active=False)
    assert rows(db_session, world.an) == [("online", 0, None)]
    presence.sweep(sessions, store, T0 + 140)
    assert rows(db_session, world.an) == [("online", 0, 120), ("away", 120, None)]
    assert [c.id for c in store.conns(world.an.id)] == ["desk.1"]


def test_every_change_of_the_other_tab_is_replayed_not_only_the_last(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    """The phone falls silent at 30 and is counted until 130; meanwhile the desktop goes idle
    at 60, is used again at 100 and left at 120. None of it was written then."""
    beat(sessions, store, world.an, "phone.1", 0, device=PHONE)
    beat(sessions, store, world.an, "phone.1", 30, device=PHONE)
    beat(sessions, store, world.an, "desk.1", 0)
    beat(sessions, store, world.an, "desk.1", 60, active=False)
    beat(sessions, store, world.an, "desk.1", 90, active=False)
    beat(sessions, store, world.an, "desk.1", 100)
    beat(sessions, store, world.an, "desk.1", 120, active=False)
    assert rows(db_session, world.an) == [("online", 0, None)]
    (desk,) = [c for c in store.conns(world.an.id) if c.id == "desk.1"]
    assert (desk.born, desk.flips) == (T0, (T0 + 60, T0 + 100, T0 + 120))
    presence.sweep(sessions, store, T0 + 140)
    assert rows(db_session, world.an) == [
        ("online", 0, 60),
        ("away", 60, 100),
        ("online", 100, 120),
        ("away", 120, None),
    ]
    # Changes older than FLIPS_KEEP_S are dropped at the next one (the tab kept refreshing).
    for at in range(150, 130 + presence.FLIPS_KEEP_S, 60):
        beat(sessions, store, world.an, "desk.1", at, active=False)
    beat(sessions, store, world.an, "desk.1", 130 + presence.FLIPS_KEEP_S)
    (desk,) = store.conns(world.an.id)
    assert desk.flips == (T0 + 130 + presence.FLIPS_KEEP_S,)


def test_a_silent_tab_then_nobody_then_an_idle_tab_is_a_gap(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    """The only tab falls silent at 30; an idle tab appears at 80. Offline in between."""
    beat(sessions, store, world.an, "phone.1", 0, device=PHONE)
    beat(sessions, store, world.an, "phone.1", 30, device=PHONE)
    beat(sessions, store, world.an, "desk.http", 80, active=False)
    beat(sessions, store, world.an, "desk.http", 110, active=False)
    assert rows(db_session, world.an) == [("online", 0, None)]
    presence.sweep(sessions, store, T0 + 140)
    assert rows(db_session, world.an) == [("online", 0, 30), ("away", 80, None)]


def test_a_silent_idle_tab_changes_nothing_while_another_is_in_use(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, "phone.1", 0, active=False, device=PHONE)
    for at in (0, 60, 120):
        beat(sessions, store, world.an, "desk.1", at)
    presence.sweep(sessions, store, T0 + 140)
    assert rows(db_session, world.an) == [("online", 0, None)]
    # Old records (written before `born` / `flips` existed) read as born at their refresh.
    old = presence._decode("x.1", '{"t": 5.0, "a": 1, "d": "d", "k": "desktop"}')
    assert old is not None and (old.born, old.flips) == (5.0, ())
    one = presence._decode(
        "x.1", '{"t": 9.0, "a": 0, "d": "d", "k": "desktop", "b": 1.0, "c": 7.0}'
    )
    assert one is not None and one.flips == (7.0,)  # the first day's format: one change
    again = presence._decode("x.1", presence._encode(old))
    assert again == old


def test_a_late_refresh_after_the_ttl_is_a_gap(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, "a.http", 0)
    beat(sessions, store, world.an, "a.http", 30)
    assert beat(sessions, store, world.an, "a.http", 300) == "online"
    assert rows(db_session, world.an) == [("online", 0, 30), ("online", 300, None)]
    assert [c.id for c in store.conns(world.an.id)] == ["a.http"]


def test_checkpoint_and_stretches_orphaned_by_a_lost_store(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, "a.1", 0)
    beat(sessions, store, world.binh, "b.1", 0)
    beat(sessions, store, world.an, "a.1", 60)
    beat(sessions, store, world.binh, "b.1", 60)
    presence.sweep(sessions, store, T0 + 70)  # checkpoint at 70
    store.tabs.clear()  # Redis flushed
    store.states.clear()
    beat(sessions, store, world.binh, "b.2", 130)  # Bình's browser reconnects, An's does not
    found = presence.sweep(sessions, store, T0 + 70 + presence.TAB_TTL_S + 30)
    assert found["orphans"] == 1
    assert rows(db_session, world.an) == [("online", 0, 70)]
    assert rows(db_session, world.binh) == [("online", 0, None)]


def test_recorded_present_but_no_tab_known_ends_at_the_checkpoint(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, "a.1", 0)
    presence.sweep(sessions, store, T0 + 40)  # checkpoint at 40
    store.tabs[world.an.id].clear()  # the tab records are gone, the recorded state is not
    assert presence.leave(sessions, store, world.an.id, "a.1", T0 + 500) == "offline"
    assert rows(db_session, world.an) == [("online", 0, 40)]


def test_a_living_person_without_an_open_stretch_gets_one_again(
    world: World, db_session: Session, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    beat(sessions, store, world.an, "a.1", 0)
    presence.transition(db_session, world.an.id, "offline", datetime.fromtimestamp(T0 + 10, UTC))
    beat(sessions, store, world.an, "a.1", 30)
    assert rows(db_session, world.an) == [("online", 0, 10)]  # the store still says "online"
    presence.sweep(sessions, store, T0 + 40)
    beat(sessions, store, world.an, "a.1", 60)
    assert rows(db_session, world.an) == [("online", 0, 10), ("online", 60, None)]


def test_more_tabs_than_the_cap_drops_the_oldest(
    world: World, sessions: presence.Sessions, store: presence.MemoryStore
) -> None:
    for i in range(presence.MAX_TABS + 3):
        beat(sessions, store, world.an, f"tab{i:02d}.1", i)
    held = sorted(c.id for c in store.conns(world.an.id))
    assert len(held) == presence.MAX_TABS and held[0] == "tab03.1"


def test_retention_deletes_old_stretches(world: World, db_session: Session) -> None:
    now = datetime(2026, 11, 23, tzinfo=UTC)
    old = now - timedelta(days=presence.KEEP_DAYS + 1)
    db_session.add_all(
        [
            PresenceSegment(
                user_id=world.an.id,
                state="online",
                started_at=old - timedelta(hours=1),
                ended_at=old,
                last_seen_at=old,
            ),
            PresenceSegment(
                user_id=world.an.id,
                state="online",
                started_at=now - timedelta(hours=1),
                ended_at=None,
                last_seen_at=now,
            ),
        ]
    )
    db_session.flush()
    assert purge(db_session, 365, now).presence_segments_deleted == 1
    assert len(rows(db_session, world.an)) == 1


# -- durations -------------------------------------------------------------------------------------


def seg(state: str, start: datetime, end: datetime | None) -> PresenceSegment:
    return PresenceSegment(
        user_id=1, state=state, started_at=start, ended_at=end, last_seen_at=end or start
    )


def test_totals_offline_is_the_gaps_between_first_and_last_seen() -> None:
    day = date(2026, 11, 23)
    start, end = presence.day_bounds(day, TZ)
    assert start == datetime(2026, 11, 22, 17, tzinfo=UTC)
    nine = start + timedelta(hours=9)
    now = start + timedelta(hours=18)
    found = presence.totals(
        presence.clip(
            [
                seg("online", nine, nine + timedelta(hours=2)),
                seg("away", nine + timedelta(hours=2), nine + timedelta(hours=3)),
                seg("online", nine + timedelta(hours=4), nine + timedelta(hours=8)),
            ],
            start,
            end,
            now,
        )
    )
    assert (found.online_s, found.away_s, found.offline_s) == (6 * 3600, 3600, 3600)
    assert found.sessions == 2
    assert found.first_seen == nine and found.last_seen == nine + timedelta(hours=8)
    assert presence.totals([]) == presence.Totals()


def test_a_stretch_over_midnight_is_split_and_an_open_one_runs_to_now() -> None:
    first, second = date(2026, 11, 22), date(2026, 11, 23)
    midnight = presence.day_bounds(second, TZ)[0]
    now = midnight + timedelta(hours=2)
    stretch = [seg("online", midnight - timedelta(hours=1), None)]
    before = presence.totals(presence.clip(stretch, *presence.day_bounds(first, TZ), now))
    after = presence.totals(presence.clip(stretch, *presence.day_bounds(second, TZ), now))
    assert before.online_s == 3600 and before.last_seen == midnight
    assert after.online_s == 2 * 3600 and after.first_seen == midnight


# -- who sees whom ---------------------------------------------------------------------------------


def ids(body: dict[str, Any]) -> set[int]:
    return {row["user"]["id"] for row in body["rows"]}


def test_a_stretch_begun_before_the_window_counts_and_older_ones_do_not(
    world: World, db_session: Session
) -> None:
    start, end = presence.day_bounds(date(2026, 11, 23), TZ)
    hour = timedelta(hours=1)

    def add(user: User, state: str, a: float, b: float | None) -> None:
        ended = None if b is None else start + b * hour
        db_session.add(
            PresenceSegment(
                user_id=user.id,
                state=state,
                started_at=start + a * hour,
                ended_at=ended,
                last_seen_at=ended or start + a * hour,
            )
        )

    add(world.an, "online", -30, -29)
    add(world.an, "away", -1, 1)
    add(world.an, "online", 2, None)
    add(world.binh, "online", -5, -4)
    db_session.flush()
    everyone = [world.an.id, world.binh.id, world.lead.id]
    found = presence.segments_between(db_session, everyone, start, end)
    assert [(s.state, s.started_at) for s in found[world.an.id]] == [
        ("away", start - hour),
        ("online", start + 2 * hour),
    ]
    assert world.binh.id not in found and world.lead.id not in found
    assert presence.segments_between(db_session, [], start, end) == {}
    seen = presence.last_seen(db_session, everyone)
    assert seen == {world.an.id: start + hour, world.binh.id: start - 4 * hour}


def test_scope_own_team_all(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    sessions: presence.Sessions,
    store: presence.MemoryStore,
) -> None:
    now = datetime.now(UTC).timestamp()
    presence.beat(
        sessions,
        store,
        world.an.id,
        f"{TAB_A}.1",
        active=True,
        device=DEVICE,
        kind="desktop",
        now=now - 20,
    )
    presence.beat(
        sessions,
        store,
        world.an.id,
        f"{TAB_B}.1",
        active=False,
        device=PHONE,
        kind="mobile",
        now=now - 10,
    )
    presence.beat(
        sessions,
        store,
        world.binh.id,
        f"{TAB_A}.1",
        active=False,
        device=DEVICE,
        kind="desktop",
        now=now - 10,
    )

    mine = client.get(f"{API}/presence", headers=as_user(world.an)).json()
    assert ids(mine) == {world.an.id} and mine["scope"] == "own"
    me = mine["rows"][0]
    assert (me["state"], me["tabs"], me["devices"]) == ("online", 2, 2)
    assert me["kinds"] == ["desktop", "mobile"]
    assert me["day"]["sessions"] == 1 and me["day"]["online_s"] >= 19

    # An is in another team too: their lead of Backend is not told which.
    db_session.add(
        TeamMember(team_id=world.mobile.id, user_id=world.an.id, joined_at=date(2025, 1, 1))
    )
    db_session.flush()
    team = client.get(f"{API}/presence", headers=as_user(world.lead)).json()
    assert ids(team) == {world.lead.id, world.an.id, world.binh.id} and team["scope"] == "team"
    assert team["counts"] == {"online": 1, "away": 1, "offline": 1}
    assert {t["name"] for t in team["teams"]} == {"Backend"}

    managed = client.get(f"{API}/presence", headers=as_user(world.manager)).json()
    assert ids(managed) == {world.manager.id, world.lead.id, world.an.id, world.binh.id}

    everyone = client.get(f"{API}/presence", headers=as_user(world.admin)).json()
    assert world.outsider.id in ids(everyone) and everyone["scope"] == "all"
    assert {t["name"] for t in everyone["teams"]} == {"Backend", "Engineering", "Mobile"}

    assert client.get(f"{API}/presence").status_code == 401
    far = (date.today() - timedelta(days=presence.KEEP_DAYS + 5)).isoformat()
    assert client.get(f"{API}/presence?day={far}", headers=as_user(world.admin)).status_code == 422


def test_one_person_days_and_stretches_only_for_who_may_see_them(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
) -> None:
    today = datetime.now(UTC).astimezone(TZ).date()
    start, _ = presence.day_bounds(today - timedelta(days=1), TZ)
    nine = start + timedelta(hours=9)
    db_session.add_all(
        [
            PresenceSegment(
                user_id=world.an.id,
                state="online",
                started_at=nine,
                ended_at=nine + timedelta(hours=1),
                last_seen_at=nine + timedelta(hours=1),
            ),
            PresenceSegment(
                user_id=world.an.id,
                state="away",
                started_at=nine + timedelta(hours=1),
                ended_at=nine + timedelta(hours=1, minutes=30),
                last_seen_at=nine + timedelta(hours=1, minutes=30),
            ),
        ]
    )
    db_session.flush()
    url = f"{API}/presence/users/{world.an.id}"
    body = client.get(url, headers=as_user(world.lead)).json()
    assert len(body["days"]) == 7 and body["days"][0]["day"] == today.isoformat()
    yesterday = body["days"][1]
    assert (yesterday["online_s"], yesterday["away_s"], yesterday["offline_s"]) == (3600, 1800, 0)
    assert [s["state"] for s in yesterday["stretches"]] == ["online", "away"]
    assert body["totals"] == {"online_s": 3600, "away_s": 1800, "offline_s": 0}

    assert client.get(url, headers=as_user(world.an)).status_code == 200
    assert client.get(url, headers=as_user(world.binh)).status_code == 404
    assert client.get(url, headers=as_user(world.outsider)).status_code == 404
    one = client.get(
        f"{url}?from={today.isoformat()}&to={today.isoformat()}", headers=as_user(world.admin)
    ).json()
    assert [d["day"] for d in one["days"]] == [today.isoformat()]
    bad = client.get(
        f"{url}?from={today.isoformat()}&to={(today - timedelta(days=2)).isoformat()}",
        headers=as_user(world.admin),
    )
    assert bad.status_code == 422


# -- the socket and its stand-in -------------------------------------------------------------------


def wait(done: Callable[[], bool], seconds: float = 5.0) -> bool:
    """The socket's handler runs in the app's thread: what it wrote shows a moment later."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if done():
            return True
        time.sleep(0.02)
    return done()


def test_socket_marks_online_away_and_offline_on_close(
    client: TestClient,
    world: World,
    settings: Settings,
    db_session: Session,
    store: presence.MemoryStore,
) -> None:
    client.cookies.set(SESSION_COOKIE, issue_token(world.an.id, settings))
    url = f"{API}/presence/ws?tab={TAB_A}&device={DEVICE}"
    with client.websocket_connect(url) as ws:
        assert ws.receive_json() == {"ok": True}
        assert store.state(world.an.id) == "online"
        assert [c.tab for c in store.conns(world.an.id)] == [TAB_A]
        ws.send_text("not json")
        ws.send_text('{"active": false}')
        assert wait(lambda: store.state(world.an.id) == "away")
        ws.send_text('{"active": true}')
        assert wait(lambda: store.state(world.an.id) == "online")
    # Closed: the tab is gone at once, no waiting for the sweeper.
    assert wait(lambda: store.live_users() == [])
    found = rows(db_session, world.an)
    assert [state for state, _, _ in found] == ["online", "away", "online"]
    assert found[-1][2] is not None


def test_socket_refuses_the_signed_out_bad_ids_and_too_many(
    client: TestClient,
    world: World,
    settings: Settings,
    store: presence.MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refused_with(url: str) -> int:
        # Accepted, then closed with the code: a browser reads no code off a refused handshake.
        with client.websocket_connect(url) as ws, pytest.raises(WebSocketDisconnect) as refused:
            ws.receive_text()
        return refused.value.code

    url = f"{API}/presence/ws?tab={TAB_A}&device={DEVICE}"
    assert refused_with(url) == presence_api.CLOSE_SIGNED_OUT
    client.cookies.set(SESSION_COOKIE, issue_token(world.an.id, settings))
    bad = f"{API}/presence/ws?tab=x&device={DEVICE}"
    assert refused_with(bad) == presence_api.CLOSE_BAD_REQUEST
    assert store.live_users() == []
    # More sockets than anyone opens: the next one is turned away, the count is given back.
    monkeypatch.setitem(presence_api._open, world.an.id, presence.MAX_TABS)
    assert refused_with(url) == presence_api.CLOSE_TOO_MANY
    monkeypatch.setitem(presence_api._open, world.an.id, 0)
    with client.websocket_connect(url) as ws:
        assert ws.receive_json() == {"ok": True}
        assert presence_api._open[world.an.id] == 1
    assert wait(lambda: world.an.id not in presence_api._open)


def test_redis_down_pauses_presence_instead_of_waiting_on_every_call(
    client: TestClient, world: World, as_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    class Down:
        def __getattr__(self, name: str) -> Any:
            def fail(*args: Any, **kwargs: Any) -> Any:
                calls.append(name)
                raise RedisError("no answer")

            return fail

    monkeypatch.setattr(presence_api, "store_of", lambda: Down())
    monkeypatch.setattr(presence_api, "_paused_until", 0.0)
    an = as_user(world.an)
    body = {"tab": TAB_A, "device": DEVICE, "active": True}
    for _ in range(3):
        assert client.post(f"{API}/presence/beat", headers=an, json=body).status_code == 204
    assert len(calls) == 1  # the first failure; the rest did not try
    board = client.get(f"{API}/presence", headers=an).json()
    assert board["live"] is False and board["rows"][0]["state"] == "offline"
    assert len(calls) == 1


def test_http_stand_in_beats_and_leaves(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    store: presence.MemoryStore,
) -> None:
    an = as_user(world.an)
    body = {"tab": TAB_A, "device": DEVICE, "active": False}
    assert client.post(f"{API}/presence/beat", headers=an, json=body).status_code == 204
    assert store.state(world.an.id) == "away"
    assert (
        client.post(f"{API}/presence/beat", headers=an, json={**body, "tab": "x"}).status_code
        == 422
    )
    assert client.post(f"{API}/presence/beat", json=body).status_code == 401
    assert client.post(f"{API}/presence/leave", headers=an, json={"tab": TAB_A}).status_code == 204
    assert store.live_users() == []
    assert [state for state, _, _ in rows(db_session, world.an)] == ["away"]


# -- forced sign-out (§14) -------------------------------------------------------------------------


def signed_in(client: TestClient, token: str) -> int:
    return client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code


def test_admin_signs_one_person_out_everywhere(
    client: TestClient,
    world: World,
    as_user: Any,
    settings: Settings,
    db_session: Session,
    sessions: presence.Sessions,
    store: presence.MemoryStore,
) -> None:
    an_token = issue_token(world.an.id, settings)
    binh_token = issue_token(world.binh.id, settings)
    now = datetime.now(UTC).timestamp()
    presence.beat(
        sessions,
        store,
        world.an.id,
        f"{TAB_A}.1",
        active=True,
        device=DEVICE,
        kind="desktop",
        now=now - 60,
    )
    url = f"{API}/presence/users/{world.an.id}/sign-out"
    assert client.post(url, headers=as_user(world.lead)).status_code == 403
    assert client.post(url).status_code == 401
    missing = f"{API}/presence/users/999999/sign-out"
    assert client.post(missing, headers=as_user(world.admin)).status_code == 404
    assert signed_in(client, an_token) == 200

    assert client.post(url, headers=as_user(world.admin)).status_code == 204
    refused = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {an_token}"})
    assert refused.status_code == 401 and "đăng nhập lại" in refused.json()["detail"]
    assert signed_in(client, binh_token) == 200  # nobody else is touched
    # Signing in again (GitLab) gives a token of the new epoch: not a lock-out.
    db_session.refresh(world.an)
    assert world.an.session_epoch == 1
    assert signed_in(client, issue_token(world.an.id, settings, epoch=1)) == 200
    # Offline at once on the monitor, the stretch closed, the tab forgotten.
    assert store.live_users() == [] and store.kicked_at(world.an.id) is not None
    (stretch,) = rows(db_session, world.an)
    assert stretch[0] == "online" and stretch[2] is not None
    logged = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "auth.force_logout")
    ).all()
    assert [(a.actor_user_id, a.target) for a in logged] == [
        (world.admin.id, f"user:{world.an.id}")
    ]
    board = client.get(f"{API}/presence", headers=as_user(world.admin)).json()
    assert board["can_sign_out"] is True
    assert (
        client.get(f"{API}/presence", headers=as_user(world.lead)).json()["can_sign_out"] is False
    )


def test_admin_signs_everyone_out_but_themself(
    client: TestClient,
    world: World,
    as_user: Any,
    settings: Settings,
    db_session: Session,
    sessions: presence.Sessions,
    store: presence.MemoryStore,
) -> None:
    tokens = {u.id: issue_token(u.id, settings) for u in (world.admin, world.an, world.lead)}
    now = datetime.now(UTC).timestamp()
    for user in (world.admin, world.an, world.lead):
        presence.beat(
            sessions,
            store,
            user.id,
            f"{TAB_A}.1",
            active=True,
            device=DEVICE,
            kind="desktop",
            now=now - 30,
        )
    assert client.post(f"{API}/presence/sign-out-all", headers=as_user(world.an)).status_code == 403
    done = client.post(f"{API}/presence/sign-out-all", headers=as_user(world.admin))
    assert done.status_code == 200 and done.json() == {"accounts": 5}
    assert signed_in(client, tokens[world.admin.id]) == 200
    assert signed_in(client, tokens[world.an.id]) == 401
    assert signed_in(client, tokens[world.lead.id]) == 401
    assert store.live_users() == [world.admin.id]
    assert rows(db_session, world.admin)[-1][2] is None  # still online
    assert rows(db_session, world.lead)[-1][2] is not None


def test_a_token_from_before_the_epoch_counts_as_epoch_zero(
    client: TestClient, world: World, settings: Settings, db_session: Session
) -> None:
    now = datetime.now(UTC)
    claims = {
        "sub": str(world.an.id),
        "iat": now,
        "exp": now + timedelta(hours=1),
        "iss": "codepulse",
    }
    old = jwt.encode(claims, settings.session_secret.get_secret_value(), algorithm=ALGORITHM)
    assert signed_in(client, old) == 200  # sessions open at the upgrade go on
    world.an.session_epoch = 1
    db_session.flush()
    assert signed_in(client, old) == 401


def test_an_open_socket_closes_at_its_next_refresh_a_later_one_is_fine(
    client: TestClient,
    world: World,
    as_user: Any,
    settings: Settings,
    sessions: presence.Sessions,
    store: presence.MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The rule itself: a connection opened before the sign-out is refused, a later one is not.
    presence.kick(sessions, store, world.binh.id, now=T0 + 50)
    with pytest.raises(presence.SignedOut):
        presence.beat(
            sessions,
            store,
            world.binh.id,
            "b.1",
            active=True,
            device=DEVICE,
            kind="desktop",
            now=T0 + 60,
            since=T0 + 40,
        )
    assert store.live_users() == []
    assert (
        presence.beat(
            sessions,
            store,
            world.binh.id,
            "b.2",
            active=True,
            device=DEVICE,
            kind="desktop",
            now=T0 + 60,
            since=T0 + 55,
        )
        == "online"
    )

    # And through the socket: it is told at its next refresh (BEAT_S, shortened here).
    monkeypatch.setattr(presence, "BEAT_S", 0.2)
    client.cookies.set(SESSION_COOKIE, issue_token(world.an.id, settings))
    with client.websocket_connect(f"{API}/presence/ws?tab={TAB_A}&device={DEVICE}") as ws:
        assert ws.receive_json() == {"ok": True}
        client.cookies.delete(SESSION_COOKIE)
        url = f"{API}/presence/users/{world.an.id}/sign-out"
        assert client.post(url, headers=as_user(world.admin)).status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == presence_api.CLOSE_SIGNED_OUT
    assert wait(lambda: world.an.id not in store.live_users())
