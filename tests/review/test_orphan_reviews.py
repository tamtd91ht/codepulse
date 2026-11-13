"""Reviews left queued/running with no job in Redis (app/workers/orphan_jobs.py, user
2026-11-13: "không biết là có đang review hay đang treo")."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import MergeRequest, Project, Review
from app.review.providers import FakeLLMProvider
from app.workers import orphan_jobs
from app.workers.orphan_jobs import LOST_ERROR, MAX_REQUEUES, requeue_orphans
from tests.review.test_service import mr, run  # noqa: F401 - the `mr` fixture

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


def _stuck(
    session: Session, project: Project, merge_request: MergeRequest, status: str, ago: timedelta
) -> Review:
    """A review of an open MR whose row says `status` since `ago`, as a killed worker leaves it."""
    merge_request.state = "opened"
    review: Review = run(session, FakeLLMProvider(), project, merge_request)
    merge_request.head_sha = review.reviewed_sha
    session.execute(
        update(Review)
        .where(Review.id == review.id)
        .values(status=status, started_at=NOW - ago, updated_at=NOW - ago, trigger="telegram")
        .execution_options(synchronize_session=False)
    )
    session.flush()
    session.refresh(review)
    return review


class Jobs:
    def __init__(self) -> None:
        self.queued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.losses: dict[int, int] = {}

    def enqueue(self, func: str, *args: Any, **kwargs: Any) -> None:
        self.queued.append((func, args, kwargs))

    def lost(self, review_id: int) -> int:
        self.losses[review_id] = self.losses.get(review_id, 0) + 1
        return self.losses[review_id]


def _nothing() -> tuple[set[Any], set[str]]:
    return set(), set()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_review_with_no_job_is_queued_again_in_its_lane(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
    status: str,
) -> None:
    project, merge_request = mr
    review = _stuck(db_session, project, merge_request, status, timedelta(minutes=30))
    jobs = Jobs()
    result = requeue_orphans(db_session, _nothing, jobs.lost, jobs.enqueue, now=NOW)
    assert result == {"requeued": 1, "failed": 0, "checked": 1}
    [(func, args, kwargs)] = jobs.queued
    assert func == "app.workers.review_jobs.review_mr_job"
    assert args == (project.id, merge_request.iid, review.reviewed_sha)
    # Same requester and trigger, the asked tier for a member's own request.
    assert kwargs["trigger"] == "telegram" and kwargs["asked"] is True
    assert kwargs["requested_by"] == review.requested_by_user_id
    assert "-rerun-orphan-" in kwargs["job_id"]
    db_session.refresh(review)
    assert (review.status, review.error) == ("queued", None)


def test_a_review_whose_job_is_still_there_is_left_alone(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    review = _stuck(db_session, project, merge_request, "running", timedelta(minutes=30))
    # The job waits under another suffix (a rerun) with a short sha: still the same review.
    present = {(str(project.id), str(merge_request.iid), review.reviewed_sha[:7])}
    jobs = Jobs()
    result = requeue_orphans(db_session, lambda: (present, set()), jobs.lost, jobs.enqueue, now=NOW)
    assert result["requeued"] == 0 and jobs.queued == []
    db_session.refresh(review)
    assert review.status == "running"


def test_nothing_moves_within_the_grace_or_when_redis_cannot_be_read(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    review = _stuck(db_session, project, merge_request, "queued", timedelta(minutes=2))
    jobs = Jobs()
    assert requeue_orphans(db_session, _nothing, jobs.lost, jobs.enqueue, now=NOW)["checked"] == 0
    db_session.execute(
        update(Review)
        .where(Review.id == review.id)
        .values(updated_at=NOW - timedelta(hours=1))
        .execution_options(synchronize_session=False)
    )

    def down() -> tuple[set[Any], set[str]]:
        raise ConnectionError("redis down")

    result = requeue_orphans(db_session, down, jobs.lost, jobs.enqueue, now=NOW)
    assert result["requeued"] == 0 and jobs.queued == []


def test_a_review_lost_again_and_again_is_failed_with_the_reason(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    review = _stuck(db_session, project, merge_request, "running", timedelta(minutes=30))
    jobs = Jobs()
    jobs.losses[review.id] = MAX_REQUEUES
    result = requeue_orphans(db_session, _nothing, jobs.lost, jobs.enqueue, now=NOW)
    assert result == {"requeued": 0, "failed": 1, "checked": 1} and jobs.queued == []
    db_session.refresh(review)
    assert (review.status, review.error) == ("failed", LOST_ERROR)


def test_a_review_that_can_no_longer_run_is_failed_not_queued(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    review = _stuck(db_session, project, merge_request, "queued", timedelta(minutes=30))
    merge_request.state = "merged"
    db_session.flush()
    jobs = Jobs()
    result = requeue_orphans(db_session, _nothing, jobs.lost, jobs.enqueue, now=NOW)
    assert result["failed"] == 1 and jobs.queued == []
    db_session.refresh(review)
    assert review.status == "failed" and "không chạy lại" in (review.error or "")


def test_has_job_matches_prefixes_both_ways() -> None:
    commits = {"abcdef1"}
    assert orphan_jobs._has_job("review-commit-abcdef123456", set(), commits)
    assert not orphan_jobs._has_job("review-commit-0123456789ab", set(), commits)


def test_a_live_job_is_never_failed_even_if_its_mr_moved_on(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    """Review 2026-11-13: the worker still running it decides, not the sweep."""
    project, merge_request = mr
    review = _stuck(db_session, project, merge_request, "running", timedelta(minutes=30))
    present = {(str(project.id), str(merge_request.iid), review.reviewed_sha)}
    merge_request.state = "merged"
    db_session.commit()
    jobs = Jobs()
    result = requeue_orphans(db_session, lambda: (present, set()), jobs.lost, jobs.enqueue, now=NOW)
    assert result["failed"] == 0 and jobs.queued == []
    db_session.refresh(review)
    assert review.status == "running"


def test_another_mr_with_the_same_head_does_not_hide_a_lost_one(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    review = _stuck(db_session, project, merge_request, "queued", timedelta(minutes=30))
    other_mr = {(str(project.id), str(merge_request.iid + 1), review.reviewed_sha)}
    jobs = Jobs()
    result = requeue_orphans(
        db_session, lambda: (other_mr, set()), jobs.lost, jobs.enqueue, now=NOW
    )
    assert result["requeued"] == 1


def test_a_review_that_finished_meanwhile_is_not_written_over(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    """Review 2026-11-13: its worker commits `done` after the sweep read the row."""
    project, merge_request = mr
    review = _stuck(db_session, project, merge_request, "running", timedelta(minutes=30))
    jobs = Jobs()

    def finishes_meanwhile(review_id: int) -> int:
        db_session.execute(
            update(Review)
            .where(Review.id == review_id)
            .values(status="done", updated_at=NOW)
            .execution_options(synchronize_session=False)
        )
        db_session.commit()
        return 1

    result = requeue_orphans(db_session, _nothing, finishes_meanwhile, jobs.enqueue, now=NOW)
    assert result["requeued"] == 0 and jobs.queued == []
    db_session.refresh(review)
    assert review.status == "done"


def test_a_job_that_cannot_be_queued_leaves_the_row_for_the_next_sweep(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    review = _stuck(db_session, project, merge_request, "queued", timedelta(minutes=30))
    jobs = Jobs()

    def down(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("redis down")

    result = requeue_orphans(db_session, _nothing, jobs.lost, down, now=NOW)
    assert result["requeued"] == 0
    db_session.refresh(review)
    assert review.status == "queued"
