from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, AuditLog, Review, TeamMember, User
from tests.api.conftest import FakeQueue, World


def test_removing_a_mistaken_person_keeps_their_review_history(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    an_id = world.an.id
    db_session.add(Account(kind="git_email", external_key="an@x.vn", user_id=an_id))
    db_session.commit()
    path = f"/api/v1/users/{an_id}"
    assert client.delete(path, headers=as_user(world.lead)).status_code == 403
    me = f"/api/v1/users/{world.admin.id}"
    assert client.delete(me, headers=as_user(world.admin)).status_code == 409

    assert client.delete(path, headers=as_user(world.admin)).status_code == 204
    db_session.expire_all()
    assert db_session.get(User, an_id) is None
    review = db_session.get(Review, world.an_review.id)
    assert review is not None and review.author_user_id is None  # history stays
    account = db_session.scalars(select(Account).where(Account.external_key == "an@x.vn")).one()
    assert account.user_id is None  # back to "not linked"
    assert db_session.scalars(select(TeamMember).where(TeamMember.user_id == an_id)).first() is None
    entry = db_session.scalars(select(AuditLog).where(AuditLog.action == "user.delete")).one()
    assert entry.details is not None and entry.details["email"] == "an@x.vn"
    ((func, _, _),) = queue.jobs
    assert func.endswith("recompute_job")
    assert client.delete(path, headers=as_user(world.admin)).status_code == 404
