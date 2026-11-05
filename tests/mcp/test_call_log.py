"""The MCP call log (docs/specs/26 §13.1): one `mcp_calls` row per tool call, refusals and
rate_limited included, with what the call touched, and never a failed call because of it."""

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.mcp import server as mcp_server
from app.mcp.server import RATE_PER_MINUTE
from app.mcp.tokens import SCOPE_READ
from app.models import AuditLog, FindingClosure, McpCall
from tests.mcp.conftest import Env, caller_for, invoke

FIX = "c" * 40
GITLAB = "https://gitlab.example.com/api/v4/projects/1001/repository/commits"
NOTE = "Đã tham số hoá câu truy vấn ở app/a.py, bỏ nối chuỗi, thêm test cho đầu vào có dấu nháy."
ARGUMENT = (
    "Giá trị v chỉ đến từ enum nội bộ đã kiểm ở app/schema.py, không bao giờ từ người dùng, "
    "nên không thể tiêm SQL ở môi trường thật."
)


def _calls(env: Env) -> list[McpCall]:
    return list(env.session.scalars(select(McpCall).order_by(McpCall.id)))


def test_read_tools_record_what_they_returned(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an)
    assert not invoke(an, "list_findings").is_error
    assert not invoke(an, "list_findings", {"repo": "demo/p"}).is_error
    assert not invoke(an, "get_finding", {"id": world.an_finding.id}).is_error
    assert not invoke(an, "get_mr_findings", {"repo": "demo/p", "iid": 7}).is_error
    assert not invoke(an, "list_close_requests").is_error
    listed, by_repo, got, mr, requests = _calls(env)
    assert (listed.tool, listed.ok, listed.error_code) == ("list_findings", True, None)
    assert listed.user_id == world.an.id and listed.token_id == an.token_id
    assert listed.result_count == 1 and listed.project_id is None  # no repo asked
    assert by_repo.project_id == world.project.id and by_repo.result_count == 1
    assert (got.tool, got.finding_id, got.project_id, got.result_count) == (
        "get_finding",
        world.an_finding.id,
        world.project.id,
        1,
    )
    assert (mr.project_id, mr.result_count) == (world.project.id, 1)
    assert requests.result_count == 0
    assert all(c.duration_ms >= 0 and c.created_at is not None for c in _calls(env))


def test_refusals_are_recorded_with_their_code(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an, scopes=(SCOPE_READ,))
    assert invoke(an, "get_finding", {"id": 999_999}).is_error
    assert invoke(
        an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT}
    ).is_error
    assert invoke(an, "cancel_close_request", {"id": 42}).is_error
    missing, no_scope, cancel = _calls(env)
    assert (missing.ok, missing.error_code, missing.finding_id) == (False, "not_found", 999_999)
    # Refused before the work ran: what the arguments said is still there.
    assert (no_scope.error_code, no_scope.finding_id) == ("forbidden", world.an_finding.id)
    assert (cancel.error_code, cancel.closure_id) == ("forbidden", 42)


@respx.mock
def test_request_closure_records_the_request_and_commit(env: Env, world: Any) -> None:
    respx.get(f"{GITLAB}/{FIX[:8]}").mock(
        return_value=httpx.Response(200, json={"id": FIX, "short_id": FIX[:8]})
    )
    respx.get(f"{GITLAB}/{'d' * 8}").mock(return_value=httpx.Response(404, json={}))
    an = caller_for(env.session, env.settings, world.an)
    pushed = {
        "finding_id": world.an_finding.id,
        "note": NOTE,
        "files_changed": ["app/a.py"],
        "tests": [{"command": "pytest", "result": "passed"}],
    }
    assert invoke(an, "request_closure", {**pushed, "commits": ["d" * 8]}).is_error
    result = invoke(an, "request_closure", {**pushed, "commits": [FIX[:8]]})
    assert not result.is_error, result
    closure = env.session.scalars(select(FindingClosure)).one()
    not_pushed, sent = _calls(env)
    assert (not_pushed.error_code, not_pushed.commit_sha) == ("commit_not_pushed", "d" * 8)
    assert not_pushed.project_id == world.project.id and not_pushed.closure_id is None
    assert (sent.ok, sent.closure_id, sent.finding_id) == (True, closure.id, world.an_finding.id)
    assert sent.commit_sha == FIX and sent.project_id == world.project.id  # as GitLab knows it
    cancelled = invoke(an, "cancel_close_request", {"id": closure.id})
    assert not cancelled.is_error
    last = _calls(env)[-1]
    assert (last.tool, last.closure_id, last.finding_id, last.project_id) == (
        "cancel_close_request",
        closure.id,
        world.an_finding.id,
        world.project.id,
    )


def test_rate_limited_calls_are_recorded_but_not_audited(env: Env, world: Any) -> None:
    # The lead reads an's finding: someone else's work, so each read is audited.
    lead = caller_for(env.session, env.settings, world.lead)
    for _ in range(RATE_PER_MINUTE + 1):
        invoke(lead, "get_finding", {"id": world.an_finding.id})
    calls = _calls(env)
    assert len(calls) == RATE_PER_MINUTE + 1
    assert calls[-1].error_code == "rate_limited" and not calls[-1].ok
    audited = env.session.scalars(
        select(AuditLog).where(AuditLog.action == "mcp.get_finding")
    ).all()
    assert len(audited) == RATE_PER_MINUTE


def test_a_failing_log_never_fails_the_call(
    env: Env, world: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lead = caller_for(env.session, env.settings, world.lead)  # audited: another's finding
    real = mcp_server.McpCall

    def broken(**values: Any) -> McpCall:
        return real(**{**values, "tool": None})  # NOT NULL: the insert fails at commit

    monkeypatch.setattr(mcp_server, "McpCall", broken)
    result = invoke(lead, "get_finding", {"id": world.an_finding.id})
    assert not result.is_error, result
    assert _calls(env) == []
    assert "mcp call log failed" in caplog.text
    # The call's own work (its audit row) was committed before the log was tried.
    assert env.session.scalars(select(AuditLog).where(AuditLog.action == "mcp.get_finding")).one()
