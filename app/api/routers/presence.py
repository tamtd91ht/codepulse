"""Presence (docs/specs/33): the socket every signed-in tab keeps open, its plain-HTTP stand-in,
and the monitoring screen's two reads.

The socket is the heartbeat: the session is checked once when it opens (a request would load
the whole principal every 30 s), the server refreshes the tab's record while it stays open, and
a closed tab or a dropped network is known at once. The browser only says when the person
starts or stops using the tab (`{"active": true|false}`). A browser whose socket cannot open
(a proxy without WebSocket) posts /presence/beat instead; both feed app/presence.

The dashboard's /api rewrite carries the upgrade (Next 16, checked 2026-11-23); a reverse proxy
in front of it must forward `Upgrade` / `Connection` (docs/RUNBOOK.md).
"""

import asyncio
import json
import logging
import random
import re
import secrets
import time
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app import presence
from app.api.deps import get_session
from app.api.security import SESSION_COOKIE, CurrentPrincipal, Principal, read_token, require
from app.avatars import avatar_url
from app.config import Settings, get_settings
from app.models import Team, TeamMember, User

log = logging.getLogger(__name__)

router = APIRouter(tags=["presence"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

PERMISSION = "presence.view"
ID = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
# The session and the account are checked again this often while a socket stays open.
RECHECK_S = 300
MAX_MESSAGE = 200
MAX_DAYS = 62
# Close codes the browser acts on (it stops for good): 4401 signed out, 4400 bad ids,
# 4429 more sockets than MAX_TABS for one person.
CLOSE_SIGNED_OUT = 4401
CLOSE_BAD_REQUEST = 4400
CLOSE_TOO_MANY = 4429
# A tab's `active` flag is written at most this often.
MIN_CHANGE_S = 2.0
# After Redis failed to answer, presence is not tried again for this long.
PAUSE_S = 30.0

# Swapped in tests (no Redis, one rolled-back session).
sessions: presence.Sessions = presence.default_sessions
store_of = presence.get_store
settings_of = get_settings

_paused_until = 0.0
# Sockets open in this process, per person (the store caps the records; this caps the loops).
_open: dict[int, int] = {}


def _quiet(call: Any, *args: Any, **kwargs: Any) -> None:
    """Presence never breaks the page. A Redis that does not answer is left alone for PAUSE_S
    (and said once): every open socket refreshes through the shared thread pool, and a thousand
    of them each waiting out a timeout would starve the ordinary requests."""
    global _paused_until
    if time.monotonic() < _paused_until:
        return
    try:
        call(sessions, store_of(), *args, **kwargs)
    except RedisError:
        _paused_until = time.monotonic() + PAUSE_S
        log.warning("presence not recorded: Redis does not answer", exc_info=True)
    except Exception:
        log.exception("presence not recorded")


def _socket_user(token: str | None) -> int:
    """Who the socket is for, in a short session of its own (as the inbox stream)."""
    if not token:
        raise HTTPException(401, "not signed in")
    user_id = read_token(token, settings_of())
    with sessions() as session:
        user = session.get(User, user_id)
        if user is None or user.active is False:
            raise HTTPException(401, "user is not active")
    return user_id


def _says_active(text: str, current: bool) -> bool:
    if len(text) > MAX_MESSAGE:
        return current
    try:
        data = json.loads(text)
    except ValueError:
        return current
    value = data.get("active") if isinstance(data, dict) else None
    return value if isinstance(value, bool) else current


@router.websocket("/presence/ws")
async def presence_socket(ws: WebSocket, tab: str = "", device: str = "") -> None:
    """One per tab, for as long as it is open. The session cookie rides on the handshake
    (SameSite=Lax: another site's page cannot open this socket as the person).

    A refusal is an accepted socket closed with a code: closed before it is accepted, the
    browser sees a failed handshake (1006) and cannot tell "signed out" from "no WebSocket
    here", so it would retry for ever."""
    token = ws.cookies.get(SESSION_COOKIE)
    await ws.accept()
    try:
        user_id = await run_in_threadpool(_socket_user, token)
    except HTTPException:
        await ws.close(code=CLOSE_SIGNED_OUT)
        return
    if not ID.match(tab) or not ID.match(device):
        await ws.close(code=CLOSE_BAD_REQUEST)
        return
    if _open.get(user_id, 0) >= presence.MAX_TABS:
        await ws.close(code=CLOSE_TOO_MANY)
        return
    _open[user_id] = _open.get(user_id, 0) + 1
    conn_id = f"{tab}.{secrets.token_hex(4)}"
    kind = presence.kind_of(ws.headers.get("user-agent"))
    active = True
    wanted: bool | None = None  # a change of `active` waiting for MIN_CHANGE_S to pass

    def refresh() -> None:
        _quiet(presence.beat, user_id, conn_id, active=active, device=device, kind=kind)

    try:
        await run_in_threadpool(refresh)
        await ws.send_text('{"ok":true}')  # the browser now knows the socket works end to end
        now = time.monotonic()
        checked = changed = now
        # Not every socket of a restarted server on the same second.
        due = now + presence.BEAT_S * (0.5 + random.random() / 2)  # noqa: S311
        while True:
            now = time.monotonic()
            if now - checked > RECHECK_S:  # on its own clock: chatter cannot put it off
                try:
                    await run_in_threadpool(_socket_user, token)
                except HTTPException:
                    await ws.close(code=CLOSE_SIGNED_OUT)
                    return
                checked = now
            wake = due if wanted is None else min(due, changed + MIN_CHANGE_S)
            try:
                text = await asyncio.wait_for(ws.receive_text(), max(0.05, wake - now))
            except TimeoutError:
                text = None
            except KeyError:  # a binary frame: not this protocol
                await ws.close(code=1003)
                return
            if text is not None:
                said = _says_active(text, active if wanted is None else wanted)
                wanted = None if said == active else said
            now = time.monotonic()
            # A change is written at most once per MIN_CHANGE_S (the last word wins): a tab
            # flipping its flag cannot write a row per message.
            if wanted is not None and now - changed >= MIN_CHANGE_S:
                active, wanted, changed = wanted, None, now
                await run_in_threadpool(refresh)
                due = now + presence.BEAT_S
            elif now >= due:
                await run_in_threadpool(refresh)
                due = now + presence.BEAT_S
    except WebSocketDisconnect:
        pass
    finally:
        left = _open.get(user_id, 1) - 1
        if left > 0:
            _open[user_id] = left
        else:
            _open.pop(user_id, None)
        await run_in_threadpool(_quiet, presence.leave, user_id, conn_id)


class BeatIn(BaseModel):
    tab: str = Field(pattern=ID.pattern)
    device: str = Field(pattern=ID.pattern)
    active: bool = True


class LeaveIn(BaseModel):
    tab: str = Field(pattern=ID.pattern)


def _dashboard_only(principal: Principal) -> None:
    if principal.token_id is not None:
        raise HTTPException(403, "presence is the dashboard's")


@router.post("/presence/beat", status_code=204)
def post_beat(body: BeatIn, request: Request, principal: CurrentPrincipal) -> Response:
    """The stand-in for the socket, every BEAT_S from a tab that could not open one."""
    _dashboard_only(principal)
    _quiet(
        presence.beat,
        principal.user_id,
        f"{body.tab}.http",
        active=body.active,
        device=body.device,
        kind=presence.kind_of(request.headers.get("user-agent")),
    )
    return Response(status_code=204)


@router.post("/presence/leave", status_code=204)
def post_leave(body: LeaveIn, principal: CurrentPrincipal) -> Response:
    _dashboard_only(principal)
    _quiet(presence.leave, principal.user_id, f"{body.tab}.http")
    return Response(status_code=204)


# -- the monitoring screen -------------------------------------------------------------------------


def _visible(principal: Principal) -> set[int] | None:
    """Whose presence one sees: None = everyone ("all"); the people one leads or manages
    ("team"); oneself ("own")."""
    scope = principal.scope(PERMISSION)
    if scope == "all":
        return None
    if scope == "team":
        return {principal.user_id} | principal.team_people(PERMISSION)
    if scope == "own":
        return {principal.user_id}
    raise HTTPException(403, "thiếu quyền xem trạng thái online")


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _totals(found: presence.Totals) -> dict[str, Any]:
    return {
        "online_s": found.online_s,
        "away_s": found.away_s,
        "offline_s": found.offline_s,
        "first_seen": _iso(found.first_seen),
        "last_seen": _iso(found.last_seen),
        "sessions": found.sessions,
    }


def _teams_of(
    session: Session, user_ids: list[int], today: date, only: set[int] | None
) -> dict[int, list[dict[str, Any]]]:
    """The current teams of each person; `only` (not None) keeps the teams the viewer is in,
    leads or manages: a lead does not learn which other teams their people belong to."""
    query = (
        select(TeamMember.user_id, Team.id, Team.name)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            TeamMember.user_id.in_(user_ids),
            TeamMember.joined_at <= today,
            (TeamMember.left_at.is_(None)) | (TeamMember.left_at > today),
        )
        .order_by(Team.name)
    )
    if only is not None:
        query = query.where(Team.id.in_(sorted(only)))
    rows = session.execute(query)
    out: dict[int, list[dict[str, Any]]] = {}
    for user_id, team_id, name in rows:
        teams = out.setdefault(int(user_id), [])
        if all(t["id"] != team_id for t in teams):
            teams.append({"id": int(team_id), "name": str(name)})
    return out


def _live(user_ids: list[int], now: float) -> dict[int, list[presence.Conn]] | None:
    """The living tabs of each person, read from the store itself (right even when the sweeper
    lags); None when Redis does not answer: the screen then shows the history only."""
    global _paused_until
    if time.monotonic() < _paused_until:
        return None
    try:
        held = store_of().many(user_ids)
    except RedisError:
        _paused_until = time.monotonic() + PAUSE_S
        return None
    return {uid: presence.alive(conns, now) for uid, conns in held.items()}


@router.get("/presence", dependencies=[require(PERMISSION)])
def get_presence(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    day: date | None = None,
) -> dict[str, Any]:
    """Everyone one may see: their state now, since when, and their totals for `day` (today when
    omitted, in REPORT_TZ)."""
    tz = ZoneInfo(settings.report_tz)
    now = datetime.now(UTC)
    today = now.astimezone(tz).date()
    day = day or today
    if day > today or day < today - timedelta(days=presence.KEEP_DAYS):
        raise HTTPException(422, "ngày ngoài khoảng lưu lịch sử")
    visible = _visible(principal)
    query = select(User).where(User.active.is_not(False))
    if visible is not None:
        query = query.where(User.id.in_(sorted(visible)))
    users = list(session.scalars(query.order_by(User.full_name, User.id)))
    ids = [u.id for u in users]
    start, end = presence.day_bounds(day, tz)
    segments = presence.segments_between(session, ids, start, end)
    open_since = presence.open_stretches(session, ids) if ids else {}
    live = _live(ids, now.timestamp()) if ids else {}
    own_teams = principal.own_teams | principal.lead_teams | principal.managed_teams
    teams = _teams_of(session, ids, today, None if visible is None else own_teams) if ids else {}
    states: dict[int, str] = {}
    for user in users:
        if live is None:  # no live store: what the history says
            states[user.id] = open_since.get(user.id, (presence.OFFLINE, None))[0]
        else:
            states[user.id] = presence.state_of(live.get(user.id, []))
    seen = presence.last_seen(session, [u for u, s in states.items() if s == presence.OFFLINE])
    rows = []
    counts = {presence.ONLINE: 0, presence.AWAY: 0, presence.OFFLINE: 0}
    for user in users:
        conns = (live or {}).get(user.id, [])
        state = states[user.id]
        counts[state] += 1
        recorded = open_since.get(user.id)
        if state == presence.OFFLINE:
            since = seen.get(user.id)
        else:
            # The stretch being written; the store ran ahead of it for a moment: now.
            since = recorded[1] if recorded and recorded[0] == state else now
        rows.append(
            {
                "user": {
                    "id": user.id,
                    "full_name": user.full_name,
                    "email": user.primary_email,
                    "avatar_url": avatar_url(user.id, user.avatar_file, user.updated_at),
                },
                "teams": teams.get(user.id, []),
                "state": state,
                "since": _iso(since),
                "tabs": len({c.tab for c in conns}),
                "devices": len({c.device for c in conns}),
                "kinds": sorted({c.kind for c in conns}),
                "day": _totals(
                    presence.totals(presence.clip(segments.get(user.id, []), start, end, now))
                ),
            }
        )
    all_teams = sorted(
        {t["id"]: t["name"] for found in teams.values() for t in found}.items(),
        key=lambda item: item[1],
    )
    return {
        "now": _iso(now),
        "day": day.isoformat(),
        "today": today.isoformat(),
        "tz": settings.report_tz,
        "scope": principal.scope(PERMISSION),
        "live": live is not None,
        "counts": counts,
        "teams": [{"id": tid, "name": name} for tid, name in all_teams],
        "rows": rows,
        "idle_minutes": presence.IDLE_S // 60,
        "keep_days": presence.KEEP_DAYS,
    }


@router.get("/presence/users/{user_id}", dependencies=[require(PERMISSION)])
def get_presence_user(
    user_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    """One person's days: totals and the stretches of each day (for the timeline), newest
    first. At most MAX_DAYS days; the last 7 when no range is given."""
    visible = _visible(principal)
    user = session.get(User, user_id)
    if user is None or (visible is not None and user_id not in visible):
        raise HTTPException(404, "user not found")
    tz = ZoneInfo(settings.report_tz)
    now = datetime.now(UTC)
    today = now.astimezone(tz).date()
    last = min(date_to or today, today)
    first = date_from or last - timedelta(days=6)
    if first > last:
        raise HTTPException(422, "khoảng ngày không hợp lệ")
    first = max(first, last - timedelta(days=MAX_DAYS - 1))
    start, _ = presence.day_bounds(first, tz)
    _, end = presence.day_bounds(last, tz)
    segments = presence.segments_between(session, [user_id], start, end).get(user_id, [])
    days = []
    sums = {"online_s": 0, "away_s": 0, "offline_s": 0}
    current = last
    while current >= first:
        day_start, day_end = presence.day_bounds(current, tz)
        stretches = presence.clip(segments, day_start, day_end, now)
        found = presence.totals(stretches)
        sums["online_s"] += found.online_s
        sums["away_s"] += found.away_s
        sums["offline_s"] += found.offline_s
        days.append(
            {
                "day": current.isoformat(),
                "start": _iso(day_start),
                "end": _iso(day_end),
                **_totals(found),
                "stretches": [
                    {"state": s.state, "start": _iso(s.start), "end": _iso(s.end)}
                    for s in stretches
                ],
            }
        )
        current -= timedelta(days=1)
    return {
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.primary_email,
            "avatar_url": avatar_url(user.id, user.avatar_file, user.updated_at),
        },
        "from": first.isoformat(),
        "to": last.isoformat(),
        "tz": settings.report_tz,
        "now": _iso(now),
        "totals": sums,
        "days": days,
    }
