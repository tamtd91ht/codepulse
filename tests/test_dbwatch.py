"""Long database transactions ring the admins' bell, naming the flow (app/dbwatch.py), and each
connection says which flow it works for (app/dblabel.py)."""

import time
from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app import dblabel
from app.config import Settings
from app.dbwatch import KIND, Seen, check, long_transactions, message
from app.models import User, UserNotification

pytestmark = pytest.mark.integration


@pytest.fixture
def held(migrated_engine: Engine) -> Iterator[None]:
    """Another connection with a transaction left open, labelled as a worker's sync job."""
    with migrated_engine.connect() as conn:
        conn.execute(text("SET application_name = 'cp-worker sync.sync_project_job #7'"))
        conn.commit()
        conn.execute(text("SELECT 1"))  # opens the transaction, then waits on the app
        yield
        conn.rollback()


def settings(**kw: object) -> Settings:
    return Settings(
        session_secret=SecretStr("s" * 48),
        admin_emails=["admin@x.vn"],
        **kw,  # type: ignore[arg-type]
    )


def test_a_long_transaction_rings_each_admin_once_with_its_flow(
    db_session: Session, held: None
) -> None:
    admin = User(full_name="Admin", primary_email="admin@x.vn")
    other = User(full_name="An", primary_email="an@x.vn")
    db_session.add_all([admin, other])
    db_session.flush()
    time.sleep(1.2)

    found = long_transactions(db_session, 1)
    mine = [tx for tx in found if tx.application_name == "cp-worker sync.sync_project_job #7"]
    assert len(mine) == 1
    tx = mine[0]
    assert tx.flow == "worker · sync.sync_project_job #7"
    assert tx.state == "idle in transaction"
    title, body = message(tx, escalated=False)
    assert "sync.sync_project_job #7" in title and "chờ ứng dụng" in body

    seen = Seen(None)
    assert check(db_session, settings(long_tx_alert_s=1), seen) >= 1
    rows = db_session.scalars(select(UserNotification).where(UserNotification.kind == KIND)).all()
    assert {r.user_id for r in rows} == {admin.id}
    assert any("sync.sync_project_job #7" in r.title for r in rows)
    # Rung once: the next minute says nothing new about the same transaction.
    assert check(db_session, settings(long_tx_alert_s=1), seen) == 0


def test_the_watch_is_off_at_zero(db_session: Session, held: None) -> None:
    time.sleep(1.1)
    assert check(db_session, settings(long_tx_alert_s=0), Seen(None)) == 0


def test_connections_carry_the_flow_as_application_name(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    dblabel.install(engine)
    dblabel.set_process("test")
    try:
        with dblabel.labelled("GET /api/v1/reviews"), engine.connect() as conn:
            name = conn.scalar(text("SELECT current_setting('application_name')"))
        assert name == "cp-test GET /api/v1/reviews"
        label = dblabel.job_label("app.collector.sync.sync_project_job", (12,))
        with dblabel.labelled(label), engine.connect() as conn:
            name = conn.scalar(text("SELECT current_setting('application_name')"))
        assert name == "cp-test sync.sync_project_job #12"
    finally:
        dblabel.set_process("app")
        engine.dispose()
