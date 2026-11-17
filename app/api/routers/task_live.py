"""Live Board / task detail (docs/specs/31 §21.7): Server-Sent Events of one task space.

`GET /tasks/spaces/{bp_id}/live` streams `task` events `{type: "task", key, changed: [...]}`
for every task of the dự án that is created, changed, moved, deleted or commented, by anyone
(git rules and automation included; published by app/tasks/live.py after the commit). Only one
who sees Task on the dự án opens it; the stream ends after STREAM_S and the browser reconnects,
which checks the right again, so a withdrawn right stops the events within minutes. There is
no replay: on (re)connect the browser reloads what it shows (the `ready` event says when).
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.tasks import service as ts
from app.tasks.live import hub, sse

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]

STREAM_S = 10 * 60
PING_S = 15.0  # a comment line, so proxies do not drop a quiet stream


def _check(session: Session, principal: Principal, bp_id: int) -> None:
    # 404 without tasks.view on the dự án (or without Task there), as /tasks/spaces/{bp_id}/*.
    ts.space_or_404(session, principal, bp_id)
    # The stream lives for minutes: give the pooled connection back before it starts.
    session.commit()


@router.get("/tasks/spaces/{bp_id}/live")
async def live(
    bp_id: int, request: Request, principal: CurrentPrincipal, session: SessionDep
) -> StreamingResponse:
    await run_in_threadpool(_check, session, principal, bp_id)

    async def events() -> AsyncIterator[str]:
        # Opened here, not before the response starts: a client gone before the first byte
        # leaves no queue behind (the finally below always runs once this starts).
        queue = hub.open(bp_id)
        try:
            yield "retry: 3000\n\n"
            yield "event: ready\ndata: {}\n\n"
            end = time.monotonic() + STREAM_S
            while time.monotonic() < end:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), PING_S)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield sse(message)
        finally:
            hub.close(bp_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
