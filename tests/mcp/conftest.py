"""Fixtures of the MCP server tests (docs/specs/26): the API tests' world, a token, and the
server's dependencies pointed at the test session, a fake Redis and GitLab (respx)."""

from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import anyio
import pytest
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.config import Settings
from app.mcp import server as mcp_server
from app.mcp.server import Caller, McpDeps, _caller
from app.mcp.tokens import SCOPE_CLOSE, SCOPE_READ, authenticate, create_token
from app.models import User
from tests.api.conftest import BOX, FakeQueue, settings, world  # noqa: F401 - fixtures


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    def incr(self, name: str) -> int:
        self.counts[name] = self.counts.get(name, 0) + 1
        return self.counts[name]

    def expire(self, name: str, time: int) -> bool:
        self.ttl[name] = time
        return True


@dataclass
class Env:
    session: Session
    settings: Settings
    redis: FakeRedis
    clock: list[float]
    queue: FakeQueue


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def env(
    db_session: Session,
    settings: Settings,  # noqa: F811
    world: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Env]:
    # The world is committed into the test's outer transaction (still rolled back at the end):
    # a refused tool call rolls its own work back and must not take the world with it.
    db_session.commit()
    redis = FakeRedis()
    clock = [1_800_000_000.0]
    queue = FakeQueue()
    monkeypatch.setattr(
        mcp_server,
        "deps",
        McpDeps(
            sessions=lambda: nullcontext(db_session),
            settings=lambda: settings,
            redis=lambda: redis,
            gitlab=lambda c: GitLabClient(
                c.base_url, BOX.decrypt(c.token_encrypted), max_retries=0
            ),
            clock=lambda: clock[0],
            queue=lambda: queue,
        ),
    )
    yield Env(session=db_session, settings=settings, redis=redis, clock=clock, queue=queue)


def make_token(
    session: Session, user: User, scopes: tuple[str, ...] = (SCOPE_READ, SCOPE_CLOSE)
) -> str:
    _token, raw = create_token(session, user.id, "laptop", list(scopes))
    session.commit()
    return raw


def caller_for(
    session: Session,
    settings: Settings,  # noqa: F811
    user: User,
    scopes: tuple[str, ...] = (SCOPE_READ, SCOPE_CLOSE),
) -> Caller:
    auth = authenticate(session, make_token(session, user, scopes), settings)
    return Caller(
        token_id=auth.token.id,
        token_name=auth.token.name,
        scopes=tuple(auth.token.scopes),
        principal=auth.principal,
    )


def invoke(caller: Caller, name: str, args: dict[str, Any] | None = None) -> Any:
    """One tools/call through the SDK's tool manager (argument schema included), as `caller`."""
    server = mcp_server.mcp_http.server

    async def run() -> Any:
        return await server.call_tool(name, args or {})

    token = _caller.set(caller)
    try:
        return anyio.run(run)
    finally:
        _caller.reset(token)
