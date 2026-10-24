"""Review on request: repos, people, branches, recent commits and queuing (docs/specs/17)."""

from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, AuditLog, BusinessProject, Commit, Review
from tests.api.conftest import FakeQueue, World
from tests.collector.fake_gitlab import API, fixture, mount

A = "a" * 40  # an.nguyen@company.vn, plain commit
B = "b" * 40  # Binh.Personal@gmail.com, plain commit (world.binh_review covers it: failed)
C = "c" * 40  # merge commit
D = "d" * 40  # unmapped noreply author


def link_accounts(session: Session, world: World) -> None:
    session.add_all(
        [
            Account(kind="git_email", external_key="an.nguyen@company.vn", user_id=world.an.id),
            Account(
                kind="git_email", external_key="binh.personal@gmail.com", user_id=world.binh.id
            ),
        ]
    )
    session.flush()


def gitlab(router: respx.MockRouter) -> None:
    """Branches + single commits on top of the shared fake (commits.json for the branch)."""
    router.route(host="testserver").pass_through()
    router.get(f"{API}/projects/1001/repository/branches").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "main",
                    "default": True,
                    "commit": {"committed_date": "2026-10-03T10:00:00Z"},
                },
                {
                    "name": "feat/x",
                    "default": False,
                    "commit": {"committed_date": "2026-10-04T10:00:00Z"},
                },
            ],
        )
    )
    by_sha = {c["id"]: c for c in fixture("commits.json")}
    router.get(
        url__regex=rf"{API}/projects/1001/repository/commits/(?P<sha>[0-9a-f]+)(\?.*)?$"
    ).mock(
        side_effect=lambda r, sha: (
            httpx.Response(200, json=by_sha[sha])
            if sha in by_sha
            else httpx.Response(404, json={"message": "404 Commit Not Found"})
        )
    )
    mount(router)


def test_repos_people_and_branches_follow_the_callers_scope(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = BusinessProject(name="OmiCRM")
    db_session.add(bp)
    db_session.flush()
    world.project.business_project_id = bp.id
    db_session.flush()
    repos = client.get("/api/v1/repos", headers=as_user(world.an)).json()
    assert [(r["path_with_namespace"], r["business_project"]) for r in repos] == [
        ("demo/p", "OmiCRM")
    ]

    assert [p["id"] for p in client.get("/api/v1/people", headers=as_user(world.an)).json()] == [
        world.an.id
    ]
    lead_people = {
        p["id"] for p in client.get("/api/v1/people", headers=as_user(world.lead)).json()
    }
    assert lead_people == {world.lead.id, world.an.id, world.binh.id}
    assert len(client.get("/api/v1/people", headers=as_user(world.admin)).json()) == 6

    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        branches = client.get(
            f"/api/v1/repos/{world.project.id}/branches", headers=as_user(world.an)
        ).json()
    assert [b["name"] for b in branches] == ["main", "feat/x"] and branches[0]["default"] is True


def test_recent_commits_show_review_state_and_hide_strangers(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    link_accounts(db_session, world)
    path = f"/api/v1/repos/{world.project.id}/commits"
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        as_an = client.get(path, params={"ref": "main"}, headers=as_user(world.an)).json()
        as_lead = client.get(path, params={"ref": "main"}, headers=as_user(world.lead)).json()
        only_binh = client.get(
            path, params={"ref": "main", "user_id": world.binh.id}, headers=as_user(world.lead)
        ).json()
        as_admin = client.get(path, params={"ref": "main"}, headers=as_user(world.admin)).json()
        forbidden = client.get(
            path, params={"ref": "main", "user_id": world.binh.id}, headers=as_user(world.an)
        ).json()

    # A member sees only their own commits (the merge one included, but not reviewable).
    assert [c["sha"] for c in as_an["commits"]] == [A, C] and as_an["hidden"] == 2
    a, c = as_an["commits"]
    assert a["reviewable"] is True and a["user_name"] == "An" and a["additions"] == 30
    assert (c["is_merge"], c["reviewable"], c["reason"]) == (True, False, "merge commit")
    # The lead sees their people and the unmapped author; binh's commit has a failed review.
    by_sha = {c["sha"]: c for c in as_lead["commits"]}
    assert set(by_sha) == {A, B, C, D} and as_lead["hidden"] == 0
    assert (by_sha[B]["review_status"], by_sha[B]["reviewable"]) == ("failed", True)
    assert by_sha[D]["user_id"] is None and by_sha[D]["reviewable"] is True
    assert [c["sha"] for c in only_binh["commits"]] == [B]
    assert len(as_admin["commits"]) == 4
    assert forbidden["commits"] == []  # the filter cannot widen a member's scope


def test_review_commits_queues_dashboard_reviews_and_explains_skips(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    link_accounts(db_session, world)
    path = f"/api/v1/repos/{world.project.id}/review-commits"
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        denied = client.post(
            path, json={"ref": "main", "shas": [B]}, headers=as_user(world.an)
        ).json()
        result = client.post(
            path,
            json={"ref": "main", "shas": [A, B, C, D, "e" * 40, A]},
            headers=as_user(world.lead),
        )
    assert denied == {
        "queued": [],
        "skipped": [{"sha": B, "reason": "không thuộc quyền review của bạn"}],
    }
    assert result.status_code == 202, result.text
    body = result.json()
    # A and D are new commits (rows created); B's failed review may run again; C is a merge.
    assert body["queued"] == [A, B, D]
    assert {s["sha"]: s["reason"] for s in body["skipped"]} == {
        C: "merge commit",
        "e" * 40: "không tìm thấy trên GitLab",
    }
    jobs = {args[1]: kwargs for func, args, kwargs in queue.jobs}
    assert set(jobs) == {A, B, D}
    assert jobs[A]["trigger"] == "dashboard" and jobs[A]["requested_by"] == world.lead.id
    assert jobs[A]["job_id"] == f"review-commit-{A[:12]}"
    stored = {c.sha: c for c in db_session.scalars(select(Commit).where(Commit.sha.in_([A, D])))}
    assert stored[A].project_id == world.project.id and stored[A].additions == 30
    assert stored[D].author_account_id is not None  # the noreply address became an account
    audits = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "project.review_commits").order_by(AuditLog.id)
    ).all()
    assert [a.details for a in audits] == [
        {"ref": "main", "queued": 0, "skipped": 1},
        {"ref": "main", "queued": 3, "skipped": 2},
    ]

    # The queued rows exist already (author, trigger, requester), so the list is truthful at
    # once and a second request for the same sha is refused.
    queued_row = db_session.scalars(select(Review).where(Review.commit_sha == A)).one()
    assert (queued_row.status, queued_row.trigger, queued_row.requested_by_user_id) == (
        "queued",
        "dashboard",
        world.lead.id,
    )
    assert queued_row.author_user_id == world.an.id
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        again = client.post(
            path, json={"ref": "main", "shas": [A]}, headers=as_user(world.lead)
        ).json()
    assert again["queued"] == [] and again["skipped"][0]["reason"] == "đã có review (queued)"


def test_gitlab_trouble_is_reported_generically(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    base = f"/api/v1/repos/{world.project.id}"
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/1001/repository/branches").mock(
            return_value=httpx.Response(500, text="internal https://gitlab.example.com/secret")
        )
        router.get(f"{API}/projects/1001/repository/commits").mock(
            return_value=httpx.Response(404, json={"message": "404 Tree Not Found"})
        )
        down = client.get(f"{base}/branches", headers=admin)
        missing = client.get(f"{base}/commits", params={"ref": "nope"}, headers=admin)
    assert down.status_code == 502 and "gitlab.example.com" not in down.text
    assert missing.status_code == 404 and missing.json()["detail"] == "branch not found on GitLab"
    bad_range = client.get(
        f"{base}/commits",
        params={"ref": "main", "since": "2026-10-10T00:00:00Z", "until": "2026-10-01T00:00:00"},
        headers=admin,
    )
    assert bad_range.status_code == 422  # naive and aware values compare after normalisation


def test_review_commits_respects_repo_flags_and_batch_size(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Any
) -> None:
    path = f"/api/v1/repos/{world.project.id}/review-commits"
    too_many = client.post(
        path,
        json={"ref": "main", "shas": ["a" * 40] * 0 + [f"{i:040x}" for i in range(51)]},
        headers=as_user(world.admin),
    )
    assert too_many.status_code == 422
    world.project.review_enabled = False
    db_session.flush()
    off = client.post(path, json={"ref": "main", "shas": [A]}, headers=as_user(world.admin))
    assert off.status_code == 409
    world.project.tracked = False
    db_session.flush()
    assert (
        client.get(
            f"/api/v1/repos/{world.project.id}/branches", headers=as_user(world.admin)
        ).status_code
        == 404
    )


def test_admin_sets_rules_and_reviews_carry_verdicts(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    bp = BusinessProject(name="OmiCRM")
    db_session.add(bp)
    db_session.flush()
    patched = client.patch(
        f"/api/v1/business-projects/{bp.id}",
        headers=admin,
        json={"name": "OmiCRM", "description": None, "review_rules": "- tenantId everywhere"},
    )
    assert patched.status_code == 200 and patched.json()["review_rules"] == "- tenantId everywhere"
    kept = client.patch(
        f"/api/v1/business-projects/{bp.id}",
        headers=admin,
        json={"name": "OmiCRM", "description": "x"},
    )
    assert kept.json()["review_rules"] == "- tenantId everywhere"  # omitted = unchanged
    repo = client.patch(
        f"/api/v1/projects/{world.project.id}",
        headers=admin,
        json={
            "review_rules_paths": ".claude/rules/critical/tenant-isolation.md",
            "review_rules": "  ",
        },
    )
    assert repo.status_code == 200
    assert repo.json()["review_rules_paths"] == ".claude/rules/critical/tenant-isolation.md"
    assert repo.json()["review_rules"] is None
    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "project.update").order_by(AuditLog.id.desc())
    ).first()
    assert audit is not None and audit.details == {
        "review_rules_paths": 42,
        "review_rules": 2,
    }  # sizes only
    assert (
        client.patch(f"/api/v1/projects/{world.project.id}", headers=admin, json={}).status_code
        == 422
    )

    world.an_review.verdict, world.an_review.risk_score = "block", 10
    world.an_review.intent = {
        "summary": "Thêm parser",
        "matches_intent": True,
        "altitude": "right",
        "concerns": [],
    }
    db_session.flush()
    detail = client.get(f"/api/v1/reviews/{world.an_review.id}", headers=as_user(world.an)).json()
    assert (detail["verdict"], detail["risk_score"], detail["trigger"]) == ("block", 10, "webhook")
    assert detail["intent"]["summary"] == "Thêm parser"
    blocked = client.get(
        "/api/v1/reviews", params={"verdict": "block"}, headers=as_user(world.lead)
    ).json()
    assert [r["id"] for r in blocked["items"]] == [world.an_review.id]
    assert (
        client.get(
            "/api/v1/reviews", params={"verdict": "approve"}, headers=as_user(world.lead)
        ).json()["items"]
        == []
    )


def test_repo_profiles_are_readable_and_admins_can_refresh(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    world.project.profile = {"kind": "backend-service", "summary": "Demo", "version": 1}
    db_session.flush()
    rows = client.get("/api/v1/repos/profiles", headers=as_user(world.an)).json()
    assert rows[0]["profile"]["summary"] == "Demo" and rows[0]["deep_review"] is True
    denied = client.post(
        f"/api/v1/projects/{world.project.id}/profile/refresh", headers=as_user(world.lead)
    )
    assert denied.status_code == 403
    queued = client.post(
        f"/api/v1/projects/{world.project.id}/profile/refresh", headers=as_user(world.admin)
    )
    assert queued.status_code == 202
    assert queue.jobs[-1][0] == "app.workers.review_jobs.refresh_profile_job"
    off = client.patch(
        f"/api/v1/projects/{world.project.id}",
        headers=as_user(world.admin),
        json={"deep_review": False},
    )
    assert off.status_code == 200 and off.json()["deep_review"] is False
    world.project.allow_external_llm = False
    db_session.flush()
    refused = client.post(
        f"/api/v1/projects/{world.project.id}/profile/refresh", headers=as_user(world.admin)
    )
    assert refused.status_code == 409
