"""The bell (docs/specs/24): one's own in-app notifications, their history and the live stream.

Everyone signed in has an inbox; no permission beyond being signed in (only one's own rows are
ever read or changed). The live stream is Server-Sent Events: the push only ever goes
server -> browser, and EventSource reconnects with Last-Event-ID on its own. (The dashboard's
/api rewrite does carry a WebSocket upgrade since Next 16, checked 2026-11-23; presence,
docs/specs/33, uses one because it also needs the browser to speak.)
"""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import SESSION_COOKIE, CurrentPrincipal, session_user
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import UserNotification
from app.notifications.inbox import as_message, snooze, unsnoozed
from app.notifications.stream import hub, sse

router = APIRouter(tags=["inbox"])
SessionDep = Annotated[Session, Depends(get_session)]

PAGE = 30
MAX_PAGE = 100
# A stream ends after this long and the browser reconnects at once (EventSource does it on
# its own, with Last-Event-ID): the sign-in is checked again and nothing is held for good.
STREAM_S = 10 * 60
PING_S = 15.0  # a comment line, so proxies do not drop a quiet stream
REPLAY_MAX = 50


class InboxPage(BaseModel):
    items: list[dict[str, Any]]
    unread: int
    next_before: int | None


class ReadIn(BaseModel):
    ids: list[int] | None = None  # None: every unread one


class SnoozeIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


def _unread(session: Session, user_id: int) -> int:
    """Unread and not snoozed right now (31 §20.5)."""
    return int(
        session.scalar(
            select(func.count()).where(
                UserNotification.user_id == user_id,
                UserNotification.read_at.is_(None),
                unsnoozed(datetime.now(UTC)),
            )
        )
        or 0
    )


@router.get("/inbox", response_model=InboxPage)
def list_inbox(
    principal: CurrentPrincipal,
    session: SessionDep,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = PAGE,
    unread: bool = False,
    kind: Annotated[
        str | None,
        Query(
            pattern=r"^(review_done|review_failed|finding_closed|dispute_new|dispute_decided"
            r"|task\.[a-z]+)$"
        ),
    ] = None,
) -> dict[str, Any]:
    """Newest first; `before` (an id) pages back through the history. `unread` leaves out the
    rows snoozed right now."""
    query = select(UserNotification).where(UserNotification.user_id == principal.user_id)
    if before is not None:
        query = query.where(UserNotification.id < before)
    if unread:
        query = query.where(UserNotification.read_at.is_(None), unsnoozed(datetime.now(UTC)))
    if kind is not None:
        query = query.where(UserNotification.kind == kind)
    rows = list(session.scalars(query.order_by(UserNotification.id.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [as_message(r) for r in rows],
        "unread": _unread(session, principal.user_id),
        "next_before": rows[-1].id if more and rows else None,
    }


@router.get("/inbox/unread-count")
def unread_count(principal: CurrentPrincipal, session: SessionDep) -> dict[str, int]:
    return {"unread": _unread(session, principal.user_id)}


@router.post("/inbox/read")
def mark_read(body: ReadIn, principal: CurrentPrincipal, session: SessionDep) -> dict[str, int]:
    """Mark some (`ids`) or all of one's unread notifications read; returns what is left."""
    query = update(UserNotification).where(
        UserNotification.user_id == principal.user_id, UserNotification.read_at.is_(None)
    )
    if body.ids is not None:
        if not body.ids:
            return {"unread": _unread(session, principal.user_id)}
        query = query.where(UserNotification.id.in_(body.ids[:500]))
    session.execute(
        query.values(read_at=datetime.now(UTC)).execution_options(synchronize_session=False)
    )
    session.commit()
    return {"unread": _unread(session, principal.user_id)}


@router.post("/inbox/snooze")
def snooze_notes(
    body: SnoozeIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    """Snooze, "Tạm ẩn 1 ngày" (31 §20.5): one's own unread rows leave the unread count and
    the unread list until `snoozed_until`; they stay in the history."""
    until = snooze(session, principal.user_id, body.ids)
    session.commit()
    return {"snoozed_until": until.isoformat(), "unread": _unread(session, principal.user_id)}


def _stream_user(token: str | None) -> int:
    """Who the stream is for, checked in a short session of its own: a stream lives for minutes
    and must not hold a pooled connection (the request's session would)."""
    if not token:
        raise HTTPException(401, "not signed in")
    with get_sessionmaker()() as session:
        return session_user(session, token, get_settings()).id


def _missed(user_id: int, after: int | None) -> tuple[list[dict[str, Any]], int]:
    """What came after `after` (None: nothing to replay) and the person's newest id, which the
    `ready` event carries as its id: every reconnect then sends Last-Event-ID, even when no
    notification arrived on the stream before it ended."""
    with get_sessionmaker()() as session:
        newest = session.scalar(
            select(func.max(UserNotification.id)).where(UserNotification.user_id == user_id)
        )
        if after is None:
            return [], int(newest or 0)
        rows = session.scalars(
            select(UserNotification)
            .where(UserNotification.user_id == user_id, UserNotification.id > after)
            .order_by(UserNotification.id)
            .limit(REPLAY_MAX)
        )
        return [as_message(r) for r in rows], int(newest or 0)


@router.get("/inbox/stream")
async def stream(
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
    after: int | None = None,
) -> StreamingResponse:
    """Server-Sent Events: `notification` events (one row each, as in GET /inbox). A
    reconnecting browser sends Last-Event-ID (or `after`) and first gets what it missed."""
    token = request.cookies.get(SESSION_COOKIE)
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    user_id = await run_in_threadpool(_stream_user, token)
    since = after
    if last_event_id and last_event_id.isdigit():
        since = int(last_event_id)

    async def events() -> AsyncIterator[str]:
        # Opened here, not before the response starts: a client gone before the first byte
        # leaves no queue behind (the finally below always runs once this starts).
        queue = hub.open(user_id)
        try:
            yield "retry: 3000\n\n"
            missed, newest = await run_in_threadpool(_missed, user_id, since)
            last = since or 0
            for message in missed:
                last = max(last, int(message["id"]))
                yield sse(message)
            # More than REPLAY_MAX missed: the rest stays in the history (the badge counts it).
            last = max(last, newest)
            yield f"id: {last}\nevent: ready\ndata: {{}}\n\n"
            end = time.monotonic() + STREAM_S
            while time.monotonic() < end:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), PING_S)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if int(message.get("id", 0)) <= last:
                    continue  # already sent by the replay
                yield sse(message)
        finally:
            hub.close(user_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
