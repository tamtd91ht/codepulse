"""Requests to close critical/high findings (author asks, a manager/admin runs the check or
refuses), admin close/reopen, the "Đóng finding" page, the auto-close switch."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers.closures import parse_commit
from app.collector.gitlab.client import GitLabError
from app.config import Settings
from app.models import AuditLog, FindingClosure, ProjectGrant, Review, TeamMember, User
from app.review.providers import FakeLLMProvider
from app.workers.closure_jobs import run_close_request
from app.workers.knowledge_jobs import month_spend_breakdown
from tests.api.conftest import FakeQueue, World

FIX = "c" * 40
LINK = f"https://gitlab.example.com/demo/p/-/commit/{FIX}"
DIFF = {
    "old_path": "app/a.py",
    "new_path": "app/a.py",
    "new_file": False,
    "renamed_file": False,
    "deleted_file": False,
    "diff": "@@ -3,1 +3,1 @@\n-q = 'x' + v\n+q = bind(v)\n",
}
FIXED = {
    "index": 0,
    "verdict": "fixed",
    "reason": "Đã tham số hoá.",
    "evidence_file": "app/a.py",
    "evidence_lines": "3",
    "confidence": 0.9,
}


class FakeClient:
    def __init__(self, committed: str = "2026-10-20T10:00:00+00:00") -> None:
        self.committed = committed

    def commit(self, project_id: int, sha: str) -> dict[str, Any]:
        if sha != FIX:
            raise GitLabError("404")
        return {
            "id": sha,
            "committed_date": self.committed,
            "authored_date": "2020-01-01T00:00:00Z",
        }

    def commit_diff(self, project_id: int, sha: str) -> list[dict[str, Any]]:
        return [DIFF]


def factory(client: FakeClient) -> Any:
    @contextmanager
    def make(connection: Any) -> Iterator[FakeClient]:
        yield client

    return make


def ask(client: TestClient, world: World, as_user: Any, who: Any, commits: list[str]) -> Any:
    return client.post(
        f"/api/v1/findings/{world.an_finding.id}/close-requests",
        json={"commits": commits},
        headers=as_user(who),
    )


def check(
    db_session: Session,
    settings: Settings,
    closure_id: int,
    answer: dict[str, Any],
    gitlab: FakeClient | None = None,
) -> str:
    """What the worker does once a manager pressed "Chạy review" (with that run's token)."""
    return work(db_session, settings, closure_id, token_of(db_session, closure_id), answer, gitlab)


def token_of(db_session: Session, closure_id: int) -> str:
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None
    db_session.refresh(closure)
    assert closure.run_token is not None
    return closure.run_token


def work(
    db_session: Session,
    settings: Settings,
    closure_id: int,
    token: str,
    answer: Any,
    gitlab: FakeClient | None = None,
) -> str:
    provider = FakeLLMProvider(
        lambda p: answer if isinstance(answer, str) else {"answers": [answer]}
    )
    return run_close_request(
        db_session, closure_id, token, factory(gitlab or FakeClient()), provider, settings
    )


def run(client: TestClient, as_user: Any, who: Any, closure_id: int) -> Any:
    return client.post(f"/api/v1/close-requests/{closure_id}/run", headers=as_user(who))


def test_commit_links_and_hashes() -> None:
    assert parse_commit(LINK) == FIX
    assert parse_commit(f"{LINK}#note_1") == FIX
    assert parse_commit("https://gitlab.x/g/p/commit/ABCDEF1") == "abcdef1"
    assert parse_commit(" abcdef12 ") == "abcdef12"
    with pytest.raises(ValueError):
        parse_commit("https://gitlab.x/g/p/-/merge_requests/3")


def test_member_asks_manager_runs_the_check_and_it_closes(
    client: TestClient,
    world: World,
    as_user: Any,
    queue: FakeQueue,
    db_session: Session,
    settings: Settings,
) -> None:
    asked = ask(client, world, as_user, world.an, [LINK, FIX.upper()])
    assert asked.status_code == 201
    body = asked.json()
    assert (body["status"], body["shas"]) == ("pending", [FIX])
    assert queue.jobs == []  # nothing is paid until a manager or an admin runs it
    assert ask(client, world, as_user, world.an, [FIX]).status_code == 409  # one at a time

    todo = client.get("/api/v1/close-requests?view=todo", headers=as_user(world.manager)).json()
    assert [(i["id"], i["can_decide"]) for i in todo["items"]] == [(body["id"], True)]
    assert todo["items"][0]["finding"]["title"] == "SQL injection"
    assert todo["items"][0]["author"]["name"] == "An"
    facets = client.get("/api/v1/close-requests/facets", headers=as_user(world.manager)).json()
    assert facets["todo"] == 1 and facets["can_decide"] is True

    # Not the author, not their lead (decisions belong to a manager or an admin).
    assert run(client, as_user, world.an, body["id"]).status_code == 403
    assert run(client, as_user, world.lead, body["id"]).status_code == 403
    started = run(client, as_user, world.manager, body["id"])
    assert started.status_code == 202
    assert started.json()["status"] == "checking"
    assert queue.jobs[0][0] == "app.workers.closure_jobs.close_request_job"

    assert check(db_session, settings, body["id"], FIXED) == "closed"
    db_session.refresh(world.an_finding)
    assert (world.an_finding.status, world.an_finding.severity) == ("fixed", "critical")
    history = client.get("/api/v1/close-requests", headers=as_user(world.an)).json()["items"]
    row = history[0]
    assert (row["status"], row["evidence"], row["decided_by_name"]) == (
        "closed",
        "app/a.py:3",
        "Manager",
    )
    assert row["cost_usd"] == 0.01
    assert row["can_decide"] is False  # the author never decides their own request


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ({"index": 0, "verdict": "not_fixed", "reason": "Không liên quan."}, "chưa sửa lỗi"),
        ({"index": 0, "verdict": "partial", "reason": "Còn chỗ khác."}, "một phần"),
        ({**FIXED, "confidence": 0.4}, "chưa đủ chắc chắn"),
    ],
)
def test_not_a_fix_says_why_and_leaves_it_open(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
    answer: dict[str, Any],
    reason: str,
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    assert check(db_session, settings, closure_id, answer) == "rejected"
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None and closure.reason is not None
    assert closure.reason.startswith("Chưa đủ điều kiện đóng")
    assert reason in closure.reason
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"


def test_commits_that_cannot_be_the_fix_are_refused_without_asking(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    unknown = ask(client, world, as_user, world.an, ["a1b2c3d4"]).json()["id"]
    run(client, as_user, world.admin, unknown)
    assert check(db_session, settings, unknown, FIXED) == "rejected"
    refused = db_session.get(FindingClosure, unknown)
    assert refused is not None and "Không tìm thấy commit a1b2c3d4" in (refused.reason or "")

    review = db_session.get(Review, world.an_review.id)
    assert review is not None
    review.changed_at = datetime(2026, 10, 25, tzinfo=UTC)
    db_session.flush()
    older = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, older)
    # committed_date decides, not the author date anyone can set.
    assert check(db_session, settings, older, FIXED) == "rejected"
    refused = db_session.get(FindingClosure, older)
    assert refused is not None and "có trước thay đổi" in (refused.reason or "")
    assert refused.cost_usd is None  # no model call


def test_who_may_ask_and_for_what(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    # Only the author asks: not the lead, the manager, an admin or someone else.
    for other in (world.lead, world.manager, world.admin, world.binh):
        assert ask(client, world, as_user, other, [FIX]).status_code in (403, 404)
    assert ask(client, world, as_user, world.an, ["not-a-sha"]).status_code == 422
    world.an_finding.severity = "medium"
    db_session.flush()
    assert ask(client, world, as_user, world.an, [FIX]).status_code == 422


def test_manager_refuses_author_withdraws(client: TestClient, world: World, as_user: Any) -> None:
    first = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    url = f"/api/v1/close-requests/{first}/reject"
    assert client.post(url, json={"note": "ok"}, headers=as_user(world.manager)).status_code == 422
    refused = client.post(
        url, json={"note": "Commit không liên quan"}, headers=as_user(world.manager)
    )
    assert refused.json()["status"] == "rejected"
    assert refused.json()["reason"] == "Từ chối: Commit không liên quan"

    second = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    cancel = f"/api/v1/close-requests/{second}/cancel"
    assert client.post(cancel, headers=as_user(world.manager)).status_code == 403
    assert client.post(cancel, headers=as_user(world.an)).json()["status"] == "cancelled"
    assert ask(client, world, as_user, world.an, [FIX]).status_code == 201


def test_one_flow_at_a_time_with_disputes(client: TestClient, world: World, as_user: Any) -> None:
    assert ask(client, world, as_user, world.an, [FIX]).status_code == 201
    dispute = client.post(
        f"/api/v1/findings/{world.an_finding.id}/dispute",
        json={"reason": "Không phải lỗi đâu"},
        headers=as_user(world.an),
    )
    assert dispute.status_code == 409


def test_a_request_not_queued_goes_back_to_waiting(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue, db_session: Session
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]

    def down(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("redis down")

    queue.enqueue = down  # type: ignore[method-assign]
    assert run(client, as_user, world.admin, closure_id).status_code == 503
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None and closure.status == "pending"


def test_a_lost_check_can_be_run_again(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None
    closure.updated_at = datetime.now(UTC) - timedelta(hours=4)  # the job never began
    db_session.flush()
    listed = client.get(
        f"/api/v1/reviews/{world.an_review.id}/closures", headers=as_user(world.an)
    ).json()
    assert listed[0]["status"] == "failed"
    assert run(client, as_user, world.admin, closure_id).status_code == 202


def test_admin_closes_and_reopens_with_a_note(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    waiting = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    url = f"/api/v1/findings/{world.an_finding.id}"
    admin = as_user(world.admin)
    assert client.post(f"{url}/close", json={"note": "ok"}, headers=admin).status_code == 422
    lead_close = client.post(
        f"{url}/close", json={"note": "Đã sửa ở hotfix"}, headers=as_user(world.lead)
    )
    assert lead_close.status_code == 403
    closed = client.post(f"{url}/close", json={"note": "Đã sửa ở hotfix"}, headers=admin)
    assert closed.status_code == 200
    assert closed.json()["kind"] == "admin"
    answered = db_session.get(FindingClosure, waiting)
    assert answered is not None and answered.status == "rejected"  # the admin answered it
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "fixed"

    reopened = client.post(f"{url}/reopen", json={"note": "Hotfix bị revert"}, headers=admin)
    assert reopened.json()["status"] == "reopened"
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"
    actions = set(
        db_session.scalars(select(AuditLog.action).where(AuditLog.action.like("finding.%")))
    )
    assert {"finding.close_request", "finding.close", "finding.reopen"} <= actions


def test_history_filters_and_who_sees_it(client: TestClient, world: World, as_user: Any) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    client.post(
        f"/api/v1/close-requests/{closure_id}/reject",
        json={"note": "Chưa đủ bằng chứng"},
        headers=as_user(world.admin),
    )
    ask(client, world, as_user, world.an, [FIX])

    def ids(who: Any, query: str = "") -> list[int]:
        page = client.get(f"/api/v1/close-requests{query}", headers=as_user(who)).json()
        return [i["id"] for i in page["items"]]

    assert len(ids(world.admin)) == 2
    assert len(ids(world.admin, "?status=rejected")) == 1
    assert len(ids(world.admin, f"?author_id={world.an.id}&severity=critical")) == 2
    assert ids(world.admin, f"?author_id={world.binh.id}") == []
    assert len(ids(world.admin, "?q=injection")) == 2
    assert len(ids(world.admin, f"?q={FIX[:8]}")) == 2
    assert ids(world.admin, "?from=2000-01-01&to=2000-01-02") == []
    assert len(ids(world.manager)) == 2  # their team's member
    assert ids(world.outsider) == []  # not their work, not their team
    assert ids(world.binh) == []
    facets = client.get("/api/v1/close-requests/facets", headers=as_user(world.admin)).json()
    assert [a["name"] for a in facets["authors"]] == ["An"]
    assert [r["path"] for r in facets["repos"]] == ["demo/p"]
    assert [p["name"] for p in facets["business_projects"]] == ["Demo"]


def test_the_switch_needs_its_days(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    url = "/api/v1/admin/review-settings"
    refused = client.put(url, json={"auto_close_findings": True}, headers=admin)
    assert refused.status_code == 422
    assert "số ngày N" in refused.json()["detail"]
    on = client.put(url, json={"auto_close_findings": True, "auto_close_days": 3}, headers=admin)
    assert on.status_code == 200, on.text
    values = {s["key"]: s["value"] for s in on.json()}
    assert (values["auto_close_findings"], values["auto_close_days"]) == (True, 3)
    off = client.put(url, json={"auto_close_findings": False}, headers=admin).json()
    assert {s["key"]: s["value"] for s in off}["auto_close_findings"] is False


def test_two_presses_pay_once_and_a_cancel_or_refusal_cannot_race_a_check(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue, db_session: Session
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    assert run(client, as_user, world.manager, closure_id).status_code == 202
    assert run(client, as_user, world.admin, closure_id).status_code == 409
    assert len(queue.jobs) == 1
    cancel = client.post(f"/api/v1/close-requests/{closure_id}/cancel", headers=as_user(world.an))
    assert cancel.status_code == 409
    refuse = client.post(
        f"/api/v1/close-requests/{closure_id}/reject",
        json={"note": "Không cần nữa"},
        headers=as_user(world.admin),
    )
    assert refuse.status_code == 409


def test_only_the_runs_own_job_pays_and_decides(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    first = token_of(db_session, closure_id)
    assert work(db_session, settings, closure_id, "stale-token", FIXED) == "not mine"
    assert work(db_session, settings, closure_id, first, "no json") == "failed"  # paid, unparsed
    # A duplicate job of the same run cannot claim it again.
    assert work(db_session, settings, closure_id, first, FIXED) == "not mine"
    run(client, as_user, world.admin, closure_id)
    second = token_of(db_session, closure_id)
    assert second != first
    assert work(db_session, settings, closure_id, first, FIXED) == "not mine"
    assert work(db_session, settings, closure_id, second, FIXED) == "closed"
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None and float(closure.cost_usd or 0) == 0.02  # both checks kept
    # The budget counts each call in the month it was made.
    assert month_spend_breakdown(db_session, datetime.now(UTC))["closures_usd"] == 0.02


def test_a_cancel_while_queued_leaves_the_finding_open(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    token = token_of(db_session, closure_id)
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None
    closure.status = "rejected"  # e.g. an admin answered it another way meanwhile
    db_session.flush()
    assert work(db_session, settings, closure_id, token, FIXED) == "not mine"
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"


def test_a_lost_check_shows_on_the_page_and_runs_again(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None
    closure.started_at = datetime.now(UTC) - timedelta(hours=1)  # began, then the worker died
    db_session.flush()
    todo = client.get("/api/v1/close-requests?view=todo", headers=as_user(world.manager)).json()
    assert [(i["id"], i["status"], i["can_decide"]) for i in todo["items"]] == [
        (closure_id, "failed", True)
    ]
    assert run(client, as_user, world.manager, closure_id).status_code == 202


def test_a_queued_check_is_not_given_up_too_early(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None
    closure.updated_at = datetime.now(UTC) - timedelta(hours=1)  # waits in a busy queue
    db_session.flush()
    client.get("/api/v1/close-requests", headers=as_user(world.admin))
    db_session.refresh(closure)
    assert closure.status == "checking"


def test_a_manager_decides_only_for_their_teams_in_their_granted_projects(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    other = User(full_name="Other", primary_email="other@x.vn")
    db_session.add(other)
    db_session.flush()
    db_session.add(
        TeamMember(
            team_id=world.mobile.id, user_id=other.id, role="manager", joined_at=date(2025, 1, 1)
        )
    )
    db_session.flush()
    assert run(client, as_user, other, closure_id).status_code == 403
    assert client.get("/api/v1/close-requests", headers=as_user(other)).json()["items"] == []

    # Without a grant on the dự án, the work there does not exist for the manager (spec 09).
    grant = db_session.get(ProjectGrant, (world.project.business_project_id, world.manager.id))
    assert grant is not None
    db_session.delete(grant)
    db_session.flush()
    assert run(client, as_user, world.manager, closure_id).status_code == 403
    assert (
        client.get("/api/v1/close-requests", headers=as_user(world.manager)).json()["items"] == []
    )
    facets = client.get("/api/v1/close-requests/facets", headers=as_user(world.manager)).json()
    assert (facets["todo"], facets["repos"]) == (0, [])


def test_decided_rows_offer_no_decision(client: TestClient, world: World, as_user: Any) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    client.post(
        f"/api/v1/close-requests/{closure_id}/reject",
        json={"note": "Chưa đủ bằng chứng"},
        headers=as_user(world.manager),
    )
    rows = client.get("/api/v1/close-requests", headers=as_user(world.manager)).json()["items"]
    assert rows[0]["can_decide"] is False


def test_search_is_literal(client: TestClient, world: World, as_user: Any) -> None:
    ask(client, world, as_user, world.an, [FIX])
    page = client.get("/api/v1/close-requests?q=%25", headers=as_user(world.admin)).json()
    assert page["items"] == []
