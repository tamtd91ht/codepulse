from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.main import app
from app.api.routers.commands import get_command_queue, get_run_store
from app.commands import catalog
from app.config import Settings, get_settings
from app.models import AuditLog, BusinessProject, CommandRun, Project
from tests.api.conftest import FakeQueue, World

RUNS = "/api/v1/admin/command-runs"


class FakeStore:
    def __init__(self) -> None:
        self.cancelled: set[int] = set()
        self.log: dict[int, list[dict[str, Any]]] = {}

    def cancel(self, run_id: int) -> None:
        self.cancelled.add(run_id)

    def entries(self, run_id: int, after: int = 0) -> list[dict[str, Any]]:
        return self.log.get(run_id, [])[after:]

    def result(self, run_id: int) -> str | None:
        return "Done" if run_id in self.log else None


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "runs"


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def commands_client(
    client: TestClient, settings: Settings, queue: FakeQueue, store: FakeStore, runs_dir: Path
) -> Iterator[TestClient]:
    local = settings.model_copy(update={"command_runs_dir": str(runs_dir)})
    app.dependency_overrides.update(
        {
            get_settings: lambda: local,
            get_command_queue: lambda: queue,
            get_run_store: lambda: store,
        }
    )
    yield client


def _start(client: TestClient, headers: dict[str, str], **body: Any) -> Any:
    return client.post(RUNS, headers=headers, json={"source": "repo", "command": "report", **body})


@pytest.mark.parametrize("who", ["lead", "manager", "an"])
def test_only_admins_run_commands(
    commands_client: TestClient, world: World, as_user: Any, who: str
) -> None:
    headers = as_user(getattr(world, who))
    assert commands_client.get(RUNS, headers=headers).status_code == 403
    assert _start(commands_client, headers, project_id=world.project.id).status_code == 403
    listing = commands_client.get(
        "/api/v1/admin/commands", headers=headers, params={"project_id": world.project.id}
    )
    assert listing.status_code == 403


def test_the_off_switch_removes_the_feature(
    commands_client: TestClient, world: World, as_user: Any, settings: Settings
) -> None:
    admin = as_user(world.admin)
    assert commands_client.get("/api/v1/auth/me", headers=admin).json()["command_runs"] is True
    off = settings.model_copy(update={"command_runs_enabled": False})
    app.dependency_overrides[get_settings] = lambda: off
    assert commands_client.get(RUNS, headers=admin).status_code == 404
    assert _start(commands_client, admin, project_id=world.project.id).status_code == 404
    assert commands_client.get("/api/v1/auth/me", headers=admin).json()["command_runs"] is False


def test_a_run_is_queued_once_per_repo_and_audited(
    commands_client: TestClient, world: World, as_user: Any, queue: FakeQueue, db_session: Session
) -> None:
    admin = as_user(world.admin)
    world.project.default_branch = "dev"
    db_session.flush()
    first = _start(commands_client, admin, project_id=world.project.id, arguments=" W43 ")
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["status"] == "queued" and body["ref"] == "dev" and body["arguments"] == "W43"
    ((func, args, kwargs),) = queue.jobs
    assert func == "app.commands.jobs.run_command_job" and args == (body["id"],)
    assert kwargs["job_id"] == f"command-{body['id']}" and kwargs["job_timeout"] > 30 * 60

    again = _start(commands_client, admin, project_id=world.project.id)
    assert again.status_code == 409
    entry = db_session.scalars(select(AuditLog).where(AuditLog.action == "command.run")).one()
    assert entry.details is not None and entry.details["command"] == "report"


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"command": "-p"}, 422),
        ({"command": "a b"}, 422),
        ({"ref": "--upload-pack=x"}, 422),
        ({"ref": "a..b"}, 422),
        ({"source": "shared"}, 422),  # the dự án has no shared .claude repo
    ],
)
def test_bad_runs_are_refused(
    commands_client: TestClient, world: World, as_user: Any, body: dict[str, Any], status: int
) -> None:
    response = _start(commands_client, as_user(world.admin), project_id=world.project.id, **body)
    assert response.status_code == status, response.text


def test_a_repo_kept_out_of_review_is_not_sent_to_the_llm(
    commands_client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    world.project.review_enabled = False
    db_session.flush()
    response = _start(commands_client, as_user(world.admin), project_id=world.project.id)
    assert response.status_code == 409


def test_a_shared_command_may_run_without_a_repo(
    commands_client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = db_session.get(BusinessProject, world.project.business_project_id)
    assert bp is not None
    bp.claude_repo_connection_id = world.project.connection_id
    bp.claude_repo_external_id = 77
    bp.claude_repo_path = "demo/claude"
    db_session.flush()
    admin = as_user(world.admin)
    response = _start(commands_client, admin, business_project_id=bp.id, source="shared")
    assert response.status_code == 202, response.text
    assert response.json()["project_id"] is None and response.json()["ref"] is None
    repo_command = _start(commands_client, admin, business_project_id=bp.id)
    assert repo_command.status_code == 422


def test_cancel_a_queued_run(
    commands_client: TestClient, world: World, as_user: Any, store: FakeStore
) -> None:
    admin = as_user(world.admin)
    run_id = _start(commands_client, admin, project_id=world.project.id).json()["id"]
    done = commands_client.post(f"{RUNS}/{run_id}/cancel", headers=admin)
    assert done.status_code == 200 and done.json()["status"] == "cancelled"
    assert run_id in store.cancelled
    assert commands_client.post(f"{RUNS}/{run_id}/cancel", headers=admin).status_code == 409
    # The repo is free again.
    assert _start(commands_client, admin, project_id=world.project.id).status_code == 202


def _finished(db_session: Session, project: Project, download: str) -> CommandRun:
    run = CommandRun(
        project_id=project.id,
        source="repo",
        command="report:weekly",
        status="succeeded",
        files=[{"name": "report.txt", "size": 5}],
        meta={"download": download, "recognized": True},
        finished_at=datetime.now(UTC),
    )
    db_session.add(run)
    db_session.flush()
    return run


def test_the_output_is_downloaded_once_then_deleted(
    commands_client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    runs_dir: Path,
    store: FakeStore,
) -> None:
    run = _finished(db_session, world.project, "report.txt")
    folder = runs_dir / str(run.id)
    folder.mkdir(parents=True)
    (folder / "report.txt").write_text("hello", encoding="utf-8")
    store.log[run.id] = [{"kind": "start"}, {"kind": "end", "ok": True}]
    admin = as_user(world.admin)

    detail = commands_client.get(f"{RUNS}/{run.id}", headers=admin, params={"after": 1}).json()
    assert detail["download"] == "report.txt" and detail["files_deleted"] is False
    assert detail["log"] == [{"kind": "end", "ok": True}] and detail["log_next"] == 2
    assert detail["result"] == "Done" and "download" not in detail["meta"]

    got = commands_client.get(f"{RUNS}/{run.id}/download", headers=admin)
    assert got.status_code == 200 and got.text == "hello"
    assert "report.txt" in got.headers["content-disposition"]
    assert not folder.exists()
    db_session.expire_all()
    assert db_session.get(CommandRun, run.id).files_deleted_at is not None  # type: ignore[union-attr]
    assert commands_client.get(f"{RUNS}/{run.id}/download", headers=admin).status_code == 410


def test_a_download_path_outside_the_run_is_refused(
    commands_client: TestClient, world: World, as_user: Any, db_session: Session, runs_dir: Path
) -> None:
    run = _finished(db_session, world.project, "../secret.txt")
    runs_dir.mkdir(parents=True)
    (runs_dir / "secret.txt").write_text("no", encoding="utf-8")
    got = commands_client.get(f"{RUNS}/{run.id}/download", headers=as_user(world.admin))
    assert got.status_code == 410


def test_commands_are_listed_from_the_repo(
    commands_client: TestClient, world: World, as_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[int, str]] = []

    def fake(_client: Any, external_id: int, ref: str, _sensitive: list[str]) -> Any:
        seen.append((external_id, ref))
        return [catalog.Command(name="report", source="repo", path=".claude/commands/report.md")]

    monkeypatch.setattr(catalog, "repo_commands", fake)
    response = commands_client.get(
        "/api/v1/admin/commands",
        headers=as_user(world.admin),
        params={"project_id": world.project.id, "ref": "feature/x"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert seen == [(world.project.external_id, "feature/x")]
    assert [c["name"] for c in body["commands"]] == ["report"]
    assert body["ref"] == "feature/x" and body["shared_repo"] is None
