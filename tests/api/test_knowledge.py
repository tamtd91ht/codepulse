"""Review knowledge API (docs/specs/19 §3.5) and the build job, against a real database."""

import io
import tarfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditLog, BusinessProject, ReviewKnowledge, ReviewKnowledgeEdit
from app.review.knowledge import load_for_review
from app.review.progress import MemoryProgress
from app.review.providers.base import ReviewPrompt, ReviewResult
from app.workers.knowledge_jobs import first_build_allowed, month_spend_usd, progress_id, run_build
from tests.api.conftest import FakeQueue, World, grant
from tests.collector.fake_gitlab import API

pytestmark = pytest.mark.integration

HEAD = "f" * 40
TREE = "1" * 40


def knowledge_row(session: Session, world: World, version: int = 1, **kw: Any) -> ReviewKnowledge:
    data = {
        "invariants": [
            {
                "id": "repo:inv-1",
                "statement": "Mọi truy vấn phải lọc theo tenant_id",
                "severity": "high",
                "signals": ["db_query"],
                "source": {"path": ".claude/rules/tenant.md", "lines": "1-3"},
            }
        ],
        "checks": [
            {
                "id": "repo:chk-1",
                "name": "Migration",
                "triggers": {"signals": ["migration"]},
                "items": ["a", "b", "c", "d", "e"],
            }
        ],
        "domain": {"summary": "CRM"},
        "omitted": [{"path": "x", "reason": "tooling"}],
        "meta": {"cost_usd": 0.4},
    }
    row = ReviewKnowledge(
        scope="project",
        project_id=world.project.id,
        version=version,
        schema_version=1,
        data=data,
        sources={"claude_tree_sha": TREE, "default_branch_sha": HEAD},
        status=kw.pop("status", "active"),
        built_by="manual",
        **kw,
    )
    session.add(row)
    session.flush()
    return row


def gitlab(router: respx.MockRouter, tree_sha: str = TREE) -> None:
    router.route(host="testserver").pass_through()
    router.get(url__regex=rf"{API}/projects/1001/repository/tree(\?.*)?$").mock(
        return_value=httpx.Response(
            200, json=[{"id": tree_sha, "name": ".claude", "type": "tree", "path": ".claude"}]
        )
    )


def test_get_knowledge_shows_active_build_edits_and_staleness(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    path = f"/api/v1/projects/{world.project.id}/knowledge"
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        empty = client.get(path, headers=as_user(world.an)).json()
        assert empty == {
            "project_id": world.project.id,
            "active": None,
            "edits": [],
            "stats": None,
            "building": False,
            "last_failure": None,
        }
        knowledge_row(db_session, world, version=1, status="superseded")
        row = knowledge_row(db_session, world, version=2)
        db_session.add(
            ReviewKnowledgeEdit(
                scope="project",
                project_id=world.project.id,
                item_id="repo:inv-1",
                action="set_severity",
                payload={"severity": "low"},
                user_id=world.lead.id,
            )
        )
        db_session.flush()
        body = client.get(path, headers=as_user(world.an)).json()  # any signed-in user
    active = body["active"]
    assert active["id"] == row.id and active["version"] == 2 and active["status"] == "active"
    assert active["stale"] is False and active["stale_reasons"] == []
    assert active["approved_by"] is None
    assert active["counts"] == {
        "invariants": 1,
        "checks": 1,
        "severity_rules": 0,
        "code_conventions": 0,
        "omitted": 1,
        "conflicts": 0,
    }
    assert active["data"]["invariants"][0]["id"] == "repo:inv-1"
    assert [e["action"] for e in body["edits"]] == ["set_severity"]

    with respx.mock(assert_all_called=False) as router:
        gitlab(router, tree_sha="2" * 40)  # .claude moved since the build
        moved = client.get(path, headers=as_user(world.an)).json()["active"]
    assert moved["stale"] is True
    assert moved["stale_reasons"] == [{"source": "claude", "built": "1" * 12, "current": "2" * 12}]

    assert client.get(path, headers=as_user(world.an)).status_code == 200  # GitLab down: not stale
    assert (
        client.get("/api/v1/projects/9999/knowledge", headers=as_user(world.an)).status_code == 404
    )
    assert client.get(path).status_code == 401


def test_versions_list_and_detail(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    knowledge_row(db_session, world, version=1, status="superseded")
    knowledge_row(
        db_session,
        world,
        version=2,
        approved_by_user_id=world.lead.id,
        approved_at=datetime.now(UTC),
    )
    base = f"/api/v1/projects/{world.project.id}/knowledge/versions"
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        versions = client.get(base, headers=as_user(world.an)).json()
        assert [(v["version"], v["status"]) for v in versions] == [(2, "active"), (1, "superseded")]
        assert versions[0]["approved_at"] is not None and versions[0]["counts"]["invariants"] == 1
        detail = client.get(f"{base}/1", headers=as_user(world.an)).json()
        assert detail["version"] == 1 and detail["status"] == "superseded"
        assert detail["data"]["domain"]["summary"] == "CRM"
        assert client.get(f"{base}/7", headers=as_user(world.an)).status_code == 404


def test_summary_lists_each_repo_of_the_du_an_without_gitlab(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = BusinessProject(name="OmiCRM")
    db_session.add(bp)
    db_session.flush()
    world.project.business_project_id = bp.id
    grant(db_session, world.an, bp.id)  # An sees the dự án the repo moved to
    knowledge_row(db_session, world, version=1, status="superseded")
    row = knowledge_row(db_session, world, version=2, approved_by_user_id=world.lead.id)
    path = f"/api/v1/knowledge/summary?business_project_id={bp.id}"
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()  # any GitLab call would fail the test
        summary = client.get(path, headers=as_user(world.an)).json()
    assert summary == [
        {
            "project_id": world.project.id,
            "version": 2,
            "created_at": summary[0]["created_at"],
            "approved": True,
            "counts": {
                "invariants": 1,
                "checks": 1,
                "severity_rules": 0,
                "code_conventions": 0,
                "omitted": 1,
                "conflicts": 0,
            },
            "building": False,
            "failed": False,
        }
    ]
    db_session.add(
        ReviewKnowledge(
            scope="project",
            project_id=world.project.id,
            version=3,
            schema_version=1,
            data={},
            status="failed",
            built_by="auto",
            created_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
    )
    db_session.flush()
    again = client.get(path, headers=as_user(world.an)).json()[0]
    assert again["version"] == row.version and again["failed"] is True
    other = "/api/v1/knowledge/summary?business_project_id=9999"
    assert client.get(other, headers=as_user(world.an)).status_code == 404
    assert client.get(path).status_code == 401


class StatusQueue(FakeQueue):
    """A queue that can also say whether a job is still waiting (RQ's fetch_job)."""

    def __init__(self, active: set[str]) -> None:
        super().__init__()
        self.active = active

    def fetch_job(self, job_id: str) -> Any:
        if job_id not in self.active:
            return None

        class Job:
            def get_status(self, refresh: bool = False) -> str:
                return "queued"

        return Job()


def test_refresh_needs_a_manager_queues_once_and_is_audited(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    path = f"/api/v1/projects/{world.project.id}/knowledge/refresh"
    assert client.post(path, headers=as_user(world.an)).status_code == 403
    # Leading a team is not enough: the dự án's "manage" grant is (docs/specs/09).
    assert client.post(path, headers=as_user(world.lead)).status_code == 403
    grant(db_session, world.lead, world.project.business_project_id, "manage")
    response = client.post(path, headers=as_user(world.lead))
    assert response.status_code == 202
    job_id = f"knowledge-{world.project.id}"
    assert response.json() == {"job_id": job_id}
    (f, args, kwargs) = queue.jobs[0]
    assert f == "app.workers.knowledge_jobs.build_knowledge_job" and args == (world.project.id,)
    assert kwargs["job_id"] == job_id and kwargs["requested_by"] == world.lead.id
    assert kwargs["built_by"] == "manual"
    actions = list(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.action.like("project.knowledge%"))
        )
    )
    assert actions == ["project.knowledge_refresh"]

    world.project.allow_external_llm = False
    db_session.flush()
    assert client.post(path, headers=as_user(world.admin)).status_code == 409


def test_refresh_refuses_while_a_build_is_queued(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.api.deps import get_queue
    from app.api.main import app

    grant(db_session, world.lead, world.project.business_project_id, "manage")
    busy = StatusQueue({f"knowledge-{world.project.id}"})
    app.dependency_overrides[get_queue] = lambda: busy
    path = f"/api/v1/projects/{world.project.id}/knowledge"
    assert client.post(f"{path}/refresh", headers=as_user(world.lead)).status_code == 409
    assert busy.jobs == []
    assert client.get(path, headers=as_user(world.an)).json()["building"] is True


def test_approve_sets_approver_and_audits(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    path = f"/api/v1/projects/{world.project.id}/knowledge/approve"
    grant(db_session, world.lead, world.project.business_project_id, "manage")
    assert client.post(path, headers=as_user(world.lead)).status_code == 404  # nothing yet
    row = knowledge_row(db_session, world)
    assert client.post(path, headers=as_user(world.an)).status_code == 403
    with respx.mock(assert_all_called=False) as router:
        gitlab(router)
        body = client.post(path, headers=as_user(world.lead)).json()
    assert body["approved_by"] == "Lead" and body["approved_at"] is not None
    db_session.refresh(row)
    assert row.approved_by_user_id == world.lead.id
    assert (
        db_session.scalar(select(AuditLog).where(AuditLog.action == "project.knowledge_approve"))
        is not None
    )


def test_edits_are_validated_against_the_active_build_and_deletable(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    path = f"/api/v1/projects/{world.project.id}/knowledge/edits"
    grant(db_session, world.lead, world.project.business_project_id, "manage")
    lead = as_user(world.lead)
    manual = {"item_id": "m1", "action": "add_invariant", "payload": {"statement": "Thêm"}}
    assert client.post(path, json=manual, headers=as_user(world.an)).status_code == 403
    # Without a build only manual invariants make sense.
    assert (
        client.post(
            path, json={"item_id": "repo:inv-1", "action": "disable", "payload": {}}, headers=lead
        ).status_code
        == 404
    )
    created = client.post(path, json=manual, headers=lead)
    assert created.status_code == 201 and created.json()["action"] == "add_invariant"
    manual_id = created.json()["item_id"]
    assert manual_id.startswith("manual:") and manual_id != "manual:m1"  # the server names it
    # item_id is not needed for add_invariant, and a payload is a sentence, not a document.
    assert (
        client.post(
            path, json={"action": "add_invariant", "payload": {"statement": "Thêm 2"}}, headers=lead
        ).status_code
        == 201
    )
    assert (
        client.post(
            path, json={**manual, "payload": {"statement": "x" * 5000}}, headers=lead
        ).status_code
        == 422
    )
    assert (
        client.post(path, json={"action": "disable", "payload": {}}, headers=lead).status_code
        == 422
    )

    knowledge_row(db_session, world)
    assert (
        client.post(
            path, json={"item_id": "nope", "action": "disable", "payload": {}}, headers=lead
        ).status_code
        == 404
    )
    assert (
        client.post(
            path,
            json={
                "item_id": "repo:inv-1",
                "action": "set_severity",
                "payload": {"severity": "huge"},
            },
            headers=lead,
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, json={"item_id": "repo:inv-1", "action": "reword", "payload": {}}, headers=lead
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, json={"item_id": "repo:inv-1", "action": "bogus", "payload": {}}, headers=lead
        ).status_code
        == 422
    )
    ok = client.post(
        path,
        json={"item_id": "repo:inv-1", "action": "set_severity", "payload": {"severity": "low"}},
        headers=lead,
    )
    assert ok.status_code == 201
    edit_id = ok.json()["id"]

    # The review sees the edits applied.
    loaded = load_for_review(db_session, world.project, TREE, None)
    assert loaded is not None and loaded.stale is False and loaded.approved is False
    by_id = {i.id: i for i in loaded.knowledge.invariants}
    assert by_id["repo:inv-1"].severity == "low" and manual_id in by_id
    assert loaded.knowledge.meta.version == 1
    assert load_for_review(db_session, world.project, "9" * 40, None).stale is True  # type: ignore[union-attr]

    for n in range(8):  # two manual invariants exist already
        client.post(path, json={**manual, "item_id": f"x{n}"}, headers=lead)
    assert (
        client.post(path, json={**manual, "item_id": "one-too-many"}, headers=lead).status_code
        == 422
    )

    assert client.delete(f"{path}/{edit_id}", headers=as_user(world.an)).status_code == 403
    assert client.delete(f"{path}/{edit_id}", headers=lead).status_code == 204
    assert client.delete(f"{path}/{edit_id}", headers=lead).status_code == 404
    assert db_session.get(ReviewKnowledgeEdit, edit_id) is None
    actions = set(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.action.like("project.knowledge%"))
        )
    )
    assert actions == {"project.knowledge_edit", "project.knowledge_edit_delete"}


# -- the build job ---------------------------------------------------------------------------------


class FakeGitLab:
    """Enough of GitLabClient for the build: the head commit, the tree and an archive."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def __enter__(self) -> "FakeGitLab":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def commit(self, project_id: int, sha: str) -> dict[str, Any]:
        return {"id": HEAD}

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert path.endswith("/repository/tree")
        return [{"id": TREE, "name": ".claude", "type": "tree"}]

    def archive(
        self, project_id: int, sha: str, dest: Path, max_bytes: int, path: str | None = None
    ) -> int:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            tar.add(self.root, arcname=f"repo-{sha}")
        dest.write_bytes(buffer.getvalue())
        return len(buffer.getvalue())


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.prompts: list[ReviewPrompt] = []

    def review(self, prompt: ReviewPrompt) -> ReviewResult:
        self.prompts.append(prompt)
        return ReviewResult(
            output={
                "invariants": [
                    {
                        "id": "tenant",
                        "statement": "Mọi truy vấn phải lọc theo tenant_id",
                        "severity": "critical",
                        "source": {"path": ".claude/rules/tenant.md", "lines": "1"},
                    }
                ],
                "domain": {"summary": "CRM"},
                "index": [{"path": ".claude/rules/tenant.md", "purpose": "tenant"}],
            },
            model="fake-1",
            cost_usd=0.25,
        )


def test_run_build_compiles_the_default_branch_and_stores_a_new_version(
    world: World, db_session: Session, settings: Settings, tmp_path: Path
) -> None:
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "rules" / "tenant.md").write_text("# Tenant\nalways filter", "utf-8")
    (tmp_path / "README.md").write_text('# demo\nCRM. api_key="abcdefgh12345678"', "utf-8")
    world.project.default_branch = "main"
    # Read before the commit: after it, the attribute reloads and opens a transaction.
    project_id, lead_id = world.project.id, world.lead.id
    db_session.commit()  # the job opens its own short transactions
    provider = FakeProvider()
    progress = MemoryProgress()

    first = run_build(
        db_session,
        project_id,
        settings,
        probe=False,
        requested_by=lead_id,
        progress=progress,
        provider=provider,
        make_client=lambda connection: FakeGitLab(tmp_path),
    )
    assert first["built"] is True and first["version"] == 1 and first["invariants"] == 1
    assert first["cost_usd"] == 0.25 and first["shared"] is False
    assert len(provider.prompts) == 1
    assert "### .claude/rules/tenant.md" in provider.prompts[0].user
    assert "abcdefgh12345678" not in provider.prompts[0].user
    ended = progress.ended[0]
    assert ended["review_id"] == progress_id(project_id) and ended["kind"] == "knowledge"
    assert ended["status"] == "done" and ended["requested_by"] == "Lead"

    second = run_build(
        db_session,
        project_id,
        settings,
        probe=False,
        provider=provider,
        make_client=lambda connection: FakeGitLab(tmp_path),
        built_by="auto",
    )
    assert second["version"] == 2
    rows = list(
        db_session.scalars(
            select(ReviewKnowledge)
            .where(ReviewKnowledge.project_id == project_id)
            .order_by(ReviewKnowledge.version)
        )
    )
    assert [(r.version, r.status, r.built_by) for r in rows] == [
        (1, "superseded", "manual"),
        (2, "active", "auto"),
    ]
    active = rows[1]
    sources = active.sources or {}
    assert sources["claude_tree_sha"] == TREE and sources["default_branch_sha"] == HEAD
    assert active.data["invariants"][0]["id"] == "repo:tenant"
    assert active.data["meta"]["code_probe"] == {"skipped": "no agentic provider"}
    assert active.data["meta"]["cost_usd"] == 0.25


class JunkProvider(FakeProvider):
    """Paid, but the answer is not JSON."""

    def review(self, prompt: ReviewPrompt) -> ReviewResult:
        self.prompts.append(prompt)
        return ReviewResult(output="not json at all", model="fake-1", cost_usd=0.25)


def test_run_build_that_paid_and_failed_keeps_a_failed_row_with_its_cost(
    world: World, db_session: Session, settings: Settings, tmp_path: Path
) -> None:
    (tmp_path / "README.md").write_text("# demo", "utf-8")
    before = month_spend_usd(db_session)
    # Read before the commit: after it, the attribute reloads and opens a transaction.
    project_id = world.project.id
    db_session.commit()  # the job opens its own short transactions
    progress = MemoryProgress()

    result = run_build(
        db_session,
        project_id,
        settings,
        probe=False,
        progress=progress,
        provider=JunkProvider(),
        make_client=lambda connection: FakeGitLab(tmp_path),
        built_by="first_review",
    )
    assert result == {"built": False, "reason": "unusable answer", "cost_usd": 0.25}
    assert progress.ended[0]["status"] == "failed"
    rows = list(
        db_session.scalars(select(ReviewKnowledge).where(ReviewKnowledge.project_id == project_id))
    )
    assert [(r.version, r.status, r.built_by) for r in rows] == [(1, "failed", "first_review")]
    meta = rows[0].data["meta"]
    assert meta["cost_usd"] == 0.25 and meta["error"].startswith("ValueError:")
    # The money is in the month's spend, no review rebuilds within the hour, and the review
    # itself sees no knowledge (the .claude guidance fallback).
    assert month_spend_usd(db_session) == pytest.approx(before + 0.25)
    assert first_build_allowed(db_session, project_id, redis=None, queue=None) is False
    assert load_for_review(db_session, world.project, TREE, None) is None


def test_run_build_refuses_without_budget_or_external_llm(
    world: World, db_session: Session, settings: Settings, tmp_path: Path
) -> None:
    world.project.allow_external_llm = False
    project_id = world.project.id
    db_session.commit()
    progress = MemoryProgress()
    refused = run_build(
        db_session, project_id, settings, probe=False, provider=FakeProvider(), progress=progress
    )
    assert refused == {"built": False, "reason": "external LLM disabled for this repo"}
    # Not silent: the live panel's recent list says why the asked build did not run.
    (noted,) = progress.ended
    assert (noted["review_id"], noted["kind"], noted["status"]) == (
        progress_id(project_id),
        "knowledge",
        "skipped",
    )
    assert noted["error"] == "repo không cho gửi mã ra LLM bên ngoài"

    world.project.allow_external_llm = True
    world.an_review.started_at = datetime.now(UTC)
    world.an_review.cost_usd = Decimal(199)
    db_session.commit()
    broke = Settings(session_secret=SecretStr("s" * 48), monthly_llm_budget_usd=100)
    refused = run_build(db_session, project_id, broke, probe=False, provider=FakeProvider())
    assert refused == {"built": False, "reason": "monthly LLM budget reached"}
