"""Sample data for local development (`make seed`). Idempotent: running it twice changes nothing."""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto import SecretBox, secret_box
from app.db import get_sessionmaker
from app.logging import configure_logging
from app.models import Account, Connection, Project, Team, TeamMember, User

SEED_CONNECTION = "seed-gitlab"


def seed(session: Session, box: SecretBox) -> bool:
    """Insert the sample dataset; returns False when it is already present."""
    if session.scalar(select(Connection).where(Connection.name == SEED_CONNECTION)):
        return False

    connection = Connection(
        name=SEED_CONNECTION,
        base_url="https://gitlab.example.com",
        token_encrypted=box.encrypt("glpat-seed-not-a-real-token"),
        webhook_secret_encrypted=box.encrypt("seed-webhook-secret"),
    )
    session.add(connection)
    session.flush()
    session.add(
        Project(
            connection_id=connection.id,
            external_id=1001,
            path_with_namespace="demo/codepulse-sample",
            default_branch="main",
        )
    )

    platform = Team(name="Platform")
    backend = Team(name="Backend")
    session.add_all([platform, backend])
    session.flush()
    backend.parent_id = platform.id

    people = [
        ("Nguyen Van An", "an.nguyen@company.vn", "an.nguyen", "lead"),
        ("Tran Thi Binh", "binh.tran@company.vn", "binh.tran", "developer"),
        ("Le Van Cuong", "cuong.le@company.vn", "cuong.le", "developer"),
    ]
    for full_name, email, gitlab_username, role in people:
        user = User(full_name=full_name, primary_email=email)
        session.add(user)
        session.flush()
        session.add_all(
            [
                TeamMember(
                    team_id=backend.id, user_id=user.id, role=role, joined_at=date(2026, 1, 1)
                ),
                Account(kind="gitlab_user", external_key=gitlab_username, user_id=user.id),
                Account(kind="git_email", external_key=email, user_id=user.id),
            ]
        )
    # An account nobody is linked to yet, so the "unmapped" view has something to show.
    session.add(Account(kind="git_email", external_key="ci-bot@users.noreply.example.com"))
    return True


def main() -> None:
    configure_logging()
    with get_sessionmaker()() as session, session.begin():
        created = seed(session, secret_box())
    logging.getLogger(__name__).info("seed %s", "inserted" if created else "already present")


if __name__ == "__main__":
    main()
