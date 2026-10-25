import io
import tarfile
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import gitlab_repo_path
from app.models import AuditLog, BusinessProject
from tests.api.conftest import World

API = "https://gitlab.example.com/api/v4"


def archive(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"claude-config-abc/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def business(db_session: Session, world: World) -> BusinessProject:
    bp = BusinessProject(name="Thu phí")
    db_session.add(bp)
    db_session.commit()
    return bp


def test_admin_sets_checks_and_clears_the_shared_claude_repo(
    client: TestClient, world: World, as_user: Any, business: BusinessProject, db_session: Session
) -> None:
    admin = as_user(world.admin)
    path = f"/api/v1/business-projects/{business.id}/claude-repo"
    body = {
        "connection_id": world.project.connection_id,
        "path": "demo/claude-config",
        "ref": "main",
        "include": "skills/java/SKILL.md",
    }
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        router.get(f"{API}/projects/demo%2Fclaude-config").respond(
            json={"id": 2002, "path_with_namespace": "demo/claude-config", "default_branch": "main"}
        )
        router.get(f"{API}/projects/demo%2Fnope").respond(404)
        router.get(f"{API}/projects/2002/repository/branches/main").respond(json={"name": "main"})
        router.get(f"{API}/projects/2002/repository/branches/dev").respond(404)
        router.get(f"{API}/projects/2002/repository/commits/main").respond(json={"id": "d" * 40})
        router.get(f"{API}/projects/2002/repository/archive.tar.gz").mock(
            return_value=httpx.Response(
                200,
                content=archive(
                    {
                        "skills/code-review/SKILL.md": "review",
                        "skills/java/SKILL.md": "java",
                        "skills/deploy/SKILL.md": "deploy",
                        "commands/review-mr.md": "---\ndescription: Review MR\n---\nsteps",
                    }
                ),
            )
        )

        missing = client.put(path, headers=admin, json={**body, "path": "demo/nope"})
        assert missing.status_code == 422
        bad_branch = client.put(path, headers=admin, json={**body, "ref": "dev"})
        assert bad_branch.status_code == 422
        # The repo's web link, as pasted from the browser, works like the bare path.
        link = {**body, "path": "https://gitlab.example.com/demo/claude-config/-/tree/main"}
        assert client.put(path, headers=admin, json=link).status_code == 204
        assert client.put(path, headers=admin, json=body).status_code == 204

        listed = client.get("/api/v1/business-projects", headers=admin).json()
        (row,) = [b for b in listed if b["id"] == business.id]
        assert (row["claude_repo_path"], row["claude_repo_ref"]) == ("demo/claude-config", "main")
        assert row["claude_repo_include"] == "skills/java/SKILL.md"

        preview = client.post(f"{path}/preview", headers=admin)
    assert preview.status_code == 200
    out = preview.json()
    assert out["info"]["status"] == "used" and out["info"]["sha"] == "d" * 12
    # Always quoted: the review command, the pinned skill, a skill named for review.
    assert [(d["path"], d["procedure"]) for d in out["loaded"]] == [
        ("commands/review-mr.md", True),
        ("skills/java/SKILL.md", False),
        ("skills/code-review/SKILL.md", False),
    ]
    assert out["loaded"][0]["name"] == "/review-mr"
    assert out["loaded"][0]["description"] == "Review MR"
    # The rest is chosen per commit, or Read by the agent from the index.
    assert [(d["path"], d["reason"]) for d in out["not_loaded"]] == [
        ("skills/deploy/SKILL.md", "by_commit")
    ]

    assert client.delete(path, headers=admin).status_code == 204
    db_session.refresh(business)
    assert business.claude_repo_path is None and business.claude_repo_external_id is None
    assert client.post(f"{path}/preview", headers=admin).status_code == 404
    actions = db_session.scalars(
        select(AuditLog.action).where(AuditLog.action == "business_project.claude_repo")
    ).all()
    assert len(actions) == 3


@pytest.mark.parametrize(
    ("pasted", "path"),
    [
        ("omicrm/claude-config", "omicrm/claude-config"),
        (
            "https://gitlab.vihatgroup.com/vihat-saas/omicx/service/cloud-saas-omicx-claude-ai",
            "vihat-saas/omicx/service/cloud-saas-omicx-claude-ai",
        ),
        ("https://gitlab.example.com/a/b/-/tree/develop/skills?ref_type=heads", "a/b"),
        ("https://gitlab.example.com/a/b.git", "a/b"),
        ("git@gitlab.example.com:a/b/c.git", "a/b/c"),
        ("  /a/b/  ", "a/b"),
    ],
)
def test_the_repo_path_is_taken_from_a_link_or_clone_url(pasted: str, path: str) -> None:
    assert gitlab_repo_path(pasted) == path


@pytest.mark.parametrize("pasted", ["claude-config", "https://gitlab.example.com/", "a b/c", ""])
def test_a_value_without_group_and_repo_is_refused(pasted: str) -> None:
    with pytest.raises(ValueError, match="nhóm/repo"):
        gitlab_repo_path(pasted)


def test_shared_claude_repo_is_admin_only(
    client: TestClient, world: World, as_user: Any, business: BusinessProject
) -> None:
    path = f"/api/v1/business-projects/{business.id}/claude-repo"
    body = {"connection_id": world.project.connection_id, "path": "demo/claude-config"}
    assert client.put(path, headers=as_user(world.lead), json=body).status_code == 403
    assert client.delete(path, headers=as_user(world.lead)).status_code == 403
