"""`codepulse` command line (docs/specs/13 "Cài đặt lần đầu", task 2.7)."""

import argparse
import secrets
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabError
from app.collector.sync import ClientFactory, client_factory, register_webhook, sync_project
from app.config import get_settings
from app.crypto import SecretBox, secret_box
from app.identity.autolink import unmapped_accounts
from app.identity.csv_import import CsvImportError, import_identity
from app.models import Connection, Project
from app.scoring.aggregate import recompute


class CliError(Exception):
    pass


class _Rollback(Exception):
    pass


def _connection(session: Session, connection_id: int | None) -> Connection:
    if connection_id is not None:
        connection = session.get(Connection, connection_id)
        if connection is None:
            raise CliError(f"connection {connection_id} not found")
        return connection
    connections = session.scalars(select(Connection)).all()
    if len(connections) != 1:
        raise CliError("several connections exist; pass --connection <id>")
    return connections[0]


def _project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise CliError(f"project {project_id} not found (see `codepulse projects list`)")
    return project


def connections_add(
    session: Session,
    box: SecretBox,
    out: TextIO,
    name: str,
    url: str,
    token: str,
    secret: str | None,
) -> Connection:
    connection = Connection(
        name=name,
        base_url=url.rstrip("/"),
        token_encrypted=box.encrypt(token),
        webhook_secret_encrypted=box.encrypt(secret or secrets.token_urlsafe(32)),
    )
    session.add(connection)
    session.flush()
    out.write(f"connection {connection.id} added: {connection.name} {connection.base_url}\n")
    return connection


def connections_rotate(
    session: Session,
    box: SecretBox,
    out: TextIO,
    connection_id: int,
    token: str | None,
    webhook_secret: str | None,
) -> None:
    """docs/RUNBOOK.md "Xoay token": new values are encrypted with APP_ENCRYPTION_KEY."""
    connection = _connection(session, connection_id)
    if token is None and webhook_secret is None:
        raise CliError("pass --token and/or --webhook-secret")
    if token is not None:
        connection.token_encrypted = box.encrypt(token)
        out.write(f"connection {connection.id}: token replaced; revoke the old one in GitLab\n")
    if webhook_secret is not None:
        new_secret = webhook_secret or secrets.token_urlsafe(32)
        connection.webhook_secret_encrypted = box.encrypt(new_secret)
        out.write(
            f"connection {connection.id}: webhook secret replaced; delete the old hooks and run "
            "`codepulse projects register-webhook <id>` for each project\n"
        )


def connections_list(session: Session, out: TextIO) -> None:
    for c in session.scalars(select(Connection).order_by(Connection.id)):
        out.write(f"{c.id}\t{c.name}\t{c.base_url}\n")  # never print tokens or secrets


def projects_add(
    session: Session,
    make_client: ClientFactory,
    out: TextIO,
    gitlab_id: int,
    connection_id: int | None,
) -> Project:
    connection = _connection(session, connection_id)
    existing = session.scalars(
        select(Project).where(
            Project.connection_id == connection.id, Project.external_id == gitlab_id
        )
    ).first()
    if existing:
        out.write(f"project {existing.id} already registered: {existing.path_with_namespace}\n")
        return existing
    with make_client(connection) as client:
        info = client.project(gitlab_id)
    project = Project(
        connection_id=connection.id,
        external_id=gitlab_id,
        path_with_namespace=info["path_with_namespace"],
        default_branch=info.get("default_branch"),
    )
    session.add(project)
    session.flush()
    out.write(f"project {project.id} added: {project.path_with_namespace}\n")
    return project


def projects_list(session: Session, out: TextIO) -> None:
    for p in session.scalars(select(Project).order_by(Project.id)):
        synced = p.last_synced_at.isoformat() if p.last_synced_at else "never"
        out.write(
            f"{p.id}\tgitlab:{p.external_id}\t{p.path_with_namespace}\t"
            f"review={'on' if p.review_enabled else 'off'}\t"
            f"tracked={'on' if p.tracked else 'off'}\tsynced={synced}\n"
        )


def projects_register_webhook(
    session: Session,
    make_client: ClientFactory,
    box: SecretBox,
    out: TextIO,
    project_id: int,
    base_url: str,
) -> None:
    project = _project(session, project_id)
    connection = _connection(session, project.connection_id)
    url, created = register_webhook(make_client, box, connection, project, base_url)
    if created:
        out.write(f"webhook registered for project {project.id}: {url}\n")
    else:
        out.write(f"webhook already registered for project {project.id}: {url}\n")


def identity_import(
    session: Session, out: TextIO, teams: Path | None, users: Path | None, accounts: Path | None
) -> bool:
    try:
        result = import_identity(session, teams=teams, users=users, accounts=accounts)
    except CsvImportError as exc:
        raise CliError(str(exc)) from exc
    if result.errors:
        for error in result.errors:
            out.write(f"error: {error}\n")
        out.write("nothing imported; fix the errors above and run again\n")
        return False
    out.write(
        f"imported: {result.teams} teams, {result.users} users, "
        f"{result.memberships} memberships, {result.accounts} accounts\n"
    )
    return True


def identity_unmapped(session: Session, out: TextIO) -> None:
    for account in unmapped_accounts(session):
        out.write(
            f"{account.id}\t{account.kind}\t{account.external_key}\t{account.display_name or ''}\n"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codepulse")
    sub = parser.add_subparsers(dest="group", required=True)

    conn = sub.add_parser("connections").add_subparsers(dest="command", required=True)
    add = conn.add_parser("add", help="register a GitLab instance")
    add.add_argument("--name", default="gitlab")
    add.add_argument("--url", required=True)
    add.add_argument("--token", required=True, help="bot token: read_api (+ api to comment)")
    add.add_argument("--webhook-secret", help="default: a random secret")
    conn.add_parser("list")
    rotate = conn.add_parser("rotate", help="replace a connection's token and/or webhook secret")
    rotate.add_argument("connection_id", type=int)
    rotate.add_argument("--token", help="new GitLab token")
    rotate.add_argument(
        "--webhook-secret",
        nargs="?",
        const="",
        help="new webhook secret (no value: generate one); re-run register-webhook afterwards",
    )

    proj = sub.add_parser("projects").add_subparsers(dest="command", required=True)
    padd = proj.add_parser("add", help="register a GitLab project by its numeric id")
    padd.add_argument("gitlab_project_id", type=int)
    padd.add_argument("--connection", type=int)
    proj.add_parser("list")
    hook = proj.add_parser("register-webhook")
    hook.add_argument("project_id", type=int, help="CodePulse project id (projects list)")
    sync = proj.add_parser("sync", help="collect now (inline), or --enqueue for the worker")
    sync.add_argument("project_id", type=int)
    sync.add_argument("--enqueue", action="store_true")

    ident = sub.add_parser("identity").add_subparsers(dest="command", required=True)
    imp = ident.add_parser("import")
    imp.add_argument("--teams", type=Path)
    imp.add_argument("--users", type=Path)
    imp.add_argument("--accounts", type=Path)
    ident.add_parser("unmapped")

    metrics = sub.add_parser("metrics").add_subparsers(dest="command", required=True)
    recompute = metrics.add_parser(
        "recompute", help="re-score reviews and re-aggregate every week/month in the range"
    )
    recompute.add_argument("--from", dest="date_from", type=date.fromisoformat, required=True)
    recompute.add_argument("--to", dest="date_to", type=date.fromisoformat, required=True)
    return parser


def run(
    args: argparse.Namespace,
    session: Session,
    box: SecretBox,
    make_client: ClientFactory,
    out: TextIO,
    enqueue: Callable[[int], None] | None = None,
) -> bool:
    """Execute one command inside the caller's transaction; False means roll back."""
    settings = get_settings()
    match (args.group, args.command):
        case ("connections", "add"):
            connections_add(session, box, out, args.name, args.url, args.token, args.webhook_secret)
        case ("connections", "list"):
            connections_list(session, out)
        case ("connections", "rotate"):
            connections_rotate(
                session, box, out, args.connection_id, args.token, args.webhook_secret
            )
        case ("projects", "add"):
            projects_add(session, make_client, out, args.gitlab_project_id, args.connection)
        case ("projects", "list"):
            projects_list(session, out)
        case ("projects", "register-webhook"):
            projects_register_webhook(
                session, make_client, box, out, args.project_id, settings.app_base_url
            )
        case ("projects", "sync") if args.enqueue:
            _project(session, args.project_id)
            if enqueue is None:
                raise CliError("no queue available")
            enqueue(args.project_id)
            out.write(f"sync of project {args.project_id} queued\n")
        case ("projects", "sync"):
            result = sync_project(
                session,
                _project(session, args.project_id),
                make_client,
                backfill_days=settings.backfill_days,
                ignore_paths=settings.ignore_paths,
                allowed_email_domains=settings.allowed_email_domains,
            )
            if result.skipped:
                out.write("another sync of this project is running; skipped\n")
            else:
                for key, value in sorted((result.counts or {}).items()):
                    out.write(f"{key}: {value}\n")
        case ("identity", "import"):
            return identity_import(session, out, args.teams, args.users, args.accounts)
        case ("identity", "unmapped"):
            identity_unmapped(session, out)
        case ("metrics", "recompute"):
            if args.date_to < args.date_from:
                raise CliError("--to must not be before --from")
            recomputed = recompute(
                session,
                args.date_from,
                args.date_to,
                ZoneInfo(settings.report_tz),
                settings.scoring_weights,
                settings.min_sample,
            )
            out.write(
                f"rescored {recomputed.reviews_rescored} reviews; "
                f"aggregated {len(recomputed.periods)} periods\n"
            )
    return True


def main(argv: Sequence[str] | None = None) -> int:
    from rq import Queue

    from app.collector.sync import SYNC_JOB_TIMEOUT
    from app.db import get_redis, get_sessionmaker

    args = build_parser().parse_args(argv)

    def enqueue(project_id: int) -> None:
        Queue("default", connection=get_redis()).enqueue(
            "app.collector.sync.sync_project_job",
            project_id,
            job_id=f"sync-project-{project_id}",
            job_timeout=SYNC_JOB_TIMEOUT,
        )

    box = secret_box()
    with get_sessionmaker()() as session:
        try:
            with session.begin():
                if not run(args, session, box, client_factory(box), sys.stdout, enqueue):
                    raise _Rollback
        except _Rollback:
            return 1
        except (CliError, GitLabError) as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
