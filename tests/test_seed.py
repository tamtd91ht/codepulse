import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crypto import SecretBox
from app.models import Account, Connection, TeamMember, User
from app.seed import seed

pytestmark = pytest.mark.integration


def test_seed_is_idempotent_and_encrypts_tokens(db_session: Session) -> None:
    box = SecretBox(Fernet.generate_key().decode())
    assert seed(db_session, box) is True
    assert seed(db_session, box) is False

    assert db_session.scalar(select(func.count()).select_from(User)) == 3
    assert db_session.scalar(select(func.count()).select_from(TeamMember)) == 3
    unmapped = db_session.scalars(select(Account).where(Account.user_id.is_(None))).all()
    assert [a.external_key for a in unmapped] == ["ci-bot@users.noreply.example.com"]
    connection = db_session.scalars(select(Connection)).one()
    assert "glpat" not in connection.token_encrypted
    assert box.decrypt(connection.token_encrypted) == "glpat-seed-not-a-real-token"
