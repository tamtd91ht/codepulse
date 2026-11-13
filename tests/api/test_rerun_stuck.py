"""A review queued or running past 10 minutes may be run again by hand, from the list and the
detail page, but never while a job is still behind it (user 2026-11-13)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.routers import reviews as reviews_router
from app.models import Review
from tests.api.conftest import FakeQueue, World, grant


def _since(session: Session, review: Review, status: str, ago: timedelta) -> None:
    """The row says `status` since `ago` (updated_at is otherwise reset on every write)."""
    when = datetime.now(UTC) - ago
    session.execute(
        update(Review)
        .where(Review.id == review.id)
        .values(status=status, error=None, started_at=when, updated_at=when)
        .execution_options(synchronize_session=False)
    )
    session.commit()


@pytest.fixture
def lead(db_session: Session, world: World, as_user: Any) -> Any:
    grant(db_session, world.lead, world.project.business_project_id, "manage")
    return as_user(world.lead)


def test_not_offered_before_ten_minutes(
    client: TestClient, world: World, lead: Any, queue: FakeQueue, db_session: Session
) -> None:
    _since(db_session, world.binh_review, "queued", timedelta(minutes=3))
    url = f"/api/v1/reviews/{world.binh_review.id}"
    detail = client.get(url, headers=lead).json()
    assert detail["can_rerun"] is False and detail["waiting_since"] is not None
    sent = client.post(f"{url}/rerun", headers=lead)
    assert sent.status_code == 409 and "wait for it" in sent.json()["detail"]
    assert queue.jobs == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_offered_and_queued_again_past_ten_minutes(
    client: TestClient,
    world: World,
    lead: Any,
    queue: FakeQueue,
    db_session: Session,
    status: str,
) -> None:
    _since(db_session, world.binh_review, status, timedelta(minutes=25))
    url = f"/api/v1/reviews/{world.binh_review.id}"
    assert client.get(url, headers=lead).json()["can_rerun"] is True
    listed = client.get(f"/api/v1/reviews?status={status}", headers=lead).json()
    [row] = [r for r in listed["items"] if r["id"] == world.binh_review.id]
    assert row["can_rerun"] is True and row["waiting_since"] is not None
    sent = client.post(f"{url}/rerun", json={"force": False}, headers=lead)
    assert sent.status_code == 202, sent.text
    ((func, args, kwargs),) = queue.jobs
    assert func.endswith("review_commit_job") and args[1] == "b" * 40
    assert kwargs["force"] is False
    db_session.refresh(world.binh_review)
    assert world.binh_review.status == "queued" and world.binh_review.finished_at is None


def test_never_while_a_job_is_still_behind_it(
    client: TestClient,
    world: World,
    lead: Any,
    queue: FakeQueue,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only slow, not stuck: a second paid run at the same time is refused."""
    _since(db_session, world.binh_review, "running", timedelta(minutes=40))
    queue.connection = object()  # type: ignore[attr-defined]
    monkeypatch.setattr(reviews_router, "job_alive", lambda session, redis, review: True)
    sent = client.post(f"/api/v1/reviews/{world.binh_review.id}/rerun", headers=lead)
    assert sent.status_code == 409 and "không bị treo" in sent.json()["detail"]
    assert queue.jobs == []
    db_session.refresh(world.binh_review)
    assert world.binh_review.status == "running"


def test_refused_when_the_queues_cannot_be_read(
    client: TestClient,
    world: World,
    lead: Any,
    queue: FakeQueue,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _since(db_session, world.binh_review, "queued", timedelta(minutes=40))
    queue.connection = object()  # type: ignore[attr-defined]

    def down(session: Any, redis: Any, review: Any) -> bool:
        raise ConnectionError("redis down")

    monkeypatch.setattr(reviews_router, "job_alive", down)
    sent = client.post(f"/api/v1/reviews/{world.binh_review.id}/rerun", headers=lead)
    assert sent.status_code == 503 and queue.jobs == []


def test_a_member_is_not_offered_it(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _since(db_session, world.binh_review, "queued", timedelta(minutes=40))
    sent = client.post(f"/api/v1/reviews/{world.binh_review.id}/rerun", headers=as_user(world.an))
    assert sent.status_code in (403, 404)


def test_a_failed_review_run_again_waits_with_no_end_time(
    client: TestClient, world: World, lead: Any, queue: FakeQueue, db_session: Session
) -> None:
    """User 2026-11-13: "Đang chạy" beside a "Review xong" time."""
    world.binh_review.status = "failed"
    world.binh_review.finished_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.commit()
    sent = client.post(f"/api/v1/reviews/{world.binh_review.id}/rerun", headers=lead)
    assert sent.status_code == 202
    db_session.refresh(world.binh_review)
    assert (world.binh_review.status, world.binh_review.finished_at) == ("queued", None)
