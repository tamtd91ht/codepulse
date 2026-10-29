from types import SimpleNamespace
from typing import Any

import pytest

from app.collector.gitlab.client import GitLabError
from app.config import get_settings
from app.review.service import REVIEW_REPORTED
from app.workers import review_jobs


@pytest.fixture
def given_up(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def give_up(*args: Any, **kwargs: Any) -> None:
        calls.append((args[4], args[5]))  # reason, status

    monkeypatch.setattr(review_jobs, "_give_up", give_up)
    return calls


def attempt(monkeypatch: pytest.MonkeyPatch, retries_left: int | None) -> None:
    job = SimpleNamespace(retries_left=retries_left)
    monkeypatch.setattr(review_jobs, "get_current_job", lambda: job)


def fail(exc: BaseException) -> None:
    review_jobs._note_failure(get_settings(), 1, "rerun", 7, exc, sha="a" * 40)


def test_an_asked_run_is_given_up_only_on_its_last_attempt(
    monkeypatch: pytest.MonkeyPatch, given_up: list[tuple[str, str]]
) -> None:
    attempt(monkeypatch, 2)  # RQ will retry: the live view lists it as waiting to retry
    fail(RuntimeError("x"))
    assert given_up == []
    attempt(monkeypatch, 0)
    fail(GitLabError("GET https://gitlab.x/api/v4/projects/1/repository/commits/a -> HTTP 502"))
    fail(RuntimeError("https://user:secret@llm.example/v1?key=abc failed"))
    assert given_up == [
        (
            "lỗi GitLab: GET https://gitlab.x/api/v4/projects/1/repository/commits/a -> HTTP 502",
            "failed",
        ),
        ("lỗi trước khi review: RuntimeError", "failed"),  # other messages: the kind only
    ]


def test_a_review_that_started_is_not_reported_again(
    monkeypatch: pytest.MonkeyPatch, given_up: list[tuple[str, str]]
) -> None:
    attempt(monkeypatch, None)  # no retry configured: this is the last attempt
    exc = RuntimeError("LLM broke mid-review")
    exc.add_note(REVIEW_REPORTED)  # ReviewService ended it already (live view, row "failed")
    fail(exc)
    assert given_up == []
    fail(RuntimeError("before the review"))
    assert given_up == [("lỗi trước khi review: RuntimeError", "failed")]
