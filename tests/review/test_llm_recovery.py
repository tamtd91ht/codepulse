"""Review again once the LLM works again (app/review/llm_recovery.py, app/workers/llm_jobs.py)."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LlmHealth, MergeRequest, Project, Review
from app.review import llm_recovery
from app.review.providers import FakeLLMProvider, LLMError, ReviewPrompt
from app.workers import llm_jobs
from tests.review.test_service import mr, run  # noqa: F401 - the `mr` fixture

pytestmark = pytest.mark.integration


def boom(prompt: ReviewPrompt) -> Any:
    raise LLMError("claude reported an error: usage limit reached (HTTP 429)")


def health(session: Session) -> LlmHealth:
    row = session.get(LlmHealth, llm_recovery.HEALTH_ID)
    assert row is not None
    session.refresh(row)
    return row


@pytest.mark.parametrize(
    ("error", "outage"),
    [
        ("llm: claude reported an error: rate limited (HTTP 429)", True),
        ("llm: claude failed after 4 attempts", True),
        ("llm: Connection refused", True),
        ("llm: claude exited 1 without a result", True),
        ("llm: Overloaded (HTTP 529)", True),
        ("llm: model output is non-json", False),
        ("monthly LLM budget reached: 50/50", False),
        ("llm: could not parse the answer", False),
        (None, False),
    ],
)
def test_what_counts_as_the_llm_being_down(error: str | None, outage: bool) -> None:
    assert llm_recovery.is_llm_outage(error) is outage


def test_the_first_failure_opens_the_outage_and_later_ones_do_not(db_session: Session) -> None:
    assert llm_recovery.mark_down(db_session, "llm: x (HTTP 429)", 30) is True
    since = health(db_session).down_since
    assert llm_recovery.mark_down(db_session, "llm: y (HTTP 429)", 30) is False
    row = health(db_session)
    assert (row.status, row.down_since, row.last_error) == ("down", since, "llm: x (HTTP 429)")


def test_off_by_default_nothing_waits(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    failed = run(db_session, FakeLLMProvider(boom), project, merge_request)
    assert failed.status == "failed" and failed.llm_retry is False
    assert health(db_session).status == "ok"


def test_an_outage_marks_the_review_and_later_reviews_do_not_call_the_llm(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    failed = run(db_session, FakeLLMProvider(boom), project, merge_request, llm_auto_resume=True)
    assert failed.status == "failed" and failed.llm_retry is True
    assert health(db_session).status == "down"
    # While down: no LLM call, the review waits.
    provider = FakeLLMProvider()
    again = run(db_session, provider, project, merge_request, llm_auto_resume=True)
    assert again.id == failed.id and again.status == "failed"
    assert again.error == llm_recovery.WAITING_ERROR and again.llm_retry is True
    assert provider.prompts == []
    assert llm_recovery.status(db_session)["waiting_reviews"] == 1


def test_a_bad_answer_is_not_an_outage(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr

    def nonsense(prompt: ReviewPrompt) -> Any:
        raise LLMError("model output is non-json")

    provider = FakeLLMProvider(nonsense)
    failed = run(db_session, provider, project, merge_request, llm_auto_resume=True)
    assert failed.status == "failed" and failed.llm_retry is False
    assert health(db_session).status == "ok"


def test_the_waiting_reviews_are_queued_exactly_once(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    merge_request.state = "opened"
    review = run(db_session, FakeLLMProvider(boom), project, merge_request, llm_auto_resume=True)
    merge_request.head_sha = review.reviewed_sha
    db_session.flush()
    jobs: list[tuple[Any, ...]] = []

    def enqueue(func: str, *args: Any, **kwargs: Any) -> None:
        jobs.append((func, args, kwargs))

    first = llm_recovery.requeue_waiting(db_session, enqueue)
    second = llm_recovery.requeue_waiting(db_session, enqueue)
    assert first == {"queued": 1, "left": 0} and second == {"queued": 0, "left": 0}
    assert [j[0] for j in jobs] == ["app.workers.review_jobs.review_mr_job"]
    assert jobs[0][2]["trigger"] == "rerun" and "-rerun-llm-" in jobs[0][2]["job_id"]
    db_session.refresh(review)
    assert (review.status, review.llm_retry, review.error) == ("queued", False, None)


def test_a_review_that_can_no_longer_run_is_left_failed(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    project, merge_request = mr
    review = run(db_session, FakeLLMProvider(boom), project, merge_request, llm_auto_resume=True)
    merge_request.state = "merged"
    db_session.flush()
    jobs: list[Any] = []
    assert llm_recovery.requeue_waiting(db_session, lambda *a, **k: jobs.append(a)) == {
        "queued": 0,
        "left": 1,
    }
    db_session.refresh(review)
    assert jobs == [] and review.status == "failed" and review.llm_retry is False
    assert "không review lại" in (review.error or "")


class FakeRq:
    jobs: ClassVar[list[tuple[str, dict[str, Any]]]] = []

    def __init__(self, name: str, connection: Any = None) -> None:
        self.name = name

    def enqueue(self, func: str, *args: Any, **kwargs: Any) -> None:
        FakeRq.jobs.append((func, kwargs))


@pytest.fixture
def jobs_on(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    @contextmanager
    def same_session() -> Any:
        yield db_session

    monkeypatch.setattr(llm_jobs, "get_sessionmaker", lambda: same_session)
    monkeypatch.setattr(llm_jobs, "get_redis", lambda: None)
    monkeypatch.setattr(llm_jobs, "Queue", FakeRq)
    monkeypatch.setattr(llm_jobs, "effective", lambda session, settings: {"llm_auto_resume": True})
    FakeRq.jobs = []
    return FakeRq.jobs


def test_the_probe_waits_for_its_time_then_one_success_queues_one_resume(
    db_session: Session, jobs_on: list[tuple[str, Any]]
) -> None:
    llm_recovery.mark_down(db_session, "llm: (HTTP 429)", 30)
    db_session.commit()  # the job's own rollback ("nothing due") must not undo the setup
    assert llm_jobs.llm_watch_job(provider_factory=FakeLLMProvider) == "nothing due"
    row = health(db_session)
    row.next_probe_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()
    assert llm_jobs.llm_watch_job(provider_factory=lambda: FakeLLMProvider(boom)) == "still down"
    row = health(db_session)
    assert row.status == "down" and row.probes == 1
    assert row.next_probe_at is not None and row.next_probe_at > datetime.now(UTC)
    assert jobs_on == []
    row.next_probe_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()
    assert llm_jobs.llm_watch_job(provider_factory=FakeLLMProvider) == "recovered"
    assert health(db_session).status == "recovering"
    assert [j[0] for j in jobs_on] == ["app.workers.llm_jobs.llm_resume_job"]
    # The next tick finds nothing to probe: no second resume.
    assert llm_jobs.llm_watch_job(provider_factory=FakeLLMProvider) == "nothing due"
    assert len(jobs_on) == 1


def test_resume_runs_once_per_recovery(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
    jobs_on: list[tuple[str, Any]],
) -> None:
    project, merge_request = mr
    merge_request.state = "opened"
    review = run(db_session, FakeLLMProvider(boom), project, merge_request, llm_auto_resume=True)
    merge_request.head_sha = review.reviewed_sha
    health(db_session).status = "recovering"
    db_session.flush()
    assert llm_jobs.llm_resume_job() == {"queued": 1, "left": 0}
    assert llm_jobs.llm_resume_job() == {"queued": 0, "left": 0}  # not recovering any more
    row = health(db_session)
    assert (row.status, row.requeued_last) == ("ok", 1)
    assert [j[0] for j in jobs_on] == ["app.workers.review_jobs.review_mr_job"]
    assert db_session.scalars(select(Review).where(Review.llm_retry.is_(True))).all() == []


def test_switched_off_while_down_closes_the_watch(
    db_session: Session, jobs_on: list[tuple[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_jobs, "effective", lambda session, settings: {"llm_auto_resume": False})
    llm_recovery.mark_down(db_session, "llm: (HTTP 429)", 30)
    health(db_session).next_probe_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()
    assert llm_jobs.llm_watch_job(provider_factory=FakeLLMProvider) == "switched off"
    assert health(db_session).status == "ok" and jobs_on == []


def run_as(
    session: Session, provider: Any, project: Project, merge_request: MergeRequest, trigger: str
) -> Review:
    """`run`, started by `trigger` (who asked for it decides whether it may skip the wait)."""
    import respx

    from app.collector.gitlab.client import GitLabClient
    from app.review.service import ReviewService, ReviewSettings
    from tests.collector.fake_gitlab import API, BASE, fixture, mount
    from tests.review.test_service import HEAD, SETTINGS

    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json={**fixture("merge_requests.json")[0], "description": "Adds the parser"}
        )
        settings = ReviewSettings(**{**SETTINGS.__dict__, "llm_auto_resume": True})
        service = ReviewService(session, client, provider, settings)
        result: Review = service.review_mr(project, merge_request, HEAD, trigger=trigger)
        return result


def test_while_down_a_members_request_tries_an_account_that_works_again(
    db_session: Session,
    mr: tuple[Project, MergeRequest],  # noqa: F811
) -> None:
    """User 2026-11-09: a member's own request does not wait for the next probe when a Claude
    account is usable again; the probe is brought forward for the others."""
    from app.models import LlmAccount

    project, merge_request = mr
    run(db_session, FakeLLMProvider(boom), project, merge_request, llm_auto_resume=True)
    assert health(db_session).status == "down"
    later = health(db_session).next_probe_at
    assert later is not None and later > datetime.now(UTC)

    # No stored account is usable: even a member's request waits.
    provider = FakeLLMProvider()
    waiting = run_as(db_session, provider, project, merge_request, "telegram")
    assert waiting.error == llm_recovery.WAITING_ERROR and provider.prompts == []

    # An automatic review (a webhook) waits for the probe even with an account usable.
    account = LlmAccount(label="Dự phòng", token_encrypted="x", source="paste", state="ok")
    db_session.add(account)
    db_session.flush()
    auto = FakeLLMProvider()
    still = run_as(db_session, auto, project, merge_request, "webhook")
    assert auto.prompts == [] and still.error == llm_recovery.WAITING_ERROR

    # A backup works (or the default's retry time came): the member's request runs now.
    done = run_as(db_session, provider, project, merge_request, "telegram")
    assert done.status == "done" and provider.prompts
    probe = health(db_session).next_probe_at
    assert probe is not None and probe <= datetime.now(UTC)  # the others follow soon
