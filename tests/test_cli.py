import io
import json
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import CliError, build_parser, run
from app.collector.gitlab.client import GitLabClient
from app.crypto import SecretBox
from app.models import Connection, Project
from tests.collector.fake_gitlab import API, BASE, mount

pytestmark = pytest.mark.integration

BOX = SecretBox(Fernet.generate_key().decode())


def make_client(connection: Connection) -> GitLabClient:
    return GitLabClient(connection.base_url, BOX.decrypt(connection.token_encrypted))


def cli(session: Session, *argv: str, enqueued: list[int] | None = None) -> tuple[bool, str]:
    out = io.StringIO()
    sink = enqueued if enqueued is not None else []
    ok = run(build_parser().parse_args(argv), session, BOX, make_client, out, sink.append)
    return ok, out.getvalue()


def setup_project(session: Session) -> Project:
    cli(session, "connections", "add", "--url", BASE + "/", "--token", "glpat-x")
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        cli(session, "projects", "add", "1001")
    return session.scalars(select(Project)).one()


def test_connections_add_encrypts_and_list_hides_secrets(db_session: Session) -> None:
    ok, out = cli(db_session, "connections", "add", "--url", BASE + "/", "--token", "glpat-x")
    assert ok and "added" in out
    connection = db_session.scalars(select(Connection)).one()
    assert connection.base_url == BASE
    assert BOX.decrypt(connection.token_encrypted) == "glpat-x"
    assert connection.webhook_secret_encrypted  # generated
    _, listing = cli(db_session, "connections", "list")
    assert "glpat" not in listing and BASE in listing


def test_projects_add_reads_gitlab_and_is_idempotent(db_session: Session) -> None:
    project = setup_project(db_session)
    assert (project.path_with_namespace, project.default_branch) == (
        "demo/codepulse-sample",
        "main",
    )
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        _, out = cli(db_session, "projects", "add", "1001")
    assert "already registered" in out


def test_register_webhook_posts_secret_once(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_BASE_URL", "https://codepulse.example.com")
    from app.config import get_settings

    get_settings.cache_clear()
    project = setup_project(db_session)
    hooks: list[dict[str, object]] = []
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{API}/projects/1001/hooks").mock(
            side_effect=lambda r: httpx.Response(200, json=hooks)
        )

        def add(request: httpx.Request) -> httpx.Response:
            hooks.append(json.loads(request.content))
            return httpx.Response(201, json=hooks[-1])

        router.post(f"{API}/projects/1001/hooks").mock(side_effect=add)
        for _ in range(2):
            cli(db_session, "projects", "register-webhook", str(project.id))
    get_settings.cache_clear()

    (hook,) = hooks
    assert hook["url"] == "https://codepulse.example.com/api/v1/webhooks/gitlab"
    connection = db_session.get(Connection, project.connection_id)
    assert connection is not None and connection.webhook_secret_encrypted
    assert hook["token"] == BOX.decrypt(connection.webhook_secret_encrypted)
    assert hook["merge_requests_events"] is True


def test_sync_inline_and_enqueue(db_session: Session) -> None:
    project = setup_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        ok, out = cli(db_session, "projects", "sync", str(project.id))
    assert ok and "domain.commits: 4" in out
    queued: list[int] = []
    cli(db_session, "projects", "sync", str(project.id), "--enqueue", enqueued=queued)
    assert queued == [project.id]


def test_unknown_project_is_a_cli_error(db_session: Session) -> None:
    with pytest.raises(CliError, match="not found"):
        cli(db_session, "projects", "sync", "999")


def test_identity_import_reports_errors_and_signals_rollback(
    db_session: Session, tmp_path: Path
) -> None:
    users = tmp_path / "users.csv"
    users.write_text("full_name,primary_email,team_name,role,joined_at\nX,bad,,m,2025-01-01\n")
    ok, out = cli(db_session, "identity", "import", "--users", str(users))
    assert not ok
    assert "users.csv:2: invalid primary_email" in out
    _, unmapped = cli(db_session, "identity", "unmapped")
    assert unmapped == ""


def test_rotate_replaces_token_and_secret(db_session: Session) -> None:
    cli(db_session, "connections", "add", "--url", BASE, "--token", "glpat-old")
    connection = db_session.scalars(select(Connection)).one()
    old_secret = BOX.decrypt(connection.webhook_secret_encrypted or "")
    ok, out = cli(
        db_session,
        "connections",
        "rotate",
        str(connection.id),
        "--token",
        "glpat-new",
        "--webhook-secret",
    )
    assert ok and "token replaced" in out and "register-webhook" in out
    assert BOX.decrypt(connection.token_encrypted) == "glpat-new"
    new_secret = BOX.decrypt(connection.webhook_secret_encrypted or "")
    assert new_secret != old_secret and len(new_secret) >= 32
    with pytest.raises(CliError, match="--token and/or"):
        cli(db_session, "connections", "rotate", str(connection.id))
