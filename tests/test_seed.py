from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crypto import SecretBox
from app.models import Account, Connection, TeamMember, User
from app.seed import seed

pytestmark = pytest.mark.integration


def test_seed_is_idempotent_and_encrypts_tokens(migrated_engine: Any) -> None:
    box = SecretBox(Fernet.generate_key().decode())
    with Session(migrated_engine) as session, session.begin():
        assert seed(session, box) is True
    with Session(migrated_engine) as session, session.begin():
        assert seed(session, box) is False

    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 3
        assert session.scalar(select(func.count()).select_from(TeamMember)) == 3
        unmapped = session.scalars(select(Account).where(Account.user_id.is_(None))).all()
        assert [a.external_key for a in unmapped] == ["ci-bot@users.noreply.example.com"]
        connection = session.scalars(select(Connection)).one()
        assert "glpat" not in connection.token_encrypted
        assert box.decrypt(connection.token_encrypted) == "glpat-seed-not-a-real-token"
