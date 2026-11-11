"""Review on request from Claude Code (docs/specs/26 §22): request_review and
get_review_status, against the fake GitLab and the API tests' world."""

from typing import Any

import httpx
import respx
from sqlalchemy import select

from app.mcp.tokens import SCOPE_CLOSE, SCOPE_READ, SCOPE_REVIEW
from app.models import AppSetting, AuditLog, McpCall, Review
from tests.api.test_commits import A, B, C, gitlab, link_accounts
from tests.mcp.conftest import Env, caller_for, invoke

ALL = (SCOPE_READ, SCOPE_CLOSE, SCOPE_REVIEW)


def _error(result: Any) -> tuple[str, str]:
    assert result.is_error, result
    err = result.structured_content["error"]
    return err["code"], err["message"]


def _review(env: Env, sha: str) -> Review | None:
    return env.session.scalars(
        select(Review).where(Review.commit_sha == sha).order_by(Review.id.desc())
    ).first()


def test_pushed_commits_are_queued_as_mcp_reviews(env: Env, world: Any) -> None:
    link_accounts(env.session, world)
    env.session.commit()
    an = caller_for(env.session, env.settings, world.an, ALL)
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        result = invoke(
            an,
            "request_review",
            {"repo": world.project.path_with_namespace, "commits": [A, C, A.upper()]},
        )
    assert not result.is_error, result
    data = result.structured_content
    row = _review(env, A)
    assert row is not None
    assert (row.status, row.trigger, row.requested_by_user_id, row.author_user_id) == (
        "queued",
        "mcp",
        world.an.id,
        world.an.id,
    )
    assert data["queued"] == [
        {"sha": A, "review_id": row.id, "again": False, "link": f"{data['queued'][0]['link']}"}
    ]
    assert data["queued"][0]["link"].endswith(f"/reviews/{row.id}")
    assert [(s["sha"], s["reason"]) for s in data["skipped"]] == [(C, "merge commit")]
    assert data["next"] == {"tool": "get_review_status", "review_ids": [row.id]}
    assert data["quota"] == {"limit": 30, "used": 1}
    # Served first (a person waits), under the dashboard's job id for the commit.
    func, args, kwargs = env.queue.jobs[-1]
    assert func == "app.workers.review_jobs.review_commit_job"
    assert args == (world.project.id, A)
    assert kwargs["trigger"] == "mcp" and kwargs["requested_by"] == world.an.id
    assert kwargs["job_id"] == f"review-commit-{A[:12]}"
    text = result.content[0].text
    assert "get_review_status" in text and f"review #{row.id}" in text
    call = env.session.scalars(select(McpCall).where(McpCall.tool == "request_review")).one()
    assert (call.ok, call.project_id, call.commit_sha, call.result_count) == (
        True,
        world.project.id,
        A,
        1,
    )
    assert env.session.scalars(
        select(AuditLog).where(AuditLog.action == "mcp.request_review")
    ).one()

    # Asked again: nothing new, the existing review is pointed at.
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        twice = invoke(an, "request_review", {"repo": str(world.project.id), "commits": [A]})
    assert twice.structured_content["queued"] == []
    assert twice.structured_content["skipped"][0]["review_id"] == row.id
    assert twice.structured_content["skipped"][0]["status"] == "queued"


def test_a_commit_not_on_gitlab_queues_nothing(env: Env, world: Any) -> None:
    link_accounts(env.session, world)
    env.session.commit()
    an = caller_for(env.session, env.settings, world.an, ALL)
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        code, message = _error(
            invoke(
                an,
                "request_review",
                {"repo": world.project.path_with_namespace, "commits": [A, "e" * 40]},
            )
        )
    assert code == "commit_not_pushed" and "git push" in message
    assert _review(env, A) is None and env.queue.jobs == []


def test_a_branch_named_like_a_sha_is_not_a_commit(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an, ALL)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/repository/commits/deadbeef.*").mock(
            return_value=httpx.Response(200, json={"id": A})
        )
        code, _ = _error(
            invoke(
                an,
                "request_review",
                {"repo": world.project.path_with_namespace, "commits": ["deadbeef"]},
            )
        )
    assert code == "invalid_argument"


def test_scope_permission_repo_and_arguments(env: Env, world: Any) -> None:
    old = caller_for(env.session, env.settings, world.an, (SCOPE_READ, SCOPE_CLOSE))
    code, message = _error(
        invoke(old, "request_review", {"repo": str(world.project.id), "commits": [A]})
    )
    assert code == "forbidden" and "Yêu cầu review" in message
    an = caller_for(env.session, env.settings, world.an, ALL)
    code, _ = _error(invoke(an, "request_review", {"repo": str(world.project.id), "commits": []}))
    assert code == "invalid_argument"
    code, _ = _error(
        invoke(an, "request_review", {"repo": str(world.project.id), "commits": ["not-a-sha"]})
    )
    assert code == "invalid_argument"
    # Someone else's commit: the dashboard's rule, per commit (nothing queued, nothing spent).
    link_accounts(env.session, world)
    env.session.commit()
    binh = caller_for(env.session, env.settings, world.binh, ALL)
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        other = invoke(binh, "request_review", {"repo": str(world.project.id), "commits": [A]})
    assert not other.is_error, other
    assert other.structured_content["queued"] == []
    assert other.structured_content["skipped"][0]["reason"] == "không thuộc quyền review của bạn"
    assert env.queue.jobs == []
    world.project.review_enabled = False
    env.session.commit()
    code, _ = _error(invoke(an, "request_review", {"repo": str(world.project.id), "commits": [A]}))
    assert code == "review_disabled"


def test_the_daily_mcp_quota(env: Env, world: Any) -> None:
    link_accounts(env.session, world)
    env.session.add(AppSetting(key="mcp_reviews_per_day", value=1))
    env.session.commit()
    an = caller_for(env.session, env.settings, world.an, ALL)
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        code, message = _error(
            invoke(an, "request_review", {"repo": str(world.project.id), "commits": [A, B]})
        )
        assert code == "limit_reached" and "0/1" in message
        ok = invoke(an, "request_review", {"repo": str(world.project.id), "commits": [A]})
        assert not ok.is_error
        code, _ = _error(
            invoke(an, "request_review", {"repo": str(world.project.id), "commits": [B]})
        )
    assert code == "limit_reached"


def test_status_by_id_and_by_commit(env: Env, world: Any) -> None:
    an = caller_for(env.session, env.settings, world.an, ALL)
    review = world.an_review
    by_id = invoke(an, "get_review_status", {"review_ids": [review.id]})
    assert not by_id.is_error, by_id
    item = by_id.structured_content["reviews"][0]
    assert item["id"] == review.id and item["status"] == review.status
    assert item["link"].endswith(f"/reviews/{review.id}")
    if review.status == "done":
        assert item["findings"] is not None and item["finished"]
    code, _ = _error(invoke(an, "get_review_status", {}))
    assert code == "invalid_argument"
    code, _ = _error(invoke(an, "get_review_status", {"review_ids": [10**9]}))
    assert code == "not_found"
    outsider = caller_for(env.session, env.settings, world.outsider, ALL)
    code, _ = _error(invoke(outsider, "get_review_status", {"review_ids": [review.id]}))
    assert code in ("not_found", "forbidden")


def test_status_follows_a_requested_review(env: Env, world: Any) -> None:
    link_accounts(env.session, world)
    env.session.commit()
    an = caller_for(env.session, env.settings, world.an, ALL)
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        invoke(an, "request_review", {"repo": str(world.project.id), "commits": [A]})
    status = invoke(an, "get_review_status", {"commits": [A[:10]]})
    assert not status.is_error, status
    data = status.structured_content
    assert data["reviews"][0]["status"] == "queued" and data["pending"] == [
        data["reviews"][0]["id"]
    ]
    assert "gọi lại sau" in status.content[0].text
    row = _review(env, A)
    assert row is not None
    row.status = "done"
    env.session.commit()
    done = invoke(an, "get_review_status", {"review_ids": [row.id]}).structured_content
    assert done["pending"] == [] and done["reviews"][0]["findings"] == {}
