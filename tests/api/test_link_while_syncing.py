"""Linking an author while a GitLab sync runs (user 2026-11-12: "Gắn" on a review hung, then
failed). The sync's transaction lasts its whole collection: it must not hold the accounts it only
read, and a link that still meets a held lock answers "busy" within seconds."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.locks import lock_busy, short_lock_wait
from app.collector.gitlab.convert import Converter
from app.models import Account
from tests.api.conftest import World
from tests.api.test_attribution import unmapped_work

pytestmark = pytest.mark.integration


def test_a_sync_does_not_lock_the_accounts_it_only_reads(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as setup:
        account = Account(kind="gitlab_user", external_key="lock-test-1", display_name="an")
        setup.add(account)
        setup.commit()
        account_id = account.id
    sync, link = Session(migrated_engine), Session(migrated_engine)
    try:
        converter = Converter(sync, None, [])  # type: ignore[arg-type]
        assert converter.account_id("gitlab_user", "lock-test-1", "an") == account_id
        # The sync goes on (its transaction open): the account is free to link.
        short_lock_wait(link, 300)
        link.execute(select(Account.id).where(Account.id == account_id).with_for_update())
        link.rollback()
        # A renamed author is written, so held: the link gives up quickly instead of hanging.
        converter2 = Converter(sync, None, [])  # type: ignore[arg-type]
        converter2.account_id("gitlab_user", "lock-test-1", "an.new")
        short_lock_wait(link, 300)
        with pytest.raises(OperationalError) as caught:
            link.execute(select(Account.id).where(Account.id == account_id).with_for_update())
        assert lock_busy(caught.value)
    finally:
        link.rollback()
        sync.rollback()
        link.close()
        sync.close()
        with Session(migrated_engine) as cleanup:
            cleanup.execute(delete(Account).where(Account.id == account_id))
            cleanup.commit()


class _Busy(Exception):
    sqlstate = "55P03"


def test_a_link_meeting_a_held_lock_answers_busy(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, commit_review, _ = unmapped_work(db_session, world)

    def held(*_: Any, **__: Any) -> None:
        raise OperationalError("UPDATE reviews", {}, _Busy())

    monkeypatch.setattr("app.api.routers.reviews.attribute_reviews", held)
    answer = client.post(
        f"/api/v1/reviews/{commit_review.id}/author",
        json={"user_id": world.an.id, "sync": True},
        headers=as_user(world.admin),
    )
    # Rolled back (in tests the whole session's work, setup included), not a 500.
    assert answer.status_code == 409 and "đồng bộ GitLab" in answer.json()["detail"]
