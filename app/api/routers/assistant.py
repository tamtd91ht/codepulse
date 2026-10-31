"""Stetho, the AI assistant (outside the spec, requested by the user 2026-10-31; see PROGRESS.md
and app/assistant).

Admins and managers only (the user's decision): a manager sees team aggregates, so that is all
Stetho can tell them. The answer streams as Server-Sent Events (`delta`, then `done` or
`error`). One question at a time per person; closing the drawer or "Dừng" kills the CLI. Every
turn is audited as `assistant.chat` with its cost, which counts toward the month's LLM budget
(app/workers/knowledge_jobs.month_spend_breakdown).
"""

import asyncio
import json
import logging
import queue
import threading
from collections.abc import AsyncIterator, Callable
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.admin_settings import effective, monthly_budget_usd
from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.security import CurrentPrincipal, Principal
from app.assistant.chat import (
    LOGIN_PLACE,
    MAX_QUESTION,
    MODELS,
    NAME,
    ChatOutcome,
    Streamer,
    Turn,
    run_chat,
)
from app.assistant.context import Screen, build_context
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.db import get_sessionmaker
from app.ratelimit import Limiter, shared_limiter
from app.review.credentials import stored_claude_token
from app.review.providers import default_redis
from app.review.providers.claude_code import LLM_MAX_PER_MINUTE, stream_lines

log = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

HEARTBEAT_S = 15.0
POLL_S = 0.05
MAX_RUNNING = 4
NOT_CONNECTED = f"{NAME} chưa được kết nối với Claude — nhờ admin vào {LOGIN_PLACE}."
# People with a question running (user ids). The API is one process (Dockerfile, compose).
_busy: set[int] = set()
_busy_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_RUNNING)


def require_assistant(principal: CurrentPrincipal) -> Principal:
    if not (principal.is_admin or principal.is_manager):
        raise HTTPException(403, f"{NAME} chỉ dành cho admin và manager")
    return principal


AssistantPrincipal = Annotated[Principal, Depends(require_assistant)]


class TurnIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=40_000)


class ScreenIn(BaseModel):
    path: str = Field(pattern=r"^/", max_length=200)
    search: str = Field(default="", max_length=500)
    title: str = Field(default="", max_length=80)


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    history: list[TurnIn] = Field(default_factory=list, max_length=200)
    screen: ScreenIn | None = None
    model: str = ""


@lru_cache
def _limiter() -> Limiter:
    """The reviews' pace, shared through Redis when there is one (app/ratelimit.py)."""
    return shared_limiter(LLM_MAX_PER_MINUTE, default_redis())


def get_assistant_streamer() -> Streamer:
    """The CLI runner; a fake in tests."""
    return stream_lines


Recorder = Callable[[int, dict[str, Any]], None]


def _record(user_id: int, details: dict[str, Any]) -> None:
    """The turn's audit row, in its own session: the request's is gone once the stream ends."""
    with get_sessionmaker()() as session:
        audit(session, user_id, "assistant.chat", "assistant", **details)
        session.commit()


def get_assistant_recorder() -> Recorder:
    return _record


def _token(session: Session, box: SecretBox, settings: Settings) -> str:
    """As the reviews pick it (app/review/providers.build_provider)."""
    stored = stored_claude_token(session, box)
    return (
        (stored.token if stored else "")
        or settings.claude_code_oauth_token.get_secret_value()
        or settings.llm_api_key.get_secret_value()
    )


def _unavailable(session: Session, box: SecretBox, settings: Settings) -> str | None:
    if settings.llm_provider != "claude_code":
        return f"{NAME} cần LLM_PROVIDER=claude_code."
    return None if _token(session, box, settings) else NOT_CONNECTED


def _over_budget(session: Session, settings: Settings) -> bool:
    from app.workers.knowledge_jobs import month_spend_usd  # lazy: the worker module is heavy

    budget = monthly_budget_usd(session, settings)
    return budget > 0 and month_spend_usd(session) >= budget


@router.get("/assistant/status")
def status(
    principal: AssistantPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
) -> dict[str, Any]:
    reason = _unavailable(session, box, settings)
    if reason is None and _over_budget(session, settings):
        reason = "Đã dùng hết ngân sách LLM tháng này — admin có thể nâng ở Admin → Cài đặt review."
    return {
        "name": NAME,
        "available": reason is None,
        "reason": reason,
        "models": MODELS,
        "default_model": str(effective(session, settings)["llm_model"]),
    }


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/assistant/chat")
def chat(
    body: ChatIn,
    principal: AssistantPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    streamer: Annotated[Streamer, Depends(get_assistant_streamer)],
    recorder: Annotated[Recorder, Depends(get_assistant_recorder)],
) -> StreamingResponse:
    question = body.question.strip()
    if not question:
        raise HTTPException(422, "Thiếu câu hỏi")
    if body.model and body.model not in {m["id"] for m in MODELS}:
        raise HTTPException(422, f"model: {' | '.join(m['id'] for m in MODELS)}")
    if (reason := _unavailable(session, box, settings)) is not None:
        raise HTTPException(503, reason)
    if _over_budget(session, settings):
        raise HTTPException(429, "Đã dùng hết ngân sách LLM tháng này")
    screen = (
        Screen(
            path=body.screen.path,
            search=body.screen.search if body.screen.search not in ("", "?") else "",
            title=" ".join(body.screen.title.split()),
        )
        if body.screen
        else None
    )
    if screen is not None and screen.search and not screen.search.startswith("?"):
        screen.search = f"?{screen.search}"
    user_id = principal.user_id
    with _busy_lock:
        if user_id in _busy:
            raise HTTPException(
                429, f"{NAME} đang trả lời câu hỏi trước của bạn — chờ xong hoặc bấm Dừng."
            )
        # Each turn is a `claude` process in the API container: a few at a time, for everyone.
        if not _slots.acquire(blocking=False):
            raise HTTPException(429, f"{NAME} đang bận trả lời người khác — thử lại sau ít phút.")
        _busy.add(user_id)

    def release() -> None:
        with _busy_lock:
            _busy.discard(user_id)
        _slots.release()

    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    stop = threading.Event()
    try:
        context = build_context(session, principal, settings, screen, question)
        model = body.model or str(effective(session, settings)["llm_model"])
        token = _token(session, box, settings)
        history = [Turn(t.role, t.content) for t in body.history]

        def work() -> None:
            outcome = ChatOutcome()
            try:
                _limiter().acquire()
                if stop.is_set():  # the asker left while the turn waited for its slot
                    outcome.cancelled = True
                else:
                    outcome = run_chat(
                        binary=settings.claude_code_bin,
                        model=model,
                        token=token,
                        context=context,
                        history=history,
                        question=question,
                        on_delta=lambda text: events.put(("delta", text)),
                        cancelled=stop.is_set,
                        streamer=streamer,
                    )
            except Exception as exc:
                log.exception("assistant turn failed")
                outcome.error = f"Lỗi máy chủ: {exc.__class__.__name__}"
            # Audited before the stream ends, so its cost is in the budget once the asker sees it.
            try:
                recorder(
                    user_id,
                    {
                        "status": "cancelled"
                        if outcome.cancelled
                        else ("ok" if outcome.ok else "error"),
                        "question": question[:300],
                        "turns": len(history),
                        "screen": f"{screen.path}{screen.search}" if screen else None,
                        "context_chars": len(context),
                        "model": outcome.model or model,
                        "cost_usd": outcome.cost_usd,
                        "cost_estimated": outcome.cost_estimated or None,
                        "duration_ms": outcome.duration_ms,
                        "error": outcome.error,
                    },
                )
            except Exception:
                log.exception("assistant turn not audited")
            finally:
                release()
                events.put(("end", outcome))

        # Started here, not in the stream: the turn always ends (and frees the asker) even when
        # the response body is never read.
        threading.Thread(target=work, name="assistant-turn", daemon=True).start()
    except BaseException:
        release()
        raise

    async def stream() -> AsyncIterator[str]:
        idle = 0.0
        try:
            while True:
                try:
                    kind, data = events.get_nowait()
                except queue.Empty:
                    # Polled, not a blocking get in a worker thread: an open stream holds no
                    # thread of the event loop's executor.
                    await asyncio.sleep(POLL_S)
                    idle += POLL_S
                    if idle >= HEARTBEAT_S:
                        idle = 0.0
                        yield ": ping\n\n"
                    continue
                idle = 0.0
                if kind == "delta":
                    yield _sse("delta", {"text": data})
                    continue
                outcome: ChatOutcome = data
                if outcome.ok:
                    yield _sse(
                        "done",
                        {
                            "model": outcome.model,
                            "cost_usd": outcome.cost_usd,
                            "duration_ms": outcome.duration_ms,
                        },
                    )
                elif not outcome.cancelled:
                    yield _sse("error", {"error": outcome.error or f"{NAME} không trả lời được"})
                return
        finally:
            # The asker went away (drawer closed, "Dừng", tab closed): the CLI is killed on
            # its next line, so the turn stops costing.
            stop.set()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
