"""Requests to close critical/high findings (author asks, a manager/admin runs the check or
refuses), admin close/reopen, the "Đóng finding" page, the auto-close switch."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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

    def tree_paths(self, project_id: int, ref: str, limit: int = 5000) -> list[str]:
        return list(FILES)

    def file_raw(self, project_id: int, path: str, ref: str) -> str:
        if path not in FILES:
            raise GitLabError("404", status_code=404)
        return FILES[path]


SECRET = "q8ZrT1vYw3LmN5bK"
FILES = {
    "app/a.py": "key = load_key()\n",
    "config/.env-dev": f"# local debug only\nAES_KEY={SECRET}\n",
    "config/.env-prod": "AES_KEY=${AES_KEY}\nDB_URL=${DATABASE_URL}\n",
    "README.md": "docs\n",
}
ARGUMENT = (
    "Key trong .env-dev chỉ dùng cho profile dev để debug; profile prod ở env-prod không "
    "hardcode key mà bắt buộc đọc từ biến môi trường k8s (AES_KEY)."
)
ACCEPTED = {
    "index": 0,
    "verdict": "accepted",
    "reason": "env-prod đọc AES_KEY từ biến môi trường.",
    "evidence_file": "config/.env-prod",
    "evidence_lines": "1",
    "confidence": 0.9,
}


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
    provider: FakeLLMProvider | None = None,
) -> str:
    provider = provider or FakeLLMProvider(
        lambda p: answer if isinstance(answer, str) else {"answers": [answer]}
    )
    return run_close_request(
        db_session, closure_id, token, factory(gitlab or FakeClient()), provider, settings
    )


def run(client: TestClient, as_user: Any, who: Any, closure_id: int) -> Any:
    return client.post(f"/api/v1/close-requests/{closure_id}/run", headers=as_user(who))


def withdraw(client: TestClient, as_user: Any, who: Any, closure_id: int) -> None:
    cancelled = client.post(f"/api/v1/close-requests/{closure_id}/cancel", headers=as_user(who))
    assert cancelled.json()["status"] == "cancelled"


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

    # Not the author nor a peer; their lead, a manager or an admin may (user request 2026-11-03).
    assert run(client, as_user, world.an, body["id"]).status_code == 403
    assert run(client, as_user, world.binh, body["id"]).status_code == 403
    # The review page offers the buttons to who may use them.
    on_page = f"/api/v1/reviews/{world.an_review.id}/closures"
    (as_lead,) = client.get(on_page, headers=as_user(world.lead)).json()
    assert as_lead["can_decide"] is True and as_lead["can_cancel"] is False
    (as_author,) = client.get(on_page, headers=as_user(world.an)).json()
    assert as_author["can_decide"] is False and as_author["can_cancel"] is True
    lead_todo = client.get("/api/v1/close-requests/facets", headers=as_user(world.lead)).json()
    assert lead_todo["todo"] == 1 and lead_todo["can_decide"] is True
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
    assert check(db_session, settings, closure_id, answer) == "unmet"
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
    assert check(db_session, settings, unknown, FIXED) == "unmet"
    refused = db_session.get(FindingClosure, unknown)
    assert refused is not None and "Không tìm thấy commit a1b2c3d4" in (refused.reason or "")
    withdraw(client, as_user, world.an, unknown)  # a new request needs the last one answered

    review = db_session.get(Review, world.an_review.id)
    assert review is not None
    review.changed_at = datetime(2026, 10, 25, tzinfo=UTC)
    db_session.flush()
    older = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, older)
    # committed_date decides, not the author date anyone can set.
    assert check(db_session, settings, older, FIXED) == "unmet"
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
    # A note alone says neither "fixed by …" nor what the risk really is.
    note_only = client.post(f"{url}/close", json={"note": "Đã sửa ở hotfix"}, headers=admin)
    assert note_only.status_code == 422
    closed = client.post(
        f"{url}/close", json={"note": "Đã sửa ở hotfix", "commits": [LINK]}, headers=admin
    )
    assert closed.status_code == 200
    assert closed.json()["kind"] == "admin" and closed.json()["shas"] == [FIX]
    answered = db_session.get(FindingClosure, waiting)
    assert answered is not None and answered.status == "rejected"  # the admin answered it
    db_session.refresh(world.an_finding)
    # Fixed by a commit: the severity and the score stay.
    assert (world.an_finding.status, world.an_finding.severity) == ("fixed", "critical")
    assert closed.json()["severity_before"] is None

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


def ask_with(client: TestClient, world: World, as_user: Any, body: dict[str, Any]) -> Any:
    return client.post(
        f"/api/v1/findings/{world.an_finding.id}/close-requests",
        json=body,
        headers=as_user(world.an),
    )


def test_a_request_needs_commits_or_a_long_enough_argument(
    client: TestClient, world: World, as_user: Any
) -> None:
    nothing = ask_with(client, world, as_user, {"commits": [], "argument": "  "})
    assert nothing.status_code == 422
    assert "ít nhất một commit" in nothing.text
    short = ask_with(client, world, as_user, {"argument": "x" * 49})
    assert short.status_code == 422
    assert "50 ký tự" in short.text
    ok = ask_with(client, world, as_user, {"argument": ARGUMENT})
    assert ok.status_code == 201
    assert (ok.json()["shas"], ok.json()["argument"]) == ([], ARGUMENT)


def test_an_argument_proven_by_the_current_code_accepts_the_risk(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = ask_with(client, world, as_user, {"argument": ARGUMENT}).json()["id"]
    run(client, as_user, world.manager, closure_id)
    provider = FakeLLMProvider(lambda p: {"answers": [ACCEPTED]})
    token = token_of(db_session, closure_id)
    # The argument held: a proposal, the finding still open until someone confirms it.
    assert work(db_session, settings, closure_id, token, ACCEPTED, provider=provider) == "proposed"
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"
    todo = client.get("/api/v1/close-requests?view=todo", headers=as_user(world.manager)).json()
    assert [(i["status"], i["can_decide"]) for i in todo["items"]] == [("proposed", True)]
    # Neither the author nor a peer confirms it.
    confirm = f"/api/v1/close-requests/{closure_id}/confirm"
    assert client.post(confirm, headers=as_user(world.an)).status_code == 403
    assert client.post(confirm, headers=as_user(world.binh)).status_code == 403
    db_session.refresh(world.an_review)
    score_before = world.an_review.score
    # With no choice the check's suggestion is used (medium when the model gave none).
    confirmed = client.post(confirm, headers=as_user(world.manager))
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert (body["status"], body["closed_as"]) == ("closed", "accepted_risk")
    assert (body["severity_before"], body["severity_after"]) == ("critical", "medium")
    db_session.refresh(world.an_finding)
    assert (world.an_finding.status, world.an_finding.severity) == ("accepted_risk", "medium")
    db_session.refresh(world.an_review)
    assert world.an_review.score is not None and score_before is not None
    assert world.an_review.score > score_before  # re-scored at the lowered severity
    assert client.post(confirm, headers=as_user(world.manager)).status_code == 409

    asked = provider.prompts[0].user
    assert ARGUMENT in asked
    assert "### File: app/a.py" in asked  # the finding's own file
    assert "### File: config/.env-prod" in asked and "### File: config/.env-dev" in asked
    assert "README.md" not in asked  # not named by the argument
    assert SECRET not in asked  # no value is ever shown
    assert "AES_KEY= <ẩn: chuỗi cứng 16 ký tự>" in asked
    assert "AES_KEY= <ẩn: đọc từ biến môi trường>" in asked
    assert "local debug" not in asked  # comments are hidden too

    # An admin may reopen what an argument closed: the severity found comes back.
    reopened = client.post(
        f"/api/v1/findings/{world.an_finding.id}/reopen",
        json={"note": "Prod vẫn còn dùng key cứng"},
        headers=as_user(world.admin),
    )
    assert reopened.status_code == 200
    db_session.refresh(world.an_finding)
    assert (world.an_finding.status, world.an_finding.severity) == ("open", "critical")
    db_session.refresh(world.an_review)
    assert world.an_review.score == score_before


def test_an_argument_without_proof_in_the_code_shown_stays_open(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = ask_with(client, world, as_user, {"argument": ARGUMENT}).json()["id"]
    run(client, as_user, world.manager, closure_id)
    elsewhere = {**ACCEPTED, "evidence_file": "deploy/k8s/secret.yaml"}  # never shown
    assert check(db_session, settings, closure_id, elsewhere) == "unmet"
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None and "phản biện hợp lý nhưng chưa đủ chắc chắn" in (
        closure.reason or ""
    )
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"

    # The request waits for the lead, who runs it again (no new request meanwhile).
    assert ask_with(client, world, as_user, {"argument": ARGUMENT}).status_code == 409
    again = closure_id
    assert run(client, as_user, world.manager, again).status_code == 202
    not_fixed = {"index": 0, "verdict": "not_fixed", "reason": "Không thấy file prod."}
    assert check(db_session, settings, again, not_fixed) == "unmet"
    closure = db_session.get(FindingClosure, again)
    assert closure is not None and "Phản biện chưa được code trên nhánh HEAD chứng minh" in (
        closure.reason or ""
    )


def test_accepted_is_not_a_verdict_without_an_argument(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.manager, closure_id)
    assert check(db_session, settings, closure_id, {**ACCEPTED, "evidence_file": "app/a.py"}) == (
        "unmet"
    )


def test_an_argument_pasting_a_secret_is_refused_at_once(
    client: TestClient, world: World, as_user: Any
) -> None:
    pasted = ask_with(
        client,
        world,
        as_user,
        {"argument": f"Key dev là AES_KEY={SECRET}, prod thì đọc từ biến môi trường k8s rồi ạ."},
    )
    assert pasted.status_code == 422
    assert "khoá/mật khẩu" in pasted.text


def test_a_proposal_can_be_refused(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = ask_with(client, world, as_user, {"argument": ARGUMENT}).json()["id"]
    run(client, as_user, world.manager, closure_id)
    assert check(db_session, settings, closure_id, ACCEPTED) == "proposed"
    refused = client.post(
        f"/api/v1/close-requests/{closure_id}/reject",
        json={"note": "File prod này không phải cấu hình đang chạy"},
        headers=as_user(world.manager),
    )
    assert refused.json()["status"] == "rejected"
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"


def test_evidence_must_be_lines_of_a_file_that_was_shown(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    for evidence in (
        {"evidence_file": "config/.env-prod", "evidence_lines": "40"},  # past its 2 lines
        {"evidence_file": "config/.env-staging", "evidence_lines": "1"},  # named, missing
    ):
        argument = ARGUMENT + " Thêm nữa .env-staging cũng vậy."
        closure_id = ask_with(client, world, as_user, {"argument": argument}).json()["id"]
        run(client, as_user, world.manager, closure_id)
        provider = FakeLLMProvider(lambda p, e=evidence: {"answers": [{**ACCEPTED, **e}]})
        token = token_of(db_session, closure_id)
        assert work(db_session, settings, closure_id, token, None, provider=provider) == "unmet"
        withdraw(client, as_user, world.an, closure_id)
        assert "FILES NOT SHOWN" in provider.prompts[0].user
        assert "env-staging" in provider.prompts[0].user


def test_a_name_matches_whole_names_and_the_nearest_first() -> None:
    from app.workers.closure_jobs import gather_sources

    class Tree(FakeClient):
        def tree_paths(self, project_id: int, ref: str, limit: int = 5000) -> list[str]:
            return [
                "svc-a/config/application-prod.yml",
                "svc-b/config/application-prod.yml",
                "svc-c/config/application-prod.yml",
                "svc-b/src/data.py",
                "svc-b/src/a.py",
            ]

        def file_raw(self, project_id: int, path: str, ref: str) -> str:
            return "spring:\n  datasource:\n    password: hunter22secret\n"

    got = gather_sources(
        Tree(),
        1,
        "main",
        "svc-b/src/a.py",
        "Bản prod ở application-prod.yml đọc mật khẩu từ biến môi trường, xem thêm data.py.",
        [".env*"],
    )
    assert list(got.shown) == [
        "svc-b/src/a.py",
        "svc-b/config/application-prod.yml",  # same service first
        "svc-a/config/application-prod.yml",
        "svc-b/src/data.py",
    ]
    assert all("hunter22secret" not in text for text in got.shown.values())


def test_the_authors_lead_runs_the_check_from_the_review_page(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue
) -> None:
    body = ask(client, world, as_user, world.an, [FIX]).json()
    started = run(client, as_user, world.lead, body["id"])
    assert started.status_code == 202 and started.json()["status"] == "checking"
    assert queue.jobs[-1][0] == "app.workers.closure_jobs.close_request_job"
    (item,) = client.get(
        f"/api/v1/reviews/{world.an_review.id}/closures", headers=as_user(world.lead)
    ).json()
    assert item["status"] == "checking" and item["can_decide"] is False  # nothing to press now


def _proposed(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
    answer: dict[str, Any],
) -> int:
    closure_id = ask_with(client, world, as_user, {"argument": ARGUMENT}).json()["id"]
    run(client, as_user, world.manager, closure_id)
    assert check(db_session, settings, closure_id, answer) == "proposed"
    return int(closure_id)


def test_the_check_suggests_the_severity_and_a_lead_lowers_critical_one_step_only(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = _proposed(
        client, world, as_user, db_session, settings, {**ACCEPTED, "severity": "low"}
    )
    page = f"/api/v1/reviews/{world.an_review.id}/closures"
    (as_lead,) = client.get(page, headers=as_user(world.lead)).json()
    assert as_lead["suggested_severity"] == "low" and as_lead["finding_severity"] == "critical"
    assert as_lead["can_decide"] and not as_lead["can_lower_far"]
    confirm = f"/api/v1/close-requests/{closure_id}/confirm"
    # A lead may not take a critical straight to low (their team's score is at stake) ...
    lead = as_user(world.lead)
    assert client.post(confirm, json={"outcome": "low"}, headers=lead).status_code == 403
    # ... so with no choice the suggestion falls back to what they may do: medium.
    done = client.post(confirm, headers=lead)
    assert done.status_code == 200 and done.json()["severity_after"] == "medium"


def test_a_manager_lowers_critical_to_low_with_a_reason(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = _proposed(
        client, world, as_user, db_session, settings, {**ACCEPTED, "severity": "low"}
    )
    confirm = f"/api/v1/close-requests/{closure_id}/confirm"
    manager = as_user(world.manager)
    assert client.post(confirm, json={"outcome": "low"}, headers=manager).status_code == 422
    done = client.post(
        confirm, json={"outcome": "low", "note": "Chỉ là profile dev"}, headers=manager
    )
    assert done.status_code == 200 and done.json()["severity_after"] == "low"
    db_session.refresh(world.an_finding)
    assert world.an_finding.severity == "low"


def test_a_wrong_finding_leaves_the_score(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = _proposed(client, world, as_user, db_session, settings, ACCEPTED)
    done = client.post(
        f"/api/v1/close-requests/{closure_id}/confirm",
        json={"outcome": "false_positive"},
        headers=as_user(world.lead),
    )
    assert done.status_code == 200 and done.json()["closed_as"] == "false_positive"
    db_session.refresh(world.an_finding)
    db_session.refresh(world.an_review)
    assert world.an_finding.status == "false_positive"
    assert world.an_review.score == Decimal("100.00")  # the only critical is out of the score
    reopened = client.post(
        f"/api/v1/findings/{world.an_finding.id}/reopen",
        json={"note": "Kết luận vội"},
        headers=as_user(world.admin),
    )
    assert reopened.status_code == 200
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"


def test_an_admin_accepts_the_risk_by_a_note_at_the_severity_chosen(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    url = f"/api/v1/findings/{world.an_finding.id}/close"
    admin = as_user(world.admin)
    raised = client.post(
        url, json={"note": "Không ảnh hưởng prod", "outcome": "high"}, headers=admin
    )
    assert raised.status_code == 422  # only medium, low or false_positive
    closed = client.post(
        url, json={"note": "Không ảnh hưởng prod", "outcome": "low"}, headers=admin
    )
    assert closed.status_code == 200
    body = closed.json()
    assert (body["closed_as"], body["severity_before"], body["severity_after"]) == (
        "accepted_risk",
        "critical",
        "low",
    )
    db_session.refresh(world.an_finding)
    assert (world.an_finding.status, world.an_finding.severity) == ("accepted_risk", "low")


def test_a_false_positive_set_by_a_dispute_after_a_reopen_is_not_reopened_here(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = _proposed(client, world, as_user, db_session, settings, ACCEPTED)
    client.post(
        f"/api/v1/close-requests/{closure_id}/confirm",
        json={"outcome": "false_positive"},
        headers=as_user(world.lead),
    )
    reopen = f"/api/v1/findings/{world.an_finding.id}/reopen"
    admin = as_user(world.admin)
    assert client.post(reopen, json={"note": "Kết luận vội"}, headers=admin).status_code == 200
    raised = client.post(
        f"/api/v1/findings/{world.an_finding.id}/dispute",
        headers=as_user(world.an),
        json={"reason": "Input đã được validate ở caller."},
    ).json()
    client.post(
        f"/api/v1/disputes/{raised['id']}/decide",
        headers=as_user(world.lead),
        json={"decision": "accepted"},
    )
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "false_positive"
    # The dispute's decision, not an undone close: not reopened from here.
    assert client.post(reopen, json={"note": "Thử lại"}, headers=admin).status_code == 409


NOT_FIXED = {"index": 0, "verdict": "not_fixed", "reason": "Không liên quan."}


def unmet_request(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
    body: dict[str, Any] | None = None,
) -> int:
    closure_id: int = ask_with(client, world, as_user, body or {"commits": [FIX]}).json()["id"]
    run(client, as_user, world.manager, closure_id)
    assert check(db_session, settings, closure_id, NOT_FIXED) == "unmet"
    return closure_id


def test_a_check_not_good_enough_waits_for_the_decider(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = unmet_request(client, world, as_user, db_session, settings)
    todo = client.get("/api/v1/close-requests?view=todo", headers=as_user(world.manager)).json()
    assert [(i["id"], i["status"], i["can_decide"]) for i in todo["items"]] == [
        (closure_id, "unmet", True)
    ]
    facets = client.get("/api/v1/close-requests/facets", headers=as_user(world.manager)).json()
    assert facets["todo"] == 1
    waiting = client.get("/api/v1/close-requests?status=unmet", headers=as_user(world.an)).json()[
        "items"
    ]
    assert [(i["id"], i["can_cancel"], i["can_decide"]) for i in waiting] == [
        (closure_id, True, False)
    ]
    # Refusing it keeps the check's reason after the decider's.
    refused = client.post(
        f"/api/v1/close-requests/{closure_id}/reject",
        json={"note": "Đồng ý với kết quả kiểm"},
        headers=as_user(world.manager),
    ).json()
    assert refused["status"] == "rejected"
    assert refused["reason"].startswith("Từ chối: Đồng ý với kết quả kiểm\nKết quả kiểm: Chưa đủ")
    # Answered: the author may ask again.
    assert ask(client, world, as_user, world.an, [FIX]).status_code == 201


def test_a_decider_closes_anyway_by_the_commits(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = unmet_request(client, world, as_user, db_session, settings)
    url = f"/api/v1/close-requests/{closure_id}/close"
    body = {"outcome": "fixed", "note": "Commit đã sửa, model đọc sai diff"}
    for who in (world.an, world.binh):  # neither the author nor a peer
        assert client.post(url, json=body, headers=as_user(who)).status_code == 403
    assert (
        client.post(url, json={**body, "note": "ok"}, headers=as_user(world.lead)).status_code
        == 422
    )
    closed = client.post(url, json=body, headers=as_user(world.lead))
    assert closed.status_code == 200
    out = closed.json()
    assert (out["status"], out["closed_as"]) == ("closed", "fixed")
    assert "Người duyệt vẫn đóng: Commit đã sửa" in out["reason"]
    assert "Chưa đủ điều kiện đóng" in out["reason"]  # the check's reason stays
    db_session.refresh(world.an_finding)
    assert (world.an_finding.status, world.an_finding.severity) == ("fixed", "critical")
    assert client.post(url, json=body, headers=as_user(world.lead)).status_code == 409
    actions = set(
        db_session.scalars(select(AuditLog.action).where(AuditLog.action.like("finding.%")))
    )
    assert "finding.close_request.override" in actions


def test_a_decider_closes_anyway_accepting_the_risk(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = unmet_request(client, world, as_user, db_session, settings, {"argument": ARGUMENT})
    url = f"/api/v1/close-requests/{closure_id}/close"
    note = "Prod đọc key từ k8s, đã kiểm tay"
    # No commit in the request: "fixed" cannot be its outcome.
    no_fix = client.post(url, json={"outcome": "fixed", "note": note}, headers=as_user(world.lead))
    assert no_fix.status_code == 422
    # A lead lowers a critical one step only.
    far = client.post(url, json={"outcome": "low", "note": note}, headers=as_user(world.lead))
    assert far.status_code == 403
    db_session.refresh(world.an_review)
    score_before = world.an_review.score
    closed = client.post(url, json={"outcome": "medium", "note": note}, headers=as_user(world.lead))
    assert closed.status_code == 200
    out = closed.json()
    assert (out["closed_as"], out["severity_before"], out["severity_after"]) == (
        "accepted_risk",
        "critical",
        "medium",
    )
    db_session.refresh(world.an_finding)
    assert (world.an_finding.status, world.an_finding.severity) == ("accepted_risk", "medium")
    db_session.refresh(world.an_review)
    assert world.an_review.score is not None and score_before is not None
    assert world.an_review.score > score_before


def test_only_an_unmet_request_is_closed_this_way(
    client: TestClient, world: World, as_user: Any
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    closed = client.post(
        f"/api/v1/close-requests/{closure_id}/close",
        json={"outcome": "fixed", "note": "Chưa kiểm mà đóng"},
        headers=as_user(world.manager),
    )
    assert closed.status_code == 409


def test_disputing_withdraws_the_authors_unmet_request(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = unmet_request(client, world, as_user, db_session, settings)
    dispute = client.post(
        f"/api/v1/findings/{world.an_finding.id}/dispute",
        json={"reason": "Finding này không đúng ở môi trường thật"},
        headers=as_user(world.an),
    )
    assert dispute.status_code == 201
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None
    db_session.refresh(closure)
    assert closure.status == "cancelled"


def test_a_request_of_a_finding_closed_another_way_leaves_the_todo(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = unmet_request(client, world, as_user, db_session, settings)
    world.an_finding.status = "fixed"  # e.g. a later review of the MR no longer reports it
    db_session.flush()
    todo = client.get("/api/v1/close-requests?view=todo", headers=as_user(world.manager)).json()
    assert todo["items"] == []
    facets = client.get("/api/v1/close-requests/facets", headers=as_user(world.manager)).json()
    assert facets["todo"] == 0
    (row,) = client.get("/api/v1/close-requests", headers=as_user(world.manager)).json()["items"]
    assert (row["id"], row["can_decide"]) == (closure_id, False)
    # Reopened: the stale request is answered, a new one starts from today's code.
    reopened = client.post(
        f"/api/v1/findings/{world.an_finding.id}/reopen",
        json={"note": "Lỗi vẫn còn ở nhánh chính"},
        headers=as_user(world.admin),
    )
    assert reopened.status_code == 200
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None
    db_session.refresh(closure)
    assert closure.status == "rejected"
    assert ask(client, world, as_user, world.an, [FIX]).status_code == 201


def test_a_fix_the_diff_cannot_show_is_proven_by_the_current_code(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
) -> None:
    """User 2026-11-06: the flagged lines left the diff (deleted by another commit, or the code
    rewritten): the finding's file as it is now on the default branch settles it, nobody
    traces the commits."""
    elsewhere = {**DIFF, "old_path": "app/b.py", "new_path": "app/b.py"}

    class Moved(FakeClient):
        def commit_diff(self, project_id: int, sha: str) -> list[dict[str, Any]]:
            return [elsewhere]  # the commit does not touch the finding's file

    prompts: list[str] = []

    def answer(prompt: Any) -> dict[str, Any]:
        prompts.append(prompt.user)
        return {"answers": [{**FIXED, "evidence_file": "app/a.py", "evidence_lines": "1"}]}

    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    token = token_of(db_session, closure_id)
    provider = FakeLLMProvider(answer)
    assert work(db_session, settings, closure_id, token, None, Moved(), provider) == "closed"
    assert "CURRENT CODE" in prompts[0] and "key = load_key()" in prompts[0]
    assert "Do not try to trace other commits" in provider.prompts[0].system
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "fixed"


def test_current_code_evidence_must_be_lines_that_were_shown(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
) -> None:
    elsewhere = {**DIFF, "old_path": "app/b.py", "new_path": "app/b.py"}

    class Moved(FakeClient):
        def commit_diff(self, project_id: int, sha: str) -> list[dict[str, Any]]:
            return [elsewhere]

    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    past_the_end = {**FIXED, "evidence_file": "app/a.py", "evidence_lines": "40"}  # 1 line shown
    assert check(db_session, settings, closure_id, past_the_end, Moved()) == "unmet"
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"


DELETED_FILE = {**DIFF, "deleted_file": True, "diff": ""}  # GitLab: a deleted file, no text
GONE = {**FIXED, "evidence_file": "app/a.py", "evidence_lines": None}


class Deleted(FakeClient):
    """The commit deletes the finding's file: not on the default branch any more."""

    def commit_diff(self, project_id: int, sha: str) -> list[dict[str, Any]]:
        return [DELETED_FILE]

    def tree_paths(self, project_id: int, ref: str, limit: int = 5000) -> list[str]:
        return [p for p in FILES if p != "app/a.py"]

    def file_raw(self, project_id: int, path: str, ref: str) -> str:
        if path == "app/a.py":
            raise GitLabError("404", status_code=404)
        return super().file_raw(project_id, path, ref)


def test_a_commit_deleting_the_finding_file_is_checked_and_closes(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
) -> None:
    """User 2026-11-06: the commit deletes the whole file (dead code), so its diff is empty; that
    is the fix, not "nothing to review", and the request's note is read."""
    closure_id = client.post(
        f"/api/v1/findings/{world.an_finding.id}/close-requests",
        json={"commits": [FIX], "note": "Xoá hẳn file a.py (dead code), không còn caller."},
        headers=as_user(world.an),
    ).json()["id"]
    run(client, as_user, world.admin, closure_id)
    token = token_of(db_session, closure_id)
    provider = FakeLLMProvider(lambda p: {"answers": [GONE]})
    assert work(db_session, settings, closure_id, token, None, Deleted(), provider) == "closed"
    prompt = provider.prompts[0].user
    assert "FILES DELETED" in prompt and "- app/a.py" in prompt
    assert "Xoá hẳn file a.py" in prompt  # the note, read on a web request too
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "fixed"


def test_a_file_missing_from_the_default_branch_is_no_proof_alone(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
) -> None:
    """The finding's file is not on the default branch and no namesake is left, but the request's
    commits do not delete it: it may never have been merged (a branch's new file) or been moved
    under another name. Told to the model, never taken as the fix."""
    elsewhere = {**DIFF, "old_path": "app/b.py", "new_path": "app/b.py"}

    class Earlier(Deleted):
        def commit_diff(self, project_id: int, sha: str) -> list[dict[str, Any]]:
            return [elsewhere]

    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    token = token_of(db_session, closure_id)
    provider = FakeLLMProvider(lambda p: {"answers": [GONE]})
    assert work(db_session, settings, closure_id, token, None, Earlier(), provider) == "unmet"
    prompt = provider.prompts[0].user
    assert "không còn file nào cùng tên trong repo" in prompt and "FILES DELETED" not in prompt
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"


def test_a_moved_file_is_shown_and_its_old_path_proves_nothing(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
) -> None:
    class MovedFile(Deleted):
        def tree_paths(self, project_id: int, ref: str, limit: int = 5000) -> list[str]:
            return ["lib/a.py", "README.md"]

        def file_raw(self, project_id: int, path: str, ref: str) -> str:
            if path == "lib/a.py":
                return "q = 'x' + v\n"
            return super().file_raw(project_id, path, ref)

    class Unrelated(MovedFile):
        def commit_diff(self, project_id: int, sha: str) -> list[dict[str, Any]]:
            return [{**DIFF, "old_path": "app/b.py", "new_path": "app/b.py"}]

    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    token = token_of(db_session, closure_id)
    provider = FakeLLMProvider(lambda p: {"answers": [GONE]})
    # The model says "gone" but the file only moved: a deleted-file evidence is refused.
    assert work(db_session, settings, closure_id, token, None, Unrelated(), provider) == "unmet"
    prompt = provider.prompts[0].user
    assert "### File: lib/a.py" in prompt and "FILES DELETED" not in prompt
    db_session.refresh(world.an_finding)
    assert world.an_finding.status == "open"


def test_a_tree_cut_at_the_limit_never_says_deleted() -> None:
    from app.workers.closure_jobs import TREE_LIMIT, gather_sources

    class Huge(Deleted):
        def tree_paths(self, project_id: int, ref: str, limit: int = 5000) -> list[str]:
            return [f"x/{n}.py" for n in range(limit)]

    out = gather_sources(Huge(), 1, "main", "app/a.py", "", [])
    assert out.gone == [] and TREE_LIMIT == 5000
    assert gather_sources(Deleted(), 1, "main", "app/a.py", "", []).gone == ["app/a.py"]


def test_an_empty_diff_without_note_or_code_says_what_was_skipped(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
) -> None:
    binary = {**DIFF, "old_path": "img/logo.png", "new_path": "img/logo.png", "diff": ""}

    class Binary(FakeClient):
        def commit_diff(self, project_id: int, sha: str) -> list[dict[str, Any]]:
            return [binary]

        def file_raw(self, project_id: int, path: str, ref: str) -> str:
            raise GitLabError("500", status_code=500)

    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    assert check(db_session, settings, closure_id, FIXED, Binary()) == "unmet"
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None
    db_session.refresh(closure)
    assert "img/logo.png (binary_or_empty)" in (closure.reason or "")


def test_history_shows_the_findings_state_when_it_was_closed_later(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    """User 2026-11-06: a refused request whose finding was closed later (another request, a
    later review) showed "Bị từ chối" while the finding's page said "Đã đóng · Đã sửa". The row
    shows the finding's state, the request's own result beside it; the filter matches it."""
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    refused = client.post(
        f"/api/v1/close-requests/{closure_id}/reject",
        json={"note": "Chưa thấy chỗ sửa"},
        headers=as_user(world.manager),
    )
    assert refused.json()["status"] == "rejected"
    world.an_finding.status = "fixed"  # closed later, another way
    db_session.flush()
    manager = as_user(world.manager)

    def rows(**params: str) -> list[dict[str, Any]]:
        res = client.get("/api/v1/close-requests", params=params, headers=manager)
        assert res.status_code == 200, res.text
        return list(res.json()["items"])

    (row,) = rows()
    assert (row["status"], row["shown_status"], row["shown_closed_as"]) == (
        "rejected",
        "closed",
        "fixed",
    )
    assert [r["id"] for r in rows(status="closed")] == [closure_id]
    assert rows(status="rejected") == []
    # Open again: the refused request is what the row shows once more.
    world.an_finding.status = "open"
    db_session.flush()
    (row,) = rows()
    assert (row["shown_status"], row["shown_closed_as"]) == ("rejected", None)
    assert [r["id"] for r in rows(status="rejected")] == [closure_id]


def test_a_check_of_a_finding_already_closed_is_moot_not_refused(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    closure_id = ask(client, world, as_user, world.an, [FIX]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    world.an_finding.status = "fixed"
    db_session.commit()
    assert check(db_session, settings, closure_id, FIXED) == "cancelled"
    closure = db_session.get(FindingClosure, closure_id)
    assert closure is not None
    db_session.refresh(closure)
    assert (closure.reason or "").startswith("Finding đã được đóng trước khi kiểm")


def test_a_request_that_closed_a_finding_reopened_since_shows_reopened() -> None:
    from app.models.review import shown_status

    assert shown_status("closed", "open") == "reopened"
    assert shown_status("closed", "fixed") == "closed"
    assert shown_status("pending", "open") == "pending"
    assert shown_status("pending", "accepted_risk") == "closed"
    assert shown_status("rejected", "disputed") == "rejected"


def _sent(db_session: Session, world: World, n: int) -> None:
    """`n` close requests of An in the last 24 hours (answered, so none is in flight)."""
    for _ in range(n):
        db_session.add(
            FindingClosure(
                finding_id=world.an_finding.id,
                kind="request",
                status="rejected",
                requested_by=world.an.id,
                argument="x" * 60,
            )
        )
    db_session.flush()


def test_the_close_request_limit_is_the_default_or_the_persons_own(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    """User 2026-11-06: 20 a day by default (Admin > Cấu hình), a person's own number wins,
    0 sends none; web and MCP count together."""
    _sent(db_session, world, 3)
    admin = as_user(world.admin)
    quota = client.get(f"/api/v1/admin/users/{world.an.id}/close-quota", headers=admin).json()
    assert quota == {"user_id": world.an.id, "own": None, "default": 20, "limit": 20, "used_24h": 3}

    put = client.put(
        "/api/v1/admin/review-settings", json={"close_requests_per_day": 3}, headers=admin
    )
    assert put.status_code == 200, put.text
    refused = ask(client, world, as_user, world.an, [FIX])
    assert refused.status_code == 429 and "tối đa 3" in refused.text

    own = client.put(
        f"/api/v1/admin/users/{world.an.id}/close-quota", json={"own": 5}, headers=admin
    )
    assert own.json()["limit"] == 5 and own.json()["own"] == 5
    assert ask(client, world, as_user, world.an, [FIX]).status_code == 201

    pending = db_session.scalars(
        select(FindingClosure).where(FindingClosure.status == "pending")
    ).one()
    withdraw(client, as_user, world.an, pending.id)  # none in flight: the limit answers
    none = client.put(
        f"/api/v1/admin/users/{world.an.id}/close-quota", json={"own": 0}, headers=admin
    )
    assert none.json()["limit"] == 0
    blocked = ask(client, world, as_user, world.an, [FIX])
    assert blocked.status_code == 429 and "giới hạn 0" in blocked.text
    actions = db_session.scalars(select(AuditLog.action)).all()
    assert "user.close_quota" in actions

    back = client.put(
        f"/api/v1/admin/users/{world.an.id}/close-quota", json={"own": None}, headers=admin
    )
    assert (back.json()["own"], back.json()["limit"]) == (None, 3)
    for body in ({"own": 1001}, {"own": -1}, {}):
        bad = client.put(f"/api/v1/admin/users/{world.an.id}/close-quota", json=body, headers=admin)
        assert bad.status_code == 422, body
    for value in (0, 1001):
        bad = client.put(
            "/api/v1/admin/review-settings", json={"close_requests_per_day": value}, headers=admin
        )
        assert bad.status_code == 422, value


def test_only_the_review_settings_right_sets_a_persons_limit(
    client: TestClient, world: World, as_user: Any
) -> None:
    path = f"/api/v1/admin/users/{world.an.id}/close-quota"
    assert client.get(path, headers=as_user(world.manager)).status_code == 403
    assert client.put(path, json={"own": 50}, headers=as_user(world.an)).status_code == 403
    assert (
        client.get(
            "/api/v1/admin/users/999999/close-quota", headers=as_user(world.admin)
        ).status_code
        == 404
    )


def test_a_non_admin_with_the_right_never_sets_their_own_or_an_admins_limit(
    client: TestClient, world: World, as_user: Any
) -> None:
    granted = client.put(
        f"/api/v1/admin/users/{world.manager.id}/own-permissions",
        json={"items": {"admin.review_settings": "flag"}, "confirm_sensitive": True},
        headers=as_user(world.admin),
    )
    assert granted.status_code == 200, granted.text
    manager = as_user(world.manager)
    put = lambda who: client.put(  # noqa: E731
        f"/api/v1/admin/users/{who.id}/close-quota", json={"own": 500}, headers=manager
    )
    assert put(world.an).status_code == 200
    assert put(world.manager).status_code == 403  # not their own
    assert put(world.admin).status_code == 403  # an admin's is an admin's to set


FIRST = "a" * 40  # the older commit: half the fix
SECOND = "b" * 40  # the newer one: finishes it


class TwoCommits(FakeClient):
    """Two fixing commits touching the same file; the newer listed first (GitLab's order)."""

    def commit(self, project_id: int, sha: str) -> dict[str, Any]:
        when = {FIRST: "2026-10-20T10:00:00+00:00", SECOND: "2026-10-21T10:00:00+00:00"}
        if sha not in when:
            raise GitLabError("404")
        title = "fix: bind q" if sha == FIRST else "fix: bind the other query"
        return {"id": sha, "committed_date": when[sha], "title": title}

    def commit_diff(self, project_id: int, sha: str) -> list[dict[str, Any]]:
        if sha == FIRST:
            return [DIFF]
        return [{**DIFF, "diff": "@@ -9,1 +9,1 @@\n-r = 'y' + w\n+r = bind(w)\n"}]


def test_every_commit_of_a_request_reaches_the_check_labelled_oldest_first(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
) -> None:
    """A request with several commits: each commit's diff is shown under its own header, oldest
    first whatever order they were typed in, so the model judges them together (it used to get
    one unlabelled diff with the same file twice and judged the first block only)."""
    closure_id = ask(client, world, as_user, world.an, [SECOND, FIRST]).json()["id"]
    run(client, as_user, world.admin, closure_id)
    token = token_of(db_session, closure_id)
    provider = FakeLLMProvider(lambda p: {"answers": [FIXED]})
    assert work(db_session, settings, closure_id, token, None, TwoCommits(), provider) == "closed"
    prompt = provider.prompts[0].user
    assert f"Change: commit {FIRST[:12]}, {SECOND[:12]} (cũ trước, mới sau)" in prompt
    older = prompt.index(f"## Commit {FIRST[:12]} (1/2): fix: bind q")
    newer = prompt.index(f"## Commit {SECOND[:12]} (2/2): fix: bind the other query")
    assert older < prompt.index("+q = bind(v)") < newer < prompt.index("+r = bind(w)")


def test_one_commit_keeps_the_plain_diff() -> None:
    from app.review.prepare import parse_diff
    from app.review.resolve import diff_text

    text = diff_text([parse_diff("app/a.py", DIFF["diff"])], ["app/a.py"])
    assert text.startswith("### File: app/a.py") and "## Commit" not in text


def test_the_last_commit_touching_a_file_decides_whether_it_is_deleted(settings: Settings) -> None:
    """Deleted by an older commit, brought back (bug and all) by a newer
    one, the file is not "deleted"; deleted by the newer one, the older diff is gone with it."""
    from app.workers.closure_jobs import _prepare_commits

    old = datetime(2026, 10, 20, tzinfo=UTC)
    new = datetime(2026, 10, 21, tzinfo=UTC)
    back = _prepare_commits(
        [(new, 0, SECOND, "back", [DIFF]), (old, 1, FIRST, "drop", [DELETED_FILE])], settings
    )
    assert back.deleted == [] and [f.path for f in back.files] == ["app/a.py"]
    dropped = _prepare_commits(
        [(old, 0, FIRST, "edit", [DIFF]), (new, 1, SECOND, "drop", [DELETED_FILE])], settings
    )
    assert dropped.deleted == ["app/a.py"] and dropped.files == []
