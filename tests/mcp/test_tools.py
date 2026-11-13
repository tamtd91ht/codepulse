"""The MCP tools that write (docs/specs/26 §6, §8, §9): request_closure in every branch,
cancel_close_request, list_close_requests, scopes, the per-token rate limit, the audit."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.mcp import server as mcp_server
from app.mcp.server import RATE_PER_MINUTE, rate_wait
from app.mcp.tokens import SCOPE_READ
from app.models import AuditLog, FindingClosure
from tests.mcp.conftest import Env, FakeRedis, caller_for, invoke

FIX = "c" * 40
GITLAB = "https://gitlab.example.com/api/v4/projects/1001/repository/commits"
NOTE = "Đã tham số hoá câu truy vấn ở app/a.py, bỏ nối chuỗi, thêm test cho đầu vào có dấu nháy."
ARGUMENT = (
    "Giá trị v chỉ đến từ enum nội bộ đã kiểm ở app/schema.py, không bao giờ từ người dùng, "
    "nên không thể tiêm SQL ở môi trường thật."
)
TESTS = [{"command": "pytest -q tests/test_a.py", "result": "passed", "summary": "12 passed"}]


def _error(result: Any) -> tuple[str, str]:
    assert result.is_error, result
    err = result.structured_content["error"]
    return err["code"], err["message"]


def _requests(env: Env) -> list[FindingClosure]:
    return list(env.session.scalars(select(FindingClosure).order_by(FindingClosure.id)))


@respx.mock
def test_request_with_pushed_commit(env: Env, world: Any) -> None:
    route = respx.get(f"{GITLAB}/{FIX[:8]}").mock(
        return_value=httpx.Response(200, json={"id": FIX, "short_id": FIX[:8]})
    )
    caller = caller_for(env.session, env.settings, world.an)
    result = invoke(
        caller,
        "request_closure",
        {
            "finding_id": world.an_finding.id,
            "commits": [FIX[:8]],
            "note": NOTE,
            "files_changed": ["app/a.py", "tests/test_a.py", "app/a.py"],
            "tests": TESTS,
        },
    )
    assert not result.is_error, result
    assert route.called
    [closure] = _requests(env)
    # Checked by the system at once (user 2026-11-13): no decider, the job queued.
    assert closure.status == "checking" and closure.via == "mcp"
    assert closure.decided_by is None and closure.run_token
    [(job, args, _kwargs)] = env.queue.jobs
    assert job == "app.workers.closure_jobs.close_request_job"
    assert args == (closure.id, closure.run_token)
    assert closure.shas == [FIX]  # as GitLab knows it
    assert closure.note == NOTE
    assert closure.meta == {
        "files_changed": ["app/a.py", "tests/test_a.py"],
        "tests": TESTS,
        "token_id": caller.token_id,
        "token_name": "laptop",
    }
    data = result.structured_content
    assert data["id"] == closure.id and data["status"] == "checking"
    assert "tự chạy review" in result.content[0].text
    assert data["closures_page"] == "https://dash.example.com/closures"
    actions = list(
        env.session.scalars(select(AuditLog.action).where(AuditLog.actor_user_id == world.an.id))
    )
    assert "finding.close_request" in actions and "mcp.request_closure" in actions
    row = env.session.scalars(
        select(AuditLog).where(AuditLog.action == "mcp.request_closure")
    ).one()
    assert row.details == {"token_id": caller.token_id}


@respx.mock
def test_commit_not_on_gitlab(env: Env, world: Any) -> None:
    respx.get(f"{GITLAB}/{FIX}").mock(return_value=httpx.Response(404, json={}))
    caller = caller_for(env.session, env.settings, world.an)
    result = invoke(
        caller,
        "request_closure",
        {
            "finding_id": world.an_finding.id,
            "commits": [FIX],
            "note": NOTE,
            "files_changed": ["app/a.py"],
        },
    )
    code, message = _error(result)
    assert code == "commit_not_pushed"
    assert FIX[:12] in message and "git push" in message
    assert _requests(env) == []
    refused = env.session.scalars(
        select(AuditLog).where(AuditLog.action == "mcp.request_closure")
    ).one()
    assert refused.details == {"token_id": caller.token_id, "error": "commit_not_pushed"}


@respx.mock(assert_all_called=False)
def test_commits_need_note_and_files(env: Env, world: Any, respx_mock: Any = None) -> None:
    route = respx.get(f"{GITLAB}/{FIX}").mock(return_value=httpx.Response(200, json={"id": FIX}))
    caller = caller_for(env.session, env.settings, world.an)
    base = {"finding_id": world.an_finding.id, "commits": [FIX]}
    code, message = _error(invoke(caller, "request_closure", {**base, "files_changed": ["a"]}))
    assert code == "invalid_argument" and "note" in message
    code, message = _error(
        invoke(caller, "request_closure", {**base, "note": "ngắn quá", "files_changed": ["a"]})
    )
    assert code == "invalid_argument" and "note" in message
    code, message = _error(invoke(caller, "request_closure", {**base, "note": NOTE}))
    assert code == "invalid_argument" and "files_changed" in message
    many = [f"f{n}.py" for n in range(51)]
    code, _ = _error(
        invoke(caller, "request_closure", {**base, "note": NOTE, "files_changed": many})
    )
    assert code == "invalid_argument"
    code, _ = _error(
        invoke(
            caller,
            "request_closure",
            {**base, "note": NOTE, "files_changed": ["a"], "tests": TESTS * 11},
        )
    )
    assert code == "invalid_argument"
    assert not route.called  # nothing asked of GitLab before the input is right
    assert _requests(env) == []


def test_argument_only(env: Env, world: Any) -> None:
    caller = caller_for(env.session, env.settings, world.an)
    code, message = _error(invoke(caller, "request_closure", {"finding_id": world.an_finding.id}))
    assert code == "invalid_argument" and "phản biện" in message
    code, message = _error(
        invoke(
            caller,
            "request_closure",
            {"finding_id": world.an_finding.id, "argument": "sai vì không đúng"},
        )
    )
    assert code == "invalid_argument" and "ít nhất 50" in message
    secret = ARGUMENT + " mật khẩu prod là Xk9#pq2LmZ"
    code, message = _error(
        invoke(caller, "request_closure", {"finding_id": world.an_finding.id, "argument": secret})
    )
    assert code == "invalid_argument" and "khoá/mật khẩu" in message
    result = invoke(
        caller, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT}
    )
    assert not result.is_error, result
    [closure] = _requests(env)
    assert closure.argument == ARGUMENT and closure.shas == [] and closure.via == "mcp"
    assert closure.meta is not None and closure.meta["files_changed"] == []


def test_only_the_author_and_one_pending(env: Env, world: Any) -> None:
    binh = caller_for(env.session, env.settings, world.binh)
    code, message = _error(
        invoke(binh, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT})
    )
    # binh may not even see an's review; the lead sees it but is not the author
    assert code == "forbidden"
    lead = caller_for(env.session, env.settings, world.lead)
    code, message = _error(
        invoke(lead, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT})
    )
    assert code == "forbidden" and "tác giả" in message
    an = caller_for(env.session, env.settings, world.an)
    args = {"finding_id": world.an_finding.id, "argument": ARGUMENT}
    assert not invoke(an, "request_closure", args).is_error
    code, message = _error(invoke(an, "request_closure", args))
    assert code == "conflict" and "đang chờ" in message


def test_twenty_a_day_shared_with_the_web(env: Env, world: Any) -> None:
    for _ in range(20):
        env.session.add(
            FindingClosure(
                finding_id=world.an_finding.id,
                kind="request",
                status="cancelled",
                requested_by=world.an.id,
                created_at=datetime.now(UTC),
            )
        )
    env.session.commit()
    an = caller_for(env.session, env.settings, world.an)
    code, message = _error(
        invoke(an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT})
    )
    assert code == "limit_reached" and "20" in message


def test_read_only_token_cannot_write(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an, scopes=(SCOPE_READ,))
    code, message = _error(
        invoke(an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT})
    )
    assert code == "forbidden" and "findings:close_request" in message
    code, _ = _error(invoke(an, "cancel_close_request", {"id": 1}))
    assert code == "forbidden"
    assert not invoke(an, "list_findings").is_error
    assert _requests(env) == []


def test_cancel_and_list(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an)
    created = invoke(
        an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT}
    )
    closure_id = created.structured_content["id"]
    listed = invoke(an, "list_close_requests", {"status": "checking"})
    assert [r["id"] for r in listed.structured_content["requests"]] == [closure_id]
    assert listed.structured_content["requests"][0]["finding"]["repo"] == "demo/p"
    assert invoke(an, "list_close_requests", {"status": "closed"}).structured_content["count"] == 0
    code, _ = _error(invoke(an, "list_close_requests", {"status": "bogus"}))
    assert code == "invalid_argument"
    lead = caller_for(env.session, env.settings, world.lead)
    code, message = _error(invoke(lead, "cancel_close_request", {"id": closure_id}))
    assert code == "forbidden" and "người gửi" in message
    result = invoke(an, "cancel_close_request", {"id": closure_id})
    assert not result.is_error and result.structured_content["status"] == "cancelled"
    code, _ = _error(invoke(an, "cancel_close_request", {"id": closure_id}))
    assert code == "conflict"
    code, _ = _error(invoke(an, "cancel_close_request", {"id": 999_999}))
    assert code == "not_found"
    cancel = env.session.scalars(
        select(AuditLog).where(AuditLog.action == "finding.close_request.cancel")
    ).one()
    assert cancel.details == {"via": "mcp"}


def test_get_finding_shows_the_close_history(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an)
    invoke(an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT})
    result = invoke(an, "get_finding", {"id": world.an_finding.id})
    [closure] = result.structured_content["closures"]
    assert closure["status"] == "checking" and closure["via"] == "mcp"
    assert "Lịch sử đóng (1)" in result.content[0].text


class _DownQueue:
    def enqueue(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("redis down")


def test_auto_check_not_queued_leaves_the_request_pending(
    env: Env, world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The request is saved even when its check cannot be queued: a decider runs it by hand.
    monkeypatch.setattr(mcp_server.deps, "queue", lambda: _DownQueue())
    an = caller_for(env.session, env.settings, world.an)
    result = invoke(
        an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT}
    )
    assert not result.is_error, result
    [closure] = _requests(env)
    env.session.refresh(closure)
    assert closure.status == "pending" and closure.run_token is None
    assert result.structured_content["status"] == "pending"
    assert "Manager/admin sẽ duyệt" in result.content[0].text


def test_auto_check_is_audited_without_an_actor(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an)
    invoke(an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT})
    [closure] = _requests(env)
    run = env.session.scalars(
        select(AuditLog).where(AuditLog.action == "finding.close_request.run")
    ).one()
    assert run.actor_user_id is None
    assert run.details == {"closure_id": closure.id, "auto": True, "via": "mcp"}


def test_any_auto_start_failure_still_returns_the_saved_request(
    env: Env, world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> Any:
        raise RuntimeError("no redis url")

    monkeypatch.setattr(mcp_server.deps, "queue", broken)
    an = caller_for(env.session, env.settings, world.an)
    result = invoke(
        an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT}
    )
    assert not result.is_error, result
    assert result.structured_content["status"] == "pending"
    assert env.session.scalars(
        select(AuditLog).where(AuditLog.action == "mcp.request_closure")
    ).one()


def test_no_auto_check_once_the_month_budget_is_spent(
    env: Env, world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers import knowledge_jobs

    monkeypatch.setattr(mcp_server, "monthly_budget_usd", lambda session, settings: 10.0)
    monkeypatch.setattr(knowledge_jobs, "month_spend_usd", lambda session, now: 10.0)
    an = caller_for(env.session, env.settings, world.an)
    result = invoke(
        an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT}
    )
    assert not result.is_error, result
    assert result.structured_content["status"] == "pending"
    assert env.queue.jobs == []


def test_sender_may_not_withdraw_a_run_a_decider_started(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an)
    created = invoke(
        an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT}
    )
    closure = env.session.get(FindingClosure, created.structured_content["id"])
    assert closure is not None
    closure.decided_by = world.lead.id  # a decider ran it again meanwhile
    env.session.commit()
    code, _ = _error(invoke(an, "cancel_close_request", {"id": closure.id}))
    assert code == "conflict"
    env.session.refresh(closure)
    assert closure.status == "checking"


def test_sender_may_withdraw_a_check_the_system_started(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an)
    created = invoke(
        an, "request_closure", {"finding_id": world.an_finding.id, "argument": ARGUMENT}
    )
    assert created.structured_content["status"] == "checking"
    result = invoke(an, "cancel_close_request", {"id": created.structured_content["id"]})
    assert not result.is_error, result
    assert result.structured_content["status"] == "cancelled"


def test_rate_limit_per_token_per_minute(env: Env, world: Any) -> None:
    store = FakeRedis()
    now = 1_800_000_030.0
    assert all(rate_wait(store, 7, now) is None for _ in range(RATE_PER_MINUTE))
    assert rate_wait(store, 7, now) == 30
    assert rate_wait(store, 8, now) is None  # another token
    assert rate_wait(store, 7, now + 30) is None  # the next minute
    key = f"cp:mcp:rate:7:{int(now // 60)}"
    assert store.counts[key] == RATE_PER_MINUTE + 1 and store.ttl[key] == 120

    an = caller_for(env.session, env.settings, world.an)
    for _ in range(RATE_PER_MINUTE):
        assert not invoke(an, "list_close_requests").is_error
    code, message = _error(invoke(an, "list_close_requests"))
    assert code == "rate_limited" and "giây" in message
    env.clock[0] += 60
    assert not invoke(an, "list_close_requests").is_error
