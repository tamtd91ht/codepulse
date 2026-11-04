"""Chấm lại điểm theo tiêu chí (docs/specs/22 §10)."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Commit, Review, ReviewCriteriaScore
from app.review.providers.base import ReviewPrompt
from app.review.providers.fake import FakeLLMProvider
from app.review.rubric import RUBRIC_VERSION
from app.scoring.criteria_rescore import Scope, plan, run, thin_reason
from app.scoring.criteria_store import project_sets
from tests.api.conftest import FakeQueue, World, grant
from tests.api.test_criteria_api import assign, bp, make

pytestmark = pytest.mark.integration

API = "/api/v1"


def setup(client: TestClient, world: World, as_user: Any, db_session: Session) -> dict[str, Any]:
    """A set on the dự án; An's MR review (one unplaced security finding); a thin commit review
    of Binh (120 lines, no finding, no summary)."""
    admin = as_user(world.admin)
    made = make(client, admin, name="Chuẩn OmiCRM", apply_caps=False)
    assign(client, admin, bp(world), [{"set_id": made["id"], "is_primary": True}])
    world.an_review.loc_reviewed = 100
    db_session.add(Commit(sha="f" * 40, project_id=world.project.id, message="feat: big\n"))
    db_session.add(
        Review(
            target_type="commit",
            commit_sha="f" * 40,
            reviewed_sha="f" * 40,
            status="done",
            author_user_id=world.binh.id,
            score=Decimal(100),
            loc_reviewed=120,
            summary="",
            finished_at=datetime(2026, 10, 7, 9, tzinfo=UTC),
            rubric_version=RUBRIC_VERSION,
        )
    )
    db_session.flush()
    return made


def test_preview_says_what_would_change_and_writes_nothing(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    made = setup(client, world, as_user, db_session)
    admin = as_user(world.admin)
    res = client.post(
        f"{API}/business-projects/{bp(world)}/criteria-rescore/preview", json={}, headers=admin
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reviews"] == 2 and body["findings"] == 1
    (s,) = body["sets"]
    assert s["set_id"] == made["id"] and s["never_scored"] == 2
    assert body["unplaced_findings"] == 1 and body["reviews_to_classify"] == 1
    assert body["thin_total"] == 1 and body["thin"][0]["reason"] == "empty_large"
    assert body["est_classify_usd"] > 0 and body["may_rereview"] is True
    assert db_session.scalars(select(ReviewCriteriaScore)).first() is None  # dry run
    # Filters: one person, a repo of another dự án, an unknown set.
    only_an = client.post(
        f"{API}/business-projects/{bp(world)}/criteria-rescore/preview",
        json={"user_ids": [world.an.id]},
        headers=admin,
    ).json()
    assert only_an["reviews"] == 1 and only_an["thin_total"] == 0
    period = client.post(
        f"{API}/business-projects/{bp(world)}/criteria-rescore/preview",
        json={"from": "2026-10-07", "to": "2026-10-31"},
        headers=admin,
    ).json()
    assert period["reviews"] == 1  # An's review (6 Oct) is before the period
    assert (
        client.post(
            f"{API}/business-projects/{bp(world)}/criteria-rescore/preview",
            json={"set_ids": [999999]},
            headers=admin,
        ).status_code
        == 422
    )


def test_start_needs_confirmation_and_the_right(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    setup(client, world, as_user, db_session)
    admin = as_user(world.admin)
    url = f"{API}/business-projects/{bp(world)}/criteria-rescore"
    assert client.post(url, json={"classify": True}, headers=admin).status_code == 422
    # A lead does not give criteria sets, so does not redo the scores either.
    assert client.post(url, json={"confirm": True}, headers=as_user(world.lead)).status_code == 403
    res = client.post(
        url, json={"confirm": True, "classify": True, "user_ids": [world.an.id]}, headers=admin
    )
    assert res.status_code == 202, res.text
    ((_func, args, kwargs),) = [j for j in queue.jobs if j[0].endswith("rescore_job")]
    params = args[0]
    assert params["classify"] is True and params["user_ids"] == [world.an.id]
    assert kwargs["job_id"] == f"criteria-rescore-{bp(world)}"
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "criteria.rescore")).one()
    assert client.get(url, headers=admin).json() == {"status": "none"}  # a fake queue: no job


def placer(prompt: ReviewPrompt) -> dict[str, Any]:
    """Claude, faked: every finding listed goes to the component "security"."""
    ids = [int(x) for x in re.findall(r'"id": (\d+)', prompt.user)]  # the findings JSON
    return {"placements": [{"id": i, "key": "security"} for i in ids]}


def test_run_places_findings_rescores_and_rereviews_the_thin(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    setup(client, world, as_user, db_session)
    versions = project_sets(db_session, bp(world))
    assert "security" in versions[0].keys
    provider = FakeLLMProvider(responder=placer, cost_usd=0.02)
    thin: list[int] = []
    batches: list[tuple[float, int]] = []
    outcome = run(
        db_session,
        Scope(bp(world)),
        versions,
        provider=provider,
        over_budget=lambda spent: False,
        rereview=lambda review: thin.append(review.id) or True,
        on_batch=lambda cost, calls: batches.append((cost, calls)),
    )
    assert batches == [(pytest.approx(0.02), 1)]  # recorded with the batch, not at the end
    assert outcome.scored == 2 and outcome.classified_findings == 1 and outcome.calls == 1
    assert outcome.cost_usd == pytest.approx(0.02) and outcome.rereview_queued == 1
    db_session.refresh(world.an_finding)
    assert world.an_finding.criteria == {str(versions[0].set_id): "security"}
    assert len(provider.prompts) == 1 and "SQL injection" in provider.prompts[0].user
    rows = db_session.scalars(select(ReviewCriteriaScore)).all()
    assert len(rows) == 2
    an = next(r for r in rows if r.review_id == world.an_review.id)
    security = next(c for c in an.breakdown["components"] if c["key"] == "security")
    assert security["findings"] == [{"id": world.an_finding.id, "source": "ai"}]
    assert len(thin) == 1
    # Placed now: a second plan has nothing left for Claude.
    again = plan(db_session, Scope(bp(world)), versions)
    assert again.unplaced == 0 and again.reviews_to_classify == 0


def test_run_reports_each_step_for_the_progress_bar(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    setup(client, world, as_user, db_session)
    versions = project_sets(db_session, bp(world))
    steps: list[tuple[str, int, int, int]] = []
    run(
        db_session,
        Scope(bp(world)),
        versions,
        provider=FakeLLMProvider(responder=placer, cost_usd=0.02),
        over_budget=lambda spent: False,
        rereview=None,
        progress=lambda phase, out: steps.append((phase, out.scored, out.reviews, out.calls)),
    )
    assert steps[0] == ("scoring", 0, 2, 0)  # before any work: the total is known
    assert any(phase == "classifying" for phase, *_ in steps)
    assert steps[-2:] == [("saving", 2, 2, 1), ("finished", 2, 2, 1)]
    scored = [done for phase, done, _, _ in steps if phase == "scoring"]
    assert scored == sorted(scored) and scored[-1] == 2  # moves review by review


class FakeJob:
    enqueued_at = ended_at = None
    exc_info = None

    def __init__(self, state: str, progress: dict[str, Any]) -> None:
        self.state, self.meta = state, {"progress": progress}

    def get_status(self, refresh: bool = True) -> str:
        return self.state

    def get_meta(self, refresh: bool = True) -> dict[str, Any]:
        return self.meta

    def get_position(self) -> int:
        return 3


def test_status_says_how_far_a_run_is(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue
) -> None:
    url = f"{API}/business-projects/{bp(world)}/criteria-rescore"
    admin = as_user(world.admin)
    old = {
        "phase": "classifying",
        "done": 4,
        "total": 10,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    queue.fetch_job = lambda job_id: FakeJob("started", old)  # type: ignore[attr-defined]
    body = client.get(url, headers=admin).json()
    assert body["progress"]["done"] == 4 and body["stale"] is True
    fresh = {**old, "updated_at": datetime.now(UTC).isoformat()}
    queue.fetch_job = lambda job_id: FakeJob("started", fresh)  # type: ignore[attr-defined]
    assert client.get(url, headers=admin).json()["stale"] is False
    queue.fetch_job = lambda job_id: FakeJob("queued", {})  # type: ignore[attr-defined]
    assert client.get(url, headers=admin).json()["queue_position"] == 3


def test_the_budget_stops_the_calls_not_the_scores(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    setup(client, world, as_user, db_session)
    versions = project_sets(db_session, bp(world))
    provider = FakeLLMProvider(responder=placer)
    outcome = run(
        db_session,
        Scope(bp(world)),
        versions,
        provider=provider,
        over_budget=lambda spent: True,
        rereview=None,
    )
    assert outcome.budget_stopped and outcome.calls == 0 and outcome.scored == 2
    assert not provider.prompts


def test_thin_reasons(world: World, db_session: Session) -> None:
    # An older rubric is not "thin" (Chạy lại refuses it too: ask a new review instead).
    assert thin_reason(Review(rubric_version="v0", loc_reviewed=200, summary=""), 0) is None
    # A round this feature queued is never re-reviewed for thinness again.
    again = Review(
        rubric_version=RUBRIC_VERSION, loc_reviewed=200, summary="", trigger="criteria_rescore"
    )
    assert thin_reason(again, 0) is None
    review = Review(rubric_version=RUBRIC_VERSION, loc_reviewed=200, summary="")
    assert thin_reason(review, 0) == "empty_large"
    assert thin_reason(review, 1) is None
    review = Review(rubric_version=RUBRIC_VERSION, loc_reviewed=200, summary="x" * 60)
    assert thin_reason(review, 0) is None


def test_rereview_queues_a_new_round_once_and_skips_what_cannot_run(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.workers.criteria_jobs import _rereview

    setup(client, world, as_user, db_session)
    world.project.tracked = True
    world.project.review_enabled = True
    thin = db_session.scalars(select(Review).where(Review.commit_sha == "f" * 40)).one()
    queue = FakeQueue()
    go = _rereview(db_session, world.admin.id, queue)
    assert go(thin) is True
    ((func, args, kwargs),) = queue.jobs
    assert func.endswith("review_commit_job") and args == (world.project.id, "f" * 40)
    assert kwargs["force"] is True and kwargs["expected_round"] == thin.round
    assert kwargs["trigger"] == "criteria_rescore"
    assert kwargs["job_id"] == f"review-commit-{'f' * 12}-rerun-{thin.id}-{thin.round + 1}"
    # A closed MR (or one with newer commits) cannot be reviewed again at that head.
    world.mr.state = "merged"
    db_session.flush()
    assert go(world.an_review) is False
    # Reviews turned off for the repo: nothing queued.
    world.project.review_enabled = False
    db_session.flush()
    assert go(thin) is False and len(queue.jobs) == 1


def test_rereview_needs_the_rerun_right_and_one_run_at_a_time(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    from sqlalchemy import delete

    from app.models import PermissionSetItem

    setup(client, world, as_user, db_session)
    url = f"{API}/business-projects/{bp(world)}/criteria-rescore"
    # Without review.rerun anywhere, criteria.assign alone does not re-review.
    db_session.execute(
        delete(PermissionSetItem).where(PermissionSetItem.permission == "review.rerun")
    )
    grant(db_session, world.manager, bp(world), "manage")
    db_session.flush()
    manager = as_user(world.manager)
    res = client.post(url, json={"confirm": True, "rereview_thin": True}, headers=manager)
    assert res.status_code == 403, res.text
    assert client.post(url, json={"confirm": True}, headers=manager).status_code == 202

    class Locked(FakeQueue):
        class connection:  # stands for a Redis client whose lock is taken
            @staticmethod
            def set(*args: Any, **kwargs: Any) -> bool:
                return False

    from app.api.deps import get_queue
    from app.api.main import app

    app.dependency_overrides[get_queue] = lambda: Locked()
    try:
        res = client.post(url, json={"confirm": True}, headers=as_user(world.admin))
    finally:
        app.dependency_overrides[get_queue] = lambda: queue
    assert res.status_code == 409
