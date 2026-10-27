import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import STATE_COOKIE, get_oauth_http
from app.api.main import app
from app.api.security import SESSION_COOKIE
from app.config import Settings
from app.models import (
    Account,
    AppSetting,
    AuditLog,
    Commit,
    Connection,
    MergeRequest,
    MrCommit,
    Project,
    Review,
    ReviewFinding,
    TeamMember,
    User,
)
from app.scoring.aggregate import aggregate
from tests.api.conftest import BOX, WEEK, FakeQueue, World

pytestmark = pytest.mark.integration


def test_requests_need_a_valid_session(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/api/v1/auth/me", headers=bad).status_code == 401


def test_me_reports_roles(client: TestClient, world: World, as_user: Any) -> None:
    lead = client.get("/api/v1/auth/me", headers=as_user(world.lead)).json()
    assert (lead["is_admin"], lead["lead_teams"]) == (False, [world.backend.id])
    manager = client.get("/api/v1/auth/me", headers=as_user(world.manager)).json()
    assert set(manager["managed_teams"]) == {world.engineering.id, world.backend.id}  # sub-teams
    admin = client.get("/api/v1/auth/me", headers=as_user(world.admin)).json()
    assert admin["is_admin"] is True


@pytest.mark.parametrize(
    ("who", "status"),
    [
        ("an", 200),
        ("lead", 200),
        ("admin", 200),
        ("binh", 403),
        ("manager", 403),
        ("outsider", 403),
    ],
)
def test_individual_metrics_are_for_the_person_and_their_lead(
    client: TestClient, world: World, as_user: Any, who: str, status: int
) -> None:
    url = f"/api/v1/metrics/users/{world.an.id}?period=week&from={WEEK}&to={WEEK}"
    response = client.get(url, headers=as_user(getattr(world, who)))
    assert response.status_code == status
    if status == 200:
        assert response.json()["series"][0]["quality_index"] == "75.00"


@pytest.mark.parametrize(
    ("who", "status"), [("an", 200), ("lead", 200), ("manager", 200), ("outsider", 403)]
)
def test_team_metrics_visibility(
    client: TestClient, world: World, as_user: Any, who: str, status: int
) -> None:
    url = f"/api/v1/metrics/teams/{world.backend.id}?period=week&from={WEEK}&to={WEEK}"
    assert client.get(url, headers=as_user(getattr(world, who))).status_code == status


def test_team_counts_link_to_the_findings_they_count(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    # A fixed high counts (it was found); a false positive and a non-member's finding do not.
    db_session.add_all(
        [
            ReviewFinding(
                review_id=world.an_review.id,
                severity="high",
                category="bug",
                title="fixed",
                status="fixed",
            ),
            ReviewFinding(
                review_id=world.an_review.id,
                severity="high",
                category="bug",
                title="not a bug",
                status="false_positive",
            ),
        ]
    )
    db_session.flush()
    aggregate(db_session, "week", WEEK, ZoneInfo(settings.report_tz))
    db_session.flush()
    lead = as_user(world.lead)
    url = f"/api/v1/metrics/teams/{world.backend.id}?period=week&from={WEEK}&to={WEEK}"
    body = client.get(url, headers=lead).json()
    (current,) = body["series"]
    lo, hi = body["period_range"]

    def findings(**params: Any) -> list[dict[str, Any]]:
        res = client.get("/api/v1/findings", params={"limit": 200, **params}, headers=lead)
        assert res.status_code == 200, res.text
        return list(res.json()["items"])

    counted = {"found_from": lo, "found_to": hi, "status": "counted"}
    team = {"team_id": world.backend.id, **counted}
    assert len(findings(severity="critical", **team)) == current["critical"] == 1
    assert [f["title"] for f in findings(severity="high", **team)] == ["fixed"]
    assert current["high"] == 1
    # A person's row in the members table links the same way.
    assert len(findings(severity="high", user_id=world.an.id, **counted)) == 1
    # Not in Mobile on that day: nothing of An's counts there.
    outsider = as_user(world.admin)
    res = client.get(
        "/api/v1/findings",
        params={"team_id": world.mobile.id, "severity": "critical", **counted},
        headers=outsider,
    )
    assert res.json()["items"] == []
    # Without a period, "counted" matches no number shown anywhere.
    res = client.get("/api/v1/findings", params={"status": "counted"}, headers=lead)
    assert res.status_code == 422


def test_team_counts_follow_the_report_day_and_membership(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    # 23:30 UTC on Sunday is Monday in Asia/Ho_Chi_Minh: a member who joined Backend that
    # Monday counts there; one who left on that Monday no longer does.
    assert settings.report_tz == "Asia/Ho_Chi_Minh"
    late = datetime(2026, 10, 11, 23, 30, tzinfo=UTC)  # Monday 2026-10-12, 06:30 local
    db_session.add(Commit(sha="e" * 40, project_id=world.project.id))
    db_session.flush()
    review = Review(
        target_type="commit",
        commit_sha="e" * 40,
        reviewed_sha="e" * 40,
        status="done",
        author_user_id=world.outsider.id,
        score=Decimal(60),
        finished_at=late,
        rubric_version="v1",
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(
        ReviewFinding(review_id=review.id, severity="critical", category="bug", title="x")
    )
    db_session.add(
        TeamMember(
            team_id=world.backend.id, user_id=world.outsider.id, joined_at=date(2026, 10, 12)
        )
    )
    mobile = db_session.scalars(
        select(TeamMember).where(
            TeamMember.team_id == world.mobile.id, TeamMember.user_id == world.outsider.id
        )
    ).one()
    mobile.left_at = date(2026, 10, 12)
    db_session.flush()
    admin = as_user(world.admin)
    week = {"found_from": "2026-10-12T00:00:00+07:00", "found_to": "2026-10-19T00:00:00+07:00"}

    def titles(team: Any) -> list[str]:
        params = {"team_id": team.id, "severity": "critical", "status": "counted", **week}
        res = client.get("/api/v1/findings", params=params, headers=admin)
        assert res.status_code == 200, res.text
        return [f["title"] for f in res.json()["items"]]

    assert titles(world.backend) == ["x"]
    assert titles(world.mobile) == []
    # An inactive author is left out, as the metrics leave them out.
    world.outsider.active = False
    db_session.flush()
    assert titles(world.backend) == []


def test_leaderboard_hides_people_from_managers(
    client: TestClient, world: World, as_user: Any
) -> None:
    url = f"/api/v1/leaderboard?team_id={world.backend.id}&period=week&start={WEEK}"
    lead_view = client.get(url, headers=as_user(world.lead)).json()
    assert {m["user"]["full_name"] for m in lead_view["members"]} == {"An", "Binh"}
    manager_view = client.get(url, headers=as_user(world.manager)).json()
    assert manager_view["members"] is None
    assert manager_view["team_metrics"]["quality_index"] == "75.00"
    assert client.get(url, headers=as_user(world.an)).status_code == 403


def test_reviews_are_scoped_and_reads_of_others_are_audited(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    own = client.get("/api/v1/reviews", headers=as_user(world.an)).json()["items"]
    assert [r["id"] for r in own] == [world.an_review.id]
    team = client.get("/api/v1/reviews", headers=as_user(world.lead)).json()["items"]
    assert {r["id"] for r in team} == {world.an_review.id, world.binh_review.id}
    peek = client.get(f"/api/v1/reviews?user_id={world.an.id}", headers=as_user(world.binh))
    assert peek.status_code == 403
    assert (
        client.get(f"/api/v1/reviews/{world.an_review.id}", headers=as_user(world.binh)).status_code
        == 403
    )

    detail = client.get(f"/api/v1/reviews/{world.an_review.id}", headers=as_user(world.lead)).json()
    assert detail["findings"][0]["title"] == "SQL injection"
    assert detail["web_url"] == "https://gitlab.example.com/demo/p/-/merge_requests/7"
    actions = db_session.scalars(select(AuditLog.action)).all()
    assert "review.read" in actions


def test_review_list_pagination(client: TestClient, world: World, as_user: Any) -> None:
    first = client.get("/api/v1/reviews?limit=1", headers=as_user(world.admin)).json()
    assert len(first["items"]) == 1 and first["next_cursor"]
    second = client.get(
        f"/api/v1/reviews?limit=1&cursor={first['next_cursor']}", headers=as_user(world.admin)
    ).json()
    assert second["next_cursor"] is None
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_review_list_filters_by_time_member_project_and_status(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.models import BusinessProject

    bp = BusinessProject(name="OmiCRM")
    db_session.add(bp)
    db_session.flush()
    world.project.business_project_id = bp.id
    db_session.flush()
    headers = as_user(world.lead)

    def ids(query: str) -> set[int]:
        res = client.get(f"/api/v1/reviews?{query}", headers=headers)
        assert res.status_code == 200, res.text
        return {r["id"] for r in res.json()["items"]}

    both = {world.an_review.id, world.binh_review.id}
    # Days are REPORT_TZ days: 2026-10-05 18:00 UTC is already 2026-10-06 01:00 in
    # Asia/Ho_Chi_Minh, so it belongs to the 6th, not to the 5th (as it would in UTC).
    world.binh_review.finished_at = datetime(2026, 10, 5, 18, tzinfo=UTC)
    db_session.flush()
    assert ids("from=2026-10-06&to=2026-10-06") == both
    assert ids("to=2026-10-05") == set()
    assert ids("from=2026-10-07") == set()
    assert ids(f"user_id={world.an.id}") == {world.an_review.id}
    assert ids(f"business_project_id={bp.id}") == both
    assert ids(f"business_project_id={bp.id + 1000}") == set()
    assert ids("status=done") == {world.an_review.id}
    assert ids("status=done&status=failed") == both
    item = client.get(f"/api/v1/reviews?user_id={world.an.id}", headers=headers).json()["items"][0]
    assert item["author"]["primary_email"] == "an@x.vn"
    assert client.get("/api/v1/reviews?status=nope", headers=headers).status_code == 422
    # The filters only narrow what the viewer may see: a developer stays on their own reviews.
    mine = client.get(
        f"/api/v1/reviews?business_project_id={bp.id}&status=done&status=failed",
        headers=as_user(world.an),
    ).json()["items"]
    assert [r["id"] for r in mine] == [world.an_review.id]


def test_critical_board(client: TestClient, world: World, as_user: Any) -> None:
    board = client.get(
        "/api/v1/findings?severity=critical&severity=high", headers=as_user(world.lead)
    ).json()["items"]
    (item,) = board
    assert (item["title"], item["author"], item["target"]) == ("SQL injection", "An", "MR !7")
    assert item["age_days"] >= 0
    assert client.get("/api/v1/findings", headers=as_user(world.binh)).json()["items"] == []
    forbidden = client.get(
        f"/api/v1/findings?team_id={world.backend.id}", headers=as_user(world.outsider)
    )
    assert forbidden.status_code == 403


def test_rerun_only_failed_or_skipped(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue
) -> None:
    done = client.post(f"/api/v1/reviews/{world.an_review.id}/rerun", headers=as_user(world.lead))
    assert done.status_code == 409
    failed = client.post(
        f"/api/v1/reviews/{world.binh_review.id}/rerun", headers=as_user(world.lead)
    )
    assert failed.status_code == 202
    ((func, args, _),) = queue.jobs
    assert func.endswith("review_commit_job") and args[1] == "b" * 40
    dev = client.post(f"/api/v1/reviews/{world.binh_review.id}/rerun", headers=as_user(world.an))
    assert dev.status_code == 403


def test_forced_rerun_makes_a_new_round_only_for_an_open_mr_at_its_head(
    client: TestClient,
    world: World,
    as_user: Any,
    queue: FakeQueue,
    db_session: Session,
) -> None:
    url = f"/api/v1/reviews/{world.an_review.id}/rerun"
    lead = as_user(world.lead)
    detail = client.get(f"/api/v1/reviews/{world.an_review.id}", headers=lead).json()
    assert detail["can_rerun"] is False and detail["round"] == 1
    assert [h["id"] for h in detail["history"]] == [world.an_review.id]
    # MR not open: a finished review stays final.
    assert client.post(url, json={"force": True}, headers=lead).status_code == 409
    world.mr.state = "opened"
    db_session.commit()
    assert client.get(f"/api/v1/reviews/{world.an_review.id}", headers=lead).json()["can_rerun"]
    assert client.post(url, headers=lead).status_code == 409  # done without force
    assert client.post(url, json={"force": True}, headers=as_user(world.an)).status_code == 403
    ok = client.post(url, json={"force": True}, headers=lead)
    assert ok.status_code == 202
    ((func, args, kwargs),) = queue.jobs
    assert func.endswith("review_mr_job") and args[1:] == (7, "a" * 40)
    assert kwargs["force"] is True and kwargs["job_id"].endswith("-2")
    assert kwargs["expected_round"] == 1
    db_session.refresh(world.an_review)
    assert world.an_review.status == "done"  # untouched until the new round finishes
    # A new push: the old head is history, it cannot be reviewed again.
    world.mr.head_sha = "c" * 40
    db_session.commit()
    assert client.post(url, json={"force": True}, headers=lead).status_code == 409
    # A superseded round never starts another one.
    world.mr.head_sha = "a" * 40
    newer = Review(
        target_type="mr",
        mr_id=world.mr.id,
        reviewed_sha="a" * 40,
        status="done",
        round=2,
        author_user_id=world.an.id,
        rubric_version=world.an_review.rubric_version,
    )
    db_session.add(newer)
    db_session.flush()
    world.an_review.status, world.an_review.superseded_by = "superseded", newer.id
    db_session.commit()
    assert client.post(url, json={"force": True}, headers=lead).status_code == 409
    history = client.get(f"/api/v1/reviews/{newer.id}", headers=lead).json()["history"]
    assert [(h["round"], h["status"]) for h in history] == [(1, "superseded"), (2, "done")]


def test_forced_rerun_of_an_old_commit(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue, db_session: Session
) -> None:
    world.binh_review.status = "done"
    db_session.commit()
    url = f"/api/v1/reviews/{world.binh_review.id}/rerun"
    ok = client.post(url, json={"force": True}, headers=as_user(world.lead))
    assert ok.status_code == 202
    ((func, args, kwargs),) = queue.jobs
    assert func.endswith("review_commit_job") and args[1] == "b" * 40 and kwargs["force"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/connections"),
        ("patch", "/api/v1/connections/1"),
        ("delete", "/api/v1/connections/1"),
        ("get", "/api/v1/webhooks"),
        ("post", "/api/v1/webhooks"),
        ("patch", "/api/v1/webhooks/1"),
        ("delete", "/api/v1/webhooks/1"),
        ("post", "/api/v1/webhooks/1/test"),
        ("post", "/api/v1/projects/1/webhook"),
        ("post", "/api/v1/projects/import-group"),
        ("get", "/api/v1/projects"),
        ("patch", "/api/v1/projects/1"),
        ("get", "/api/v1/users"),
        ("get", "/api/v1/business-projects"),
        ("put", "/api/v1/projects/business-project"),
        ("post", "/api/v1/projects/1/untrack"),
        ("get", "/api/v1/teams/1/members"),
        ("get", "/api/v1/identity/unmapped"),
        ("get", "/api/v1/admin/llm-costs"),
    ],
)
def test_admin_endpoints_reject_non_admins(
    client: TestClient, world: World, as_user: Any, method: str, path: str
) -> None:
    response = client.request(method, path, headers=as_user(world.lead), json={})
    assert response.status_code == 403


def test_admin_changes_are_audited_and_secrets_never_returned(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    patched = client.patch(
        f"/api/v1/projects/{world.project.id}", headers=admin, json={"post_mr_comment": True}
    )
    assert patched.json()["post_mr_comment"] is True
    created = client.post(
        "/api/v1/connections",
        headers=admin,
        json={"name": "gl2", "base_url": "https://gl2.example.com", "token": "glpat-123456789"},
    ).json()
    assert "token" not in str(created) and "secret" not in str(created)
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "project.update")).one()
    assert (audit.actor_user_id, audit.details) == (world.admin.id, {"post_mr_comment": True})


def test_rotating_a_connection_token_checks_it_and_audits_without_it(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    connection_id = world.project.connection_id
    path = f"/api/v1/connections/{connection_id}"
    admin = as_user(world.admin)
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        user = router.get("https://gitlab.example.com/api/v4/user")
        user.respond(401)
        rejected = client.patch(path, headers=admin, json={"token": "glpat-wrong-123"})
        user.respond(json={"id": 1})
        accepted = client.patch(path, headers=admin, json={"token": "glpat-rotated-123"})
    assert rejected.status_code == 422 and "glpat" not in rejected.text
    assert accepted.status_code == 200
    connection = db_session.get(Connection, connection_id)
    assert connection is not None and BOX.decrypt(connection.token_encrypted) == "glpat-rotated-123"
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "connection.update")).one()
    assert audit.details == {"fields": ["token"]}


def test_connection_patch_rejects_empty_body_and_keeps_the_host(
    client: TestClient, world: World, as_user: Any
) -> None:
    path = f"/api/v1/connections/{world.project.connection_id}"
    assert client.patch(path, headers=as_user(world.admin), json={}).status_code == 422
    moved = client.patch(
        path, headers=as_user(world.admin), json={"base_url": "https://evil.example.com"}
    )
    assert moved.status_code == 422  # the host never changes: the token stays with its GitLab


def test_register_webhook_from_the_dashboard_is_idempotent(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    hooks: list[dict[str, Any]] = []
    gitlab = "https://gitlab.example.com/api/v4/projects/1001/hooks"

    def add(request: httpx.Request) -> httpx.Response:
        hooks.append({**json.loads(request.content), "id": 7})
        return httpx.Response(201, json=hooks[-1])

    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(gitlab).mock(side_effect=lambda r: httpx.Response(200, json=hooks))
        router.post(gitlab).mock(side_effect=add)
        update = router.put(f"{gitlab}/7").respond(json={})
        path = f"/api/v1/projects/{world.project.id}/webhook"
        first = client.post(path, headers=as_user(world.admin)).json()
        second = client.post(path, headers=as_user(world.admin)).json()

    url = "https://codepulse.example.com/api/v1/webhooks/gitlab"
    assert first == {"url": url, "created": True} and second == {"url": url, "created": False}
    (hook,) = hooks
    connection = db_session.get(Connection, world.project.connection_id)
    assert connection is not None and connection.webhook_secret_encrypted
    assert hook["token"] == BOX.decrypt(connection.webhook_secret_encrypted)
    assert update.call_count == 1


def test_import_group_adds_new_projects_and_queues_their_jobs(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    api = "https://gitlab.example.com/api/v4"
    listed = [
        {"id": 1001, "path_with_namespace": "demo/p", "default_branch": "main"},
        {"id": 2002, "path_with_namespace": "demo/sub/api", "default_branch": "main"},
        {"id": 2003, "path_with_namespace": "demo/sub/web", "default_branch": "develop"},
        {"id": 2003, "path_with_namespace": "demo/sub/web", "default_branch": "develop"},
    ]
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        groups = router.get(f"{api}/groups/demo%2Fsub/projects").respond(json=listed)
        router.get(f"{api}/groups/nope/projects").respond(404)
        response = client.post(
            "/api/v1/projects/import-group",
            headers=as_user(world.admin),
            json={"connection_id": world.project.connection_id, "group": " demo/sub "},
        )
        missing = client.post(
            "/api/v1/projects/import-group",
            headers=as_user(world.admin),
            json={"connection_id": world.project.connection_id, "group": "nope"},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    # demo/p is already registered; the duplicate listing row (offset paging) is inserted once.
    assert (sorted(body["added"]), body["skipped"], body["not_queued"]) == (
        ["demo/sub/api", "demo/sub/web"],
        1,
        [],
    )
    assert groups.calls[0].request.url.params["include_subgroups"] == "true"
    assert missing.status_code == 404
    web = db_session.scalars(select(Project).where(Project.external_id == 2003)).one()
    assert web.default_branch == "develop"
    funcs = sorted(func.rsplit(".", 1)[1] for func, _, _ in queue.jobs)
    assert funcs == ["register_webhook_job"] * 2 + ["sync_project_job"] * 2
    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "project.import_group")
    ).one()
    assert audit.details is not None
    assert audit.details["group"] == "demo/sub" and len(audit.details["added"]) == 2


def test_business_projects_group_repos_and_survive_deletion(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    created = client.post("/api/v1/business-projects", headers=admin, json={"name": " OmiCRM "})
    assert created.status_code == 201 and created.json()["name"] == "OmiCRM"
    bp_id = created.json()["id"]
    duplicate = client.post("/api/v1/business-projects", headers=admin, json={"name": "OmiCRM"})
    assert duplicate.status_code == 409
    assigned = client.put(
        "/api/v1/projects/business-project",
        headers=admin,
        json={"project_ids": [world.project.id], "business_project_id": bp_id},
    )
    assert assigned.status_code == 204
    (listed,) = client.get("/api/v1/business-projects", headers=admin).json()
    assert (listed["name"], listed["repos"]) == ("OmiCRM", 1)
    repo = client.get("/api/v1/projects", headers=admin).json()[0]
    assert repo["business_project_id"] == bp_id

    assert client.delete(f"/api/v1/business-projects/{bp_id}", headers=admin).status_code == 204
    db_session.expire_all()
    assert db_session.get(Project, world.project.id) is not None
    repo = client.get("/api/v1/projects", headers=admin).json()[0]
    assert repo["business_project_id"] is None


def test_untracked_repo_keeps_history_and_refuses_sync_until_tracked_again(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue
) -> None:
    admin = as_user(world.admin)
    path = f"/api/v1/projects/{world.project.id}"
    untracked = client.post(f"{path}/untrack", headers=admin).json()
    assert untracked["tracked"] is False and untracked["untracked_at"]
    assert client.post(f"{path}/sync", headers=admin).status_code == 409
    reviews = client.post(f"{path}/backfill-reviews?from=2026-10-01&to=2026-10-31", headers=admin)
    assert reviews.status_code == 409
    # History stays readable.
    assert client.get("/api/v1/reviews", headers=as_user(world.an)).json()["items"]

    rerun = client.post(f"/api/v1/reviews/{world.binh_review.id}/rerun", headers=admin)
    assert rerun.status_code == 409
    assert client.post(f"{path}/webhook", headers=admin).status_code == 409
    unknown = client.put(
        "/api/v1/projects/business-project",
        headers=admin,
        json={"project_ids": [world.project.id], "business_project_id": 999999},
    )
    assert unknown.status_code == 404

    tracked = client.post(f"{path}/track", headers=admin).json()
    assert tracked["tracked"] is True and tracked["untracked_at"] is None
    assert [f for f, _, _ in queue.jobs] == ["app.collector.sync.sync_project_job"]


def test_backfill_reviews_open_mrs_created_in_the_range(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    db_session.add(
        MergeRequest(
            project_id=world.project.id,
            iid=8,
            state="opened",
            head_sha="c" * 40,
            created_at_src=datetime(2026, 10, 10, tzinfo=UTC),
        )
    )
    db_session.flush()
    path = f"/api/v1/projects/{world.project.id}/backfill-reviews?from=2026-10-01&to=2026-10-31"
    closed_only = client.post(path, headers=as_user(world.admin))
    assert closed_only.json() == {"queued": 0}
    with_open = client.post(f"{path}&include_open=true", headers=as_user(world.admin))
    assert with_open.json() == {"queued": 1}
    ((func, args, _),) = queue.jobs
    assert func.endswith("review_mr_job") and args == (world.project.id, 8, "c" * 40)


def test_removing_a_repo_deletes_its_data_after_a_typed_confirmation(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    admin = as_user(world.admin)
    body = {"project_ids": [world.project.id], "confirm": "yes"}
    wrong = client.post("/api/v1/projects/remove", headers=admin, json=body)
    assert wrong.status_code == 422 and "demo/p" in wrong.text
    assert (
        client.post("/api/v1/projects/remove", headers=as_user(world.lead), json=body).status_code
        == 403
    )

    body["confirm"] = " DEMO/P "
    removed = client.post("/api/v1/projects/remove", headers=admin, json=body)
    assert removed.status_code == 200, removed.text
    assert removed.json() == {
        "removed": ["demo/p"],
        "reviews_deleted": 2,
        "cleanup_queued": True,
        "not_queued": [],
    }
    db_session.expire_all()
    assert db_session.get(Project, world.project.id) is None
    for model in (MergeRequest, Review, ReviewFinding):
        assert db_session.scalars(select(model)).first() is None
    jobs = sorted(func.rsplit(".", 1)[1] for func, _, _ in queue.jobs)
    assert jobs == ["recompute_job", "remove_webhook_job"]
    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "project.remove")).one()
    assert audit.details is not None
    assert audit.details["paths"] == ["demo/p"] and audit.details["rows"]["reviews"] == 2


def test_removing_a_repo_keeps_commits_another_repo_still_uses(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    fork = Project(
        connection_id=world.project.connection_id, external_id=2001, path_with_namespace="fork/p"
    )
    db_session.add(fork)
    db_session.flush()
    fork_mr = MergeRequest(project_id=fork.id, iid=1, head_sha="b" * 40)
    db_session.add(fork_mr)
    db_session.flush()
    # The fork's MR contains the commit that demo/p synced first (commits are keyed by sha).
    db_session.add(MrCommit(mr_id=fork_mr.id, sha="b" * 40))
    db_session.flush()
    removed = client.post(
        "/api/v1/projects/remove",
        headers=as_user(world.admin),
        json={"project_ids": [world.project.id], "confirm": "demo/p"},
    )
    assert removed.status_code == 200, removed.text
    db_session.expire_all()
    assert db_session.get(Commit, "b" * 40) is not None
    assert db_session.get(MrCommit, (fork_mr.id, "b" * 40)) is not None
    assert db_session.get(Review, world.binh_review.id) is not None  # the shared commit's review
    assert db_session.get(Review, world.an_review.id) is None


def test_removing_several_repos_asks_for_the_count_phrase(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    other = Project(
        connection_id=world.project.connection_id, external_id=1002, path_with_namespace="demo/q"
    )
    db_session.add(other)
    db_session.flush()
    ids = [world.project.id, other.id]
    admin = as_user(world.admin)
    wrong = client.post(
        "/api/v1/projects/remove", headers=admin, json={"project_ids": ids, "confirm": "demo/p"}
    )
    assert wrong.status_code == 422 and "xoa 2 repo" in wrong.text
    ok = client.post(
        "/api/v1/projects/remove", headers=admin, json={"project_ids": ids, "confirm": "xoa 2 repo"}
    )
    assert sorted(ok.json()["removed"]) == ["demo/p", "demo/q"]
    missing = client.post(
        "/api/v1/projects/remove", headers=admin, json={"project_ids": ids, "confirm": "x"}
    )
    assert missing.status_code == 404


def test_register_webhook_refuses_a_localhost_base_url(
    client: TestClient, world: World, as_user: Any, settings: Settings
) -> None:
    settings.app_base_url = "http://localhost:8000"
    response = client.post(
        f"/api/v1/projects/{world.project.id}/webhook", headers=as_user(world.admin)
    )
    assert response.status_code == 409


@pytest.fixture
def oauth(client: TestClient) -> Any:
    def install(userinfo: dict[str, Any]) -> respx.MockRouter:
        router = respx.mock(assert_all_called=False)
        router.post("https://gitlab.example.com/oauth/token").respond(json={"access_token": "at"})
        router.get("https://gitlab.example.com/oauth/userinfo").respond(json=userinfo)
        app.dependency_overrides[get_oauth_http] = lambda: httpx.Client(
            transport=httpx.MockTransport(router.handler)
        )
        return router

    return install


def test_login_redirects_to_gitlab_with_state(client: TestClient) -> None:
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://gitlab.example.com/oauth/authorize?")
    assert (
        "redirect_uri=https%3A%2F%2Fcodepulse.example.com%2Fapi%2Fv1%2Fauth%2Fcallback"
        in (response.headers["location"])
    )
    assert response.cookies.get(STATE_COOKIE)


def _redirect_uri(location: str) -> str:
    return parse_qs(urlsplit(location).query)["redirect_uri"][0]


def test_sign_in_stays_on_the_public_domain_in_front_of_ip_port(
    client: TestClient, world: World, db_session: Session, oauth: Any
) -> None:
    db_session.add(AppSetting(key="public_url", value="https://codepulse.vihat.vn"))
    db_session.add(Account(kind="gitlab_user", external_key="42", user_id=world.an.id))
    db_session.flush()
    # The dashboard proxy forwards the browser's Host; uvicorn sees its own address.
    proxied = {"X-Forwarded-Host": "CodePulse.vihat.vn:443, proxy.internal"}
    login = client.get("/api/v1/auth/login", headers=proxied, follow_redirects=False)
    callback = "https://codepulse.vihat.vn/api/v1/auth/callback"
    assert _redirect_uri(login.headers["location"]) == callback
    assert "secure" in login.headers["set-cookie"].lower()

    router = oauth({"sub": "42", "email": "someone@else.com"})
    client.cookies.set(STATE_COOKIE, "st")
    response = client.get(
        "/api/v1/auth/callback?code=c&state=st", headers=proxied, follow_redirects=False
    )
    assert response.headers["location"] == "https://codepulse.vihat.vn"
    token = next(c.request for c in router.calls if c.request.url.path == "/oauth/token")
    assert parse_qs(token.content.decode())["redirect_uri"] == [callback]


@pytest.mark.parametrize(
    ("host", "callback", "landing"),
    [
        # The dashboard's own address (no public URL set).
        ("dash.example.com", "https://dash.example.com", "https://dash.example.com"),
        # APP_BASE_URL's host on another port keeps the callback registered for it.
        ("codepulse.example.com:3000", "https://codepulse.example.com", "https://dash.example.com"),
        # Unknown, forged or malformed hosts fall back to APP_BASE_URL.
        ("evil.test", "https://codepulse.example.com", "https://dash.example.com"),
        ("a:abc", "https://codepulse.example.com", "https://dash.example.com"),
        ("a:99999", "https://codepulse.example.com", "https://dash.example.com"),
        ("evil@dash.example.com", "https://codepulse.example.com", "https://dash.example.com"),
    ],
)
def test_sign_in_origin_only_follows_configured_hosts(
    client: TestClient,
    world: World,
    db_session: Session,
    oauth: Any,
    host: str,
    callback: str,
    landing: str,
) -> None:
    db_session.add(Account(kind="gitlab_user", external_key="42", user_id=world.an.id))
    db_session.flush()
    login = client.get("/api/v1/auth/login", headers={"Host": host}, follow_redirects=False)
    assert login.status_code == 302
    assert _redirect_uri(login.headers["location"]) == callback + "/api/v1/auth/callback"

    oauth({"sub": "42", "email": "someone@else.com"})
    client.cookies.set(STATE_COOKIE, "st")
    response = client.get(
        "/api/v1/auth/callback?code=c&state=st", headers={"Host": host}, follow_redirects=False
    )
    assert response.headers["location"] == landing


def test_callback_signs_in_a_registered_gitlab_user(
    client: TestClient, world: World, db_session: Session, oauth: Any
) -> None:
    db_session.add(Account(kind="gitlab_user", external_key="42", user_id=world.an.id))
    db_session.flush()
    oauth({"sub": "42", "email": "someone@else.com"})
    client.cookies.set(STATE_COOKIE, "st")
    response = client.get("/api/v1/auth/callback?code=c&state=st", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://dash.example.com"
    token = response.cookies.get(SESSION_COOKIE)
    assert token
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["id"] == world.an.id


def test_callback_rejects_bad_state_and_unknown_users(
    client: TestClient, world: World, oauth: Any
) -> None:
    oauth({"sub": "999", "email": "stranger@x.vn"})
    client.cookies.set(STATE_COOKIE, "st")
    assert client.get("/api/v1/auth/callback?code=c&state=other").status_code == 400
    assert client.get("/api/v1/auth/callback?code=c&state=st").status_code == 403


def test_first_sign_in_of_an_admin_email_creates_the_user(
    client: TestClient, settings: Settings, db_session: Session, oauth: Any
) -> None:
    settings.admin_emails = ["boss@x.vn"]
    client.cookies.set(STATE_COOKIE, "st")
    oauth({"sub": "7", "email": "Boss@x.vn", "name": "Boss"})
    unverified = client.get("/api/v1/auth/callback?code=c&state=st", follow_redirects=False)
    assert unverified.status_code == 403  # GitLab must vouch for the email

    oauth({"sub": "7", "email": "Boss@x.vn", "email_verified": True, "name": "Boss"})
    client.cookies.set(STATE_COOKIE, "st")
    response = client.get("/api/v1/auth/callback?code=c&state=st", follow_redirects=False)
    assert response.status_code == 302
    user = db_session.scalars(select(User).where(User.primary_email == "boss@x.vn")).one()
    assert user.full_name == "Boss"
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {response.cookies.get(SESSION_COOKIE)}"},
    ).json()
    assert me["id"] == user.id and me["is_admin"] is True

    oauth({"sub": "8", "email": "dev@x.vn", "email_verified": True})
    client.cookies.set(STATE_COOKIE, "st")
    assert client.get("/api/v1/auth/callback?code=c&state=st").status_code == 403


def test_admin_lists_team_members_and_marks_who_left(
    client: TestClient, world: World, as_user: Any
) -> None:
    admin = as_user(world.admin)
    path = f"/api/v1/teams/{world.backend.id}/members"
    left = client.put(
        path,
        headers=admin,
        json={
            "user_id": world.binh.id,
            "role": "member",
            "joined_at": "2025-01-01",
            "left_at": "2026-10-01",
        },
    )
    assert left.status_code == 204
    members = client.get(path, headers=admin).json()
    assert [(m["primary_email"], m["role"], m["left_at"]) for m in members] == [
        ("an@x.vn", "member", None),
        ("lead@x.vn", "lead", None),
        ("binh@x.vn", "member", "2026-10-01"),
    ]
    assert client.get("/api/v1/teams/999999/members", headers=admin).status_code == 404


def test_dashboard_aggregates(client: TestClient, world: World, as_user: Any) -> None:
    lead = as_user(world.lead)
    hot = client.get(
        f"/api/v1/metrics/teams/{world.backend.id}/hotspots?period=week&start={WEEK}", headers=lead
    ).json()
    assert hot == {
        "categories": [{"category": "security", "count": 1}],
        "files": [{"file_path": "app/a.py", "count": 1}],
    }
    board = client.get(
        f"/api/v1/leaderboard?team_id={world.backend.id}&period=week&start={WEEK}", headers=lead
    ).json()
    assert len(board["members"][0]["trend"]) == 8
    assert board["members"][0]["trend"][-1] == "75.00"
    names = [t["name"] for t in client.get("/api/v1/teams/visible", headers=lead).json()]
    assert names == ["Backend"]
    assert (
        client.get(f"/api/v1/people/{world.an.id}", headers=as_user(world.binh)).status_code == 403
    )
    listed = client.get("/api/v1/reviews", headers=as_user(world.an)).json()["items"][0]
    assert (listed["target"], listed["project"]) == ("MR !7", "demo/p")
    heat = client.get(f"/api/v1/metrics/users/{world.an.id}/categories?weeks=52", headers=lead)
    assert heat.status_code == 200
