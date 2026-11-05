"""/mcp over HTTP (docs/specs/26 §1, §9): bearer token or 401, Origin, exactly at /mcp (no
redirect: the dashboard proxies it), and the SDK's own client end to end."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import Implementation

from app.api.main import app
from app.mcp.server import mcp_http
from app.mcp.tokens import SCOPE_READ
from app.models import ApiToken, FindingClosure, McpCall
from tests.mcp.conftest import Env, make_token

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "claude-code", "version": "2.1.0"},
    },
}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def http(env: Env) -> Iterator[TestClient]:
    with TestClient(app, base_url="https://codepulse.example.com") as client:
        yield client


def _auth(raw: str, **extra: str) -> dict[str, str]:
    return {**HEADERS, "Authorization": f"Bearer {raw}", **extra}


def test_401_without_a_valid_token(env: Env, world: Any, http: TestClient) -> None:
    for headers in (
        HEADERS,
        {**HEADERS, "Authorization": "Bearer nope"},
        {**HEADERS, "Authorization": "Bearer cpat_unknown"},
    ):
        response = http.post("/mcp", json=INIT, headers=headers)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"].startswith("Bearer")
    raw = make_token(env.session, world.an)
    token = env.session.query(ApiToken).one()
    token.revoked_at = datetime.now(UTC)
    env.session.commit()
    response = http.post("/mcp", json=INIT, headers=_auth(raw))
    assert response.status_code == 401 and "thu hồi" in response.json()["detail"]
    assert raw not in response.text


def test_answers_at_exactly_mcp(env: Env, world: Any, http: TestClient) -> None:
    raw = make_token(env.session, world.an)
    response = http.post("/mcp", json=INIT, headers=_auth(raw), follow_redirects=False)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "codepulse"
    tools = http.post("/mcp", json=LIST, headers=_auth(raw), follow_redirects=False)
    assert tools.status_code == 200
    assert {t["name"] for t in tools.json()["result"]["tools"]} == {
        "list_findings",
        "get_commit_findings",
        "get_mr_findings",
        "get_finding",
        "request_closure",
        "list_close_requests",
        "cancel_close_request",
    }


def test_origin(env: Env, world: Any, http: TestClient) -> None:
    raw = make_token(env.session, world.an)
    foreign = http.post("/mcp", json=INIT, headers=_auth(raw, Origin="https://evil.example"))
    assert foreign.status_code == 403
    for origin in ("https://dash.example.com", "https://codepulse.example.com"):
        ok = http.post("/mcp", json=INIT, headers=_auth(raw, Origin=origin))
        assert ok.status_code == 200, origin


def test_tool_call_over_http_scopes_and_audit(env: Env, world: Any, http: TestClient) -> None:
    raw = make_token(env.session, world.an, (SCOPE_READ,))
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "request_closure", "arguments": {"finding_id": world.an_finding.id}},
    }
    response = http.post("/mcp", json=call, headers=_auth(raw))
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "forbidden"


@pytest.mark.anyio
async def test_sdk_client_end_to_end(env: Env, world: Any) -> None:
    raw = make_token(env.session, world.an)
    transport = httpx2.ASGITransport(app=app)
    async with (
        mcp_http.lifespan(),
        httpx2.AsyncClient(
            transport=transport,
            base_url="https://codepulse.example.com",
            headers={"Authorization": f"Bearer {raw}"},
        ) as http_client,
        Client(
            streamable_http_client("https://codepulse.example.com/mcp", http_client=http_client),
            mode="legacy",
            client_info=Implementation(name="claude-code", version="2.1.0"),
        ) as client,
    ):
        tools = await client.list_tools()
        assert "list_findings" in {t.name for t in tools.tools}
        result = await client.call_tool("list_findings", {"limit": 5})
        assert not result.is_error, result
        assert result.structured_content is not None
        assert [f["id"] for f in result.structured_content["findings"]] == [world.an_finding.id]
        assert "BEGIN-FINDING-" in result.content[0].text  # type: ignore[union-attr]


ARGUMENT = (
    "Giá trị v chỉ đến từ enum nội bộ đã kiểm ở app/schema.py, không bao giờ từ người dùng, "
    "nên không thể tiêm SQL ở môi trường thật."
)


@pytest.mark.anyio
async def test_sdk_client_modern_mode_sends_client_info(env: Env, world: Any) -> None:
    """A 2026-era client sends its name/version with each request: kept in meta.client."""
    raw = make_token(env.session, world.an)
    async with (
        mcp_http.lifespan(),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="https://codepulse.example.com",
            headers={"Authorization": f"Bearer {raw}"},
        ) as http_client,
        Client(
            streamable_http_client("https://codepulse.example.com/mcp", http_client=http_client),
            client_info=Implementation(name="claude-code", version="2.1.0"),
        ) as client,
    ):
        result = await client.call_tool(
            "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT}
        )
        assert not result.is_error, result
    closure = env.session.query(FindingClosure).one()
    assert closure.meta is not None and closure.meta.get("client") == "claude-code 2.1.0"
    call = env.session.query(McpCall).filter(McpCall.tool == "request_closure").one()
    assert call.ok and call.client == "claude-code 2.1.0" and call.closure_id == closure.id
