from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.models import Commit, MergeRequest, Project, Review, ReviewFinding
from app.review.providers import FakeLLMProvider, LLMError, ReviewPrompt
from app.review.service import ReviewService, ReviewSettings
from tests.collector.fake_gitlab import API, BASE, fixture, mount
from tests.collector.test_collect import make_project

pytestmark = pytest.mark.integration

HEAD = "d" * 40
SETTINGS = ReviewSettings(
    ignore_paths=["*.lock"],
    sensitive_paths=[".env*"],
    max_review_loc=1500,
    max_tokens_per_chunk=12000,
    min_confidence=0.5,
)


def sqli(prompt: ReviewPrompt) -> dict[str, Any]:
    return {
        "summary": "Parser added.",
        "score": 60,
        "findings": [
            {
                "severity": "critical",
                "category": "security",
                "file_path": "app/review/parser.py",
                "line_start": 5,
                "line_end": 6,
                "title": "SQL injection",
                "message": "Input concatenated into SQL.",
                "suggestion": "Parameterize.",
                "confidence": 0.9,
            },
            {
                "severity": "low",
                "category": "style",
                "file_path": "app/review/parser.py",
                "line_start": 900,
                "line_end": 900,
                "title": "Invented line",
                "message": "m",
                "confidence": 0.9,
            },
        ],
    }


@pytest.fixture
def mr(db_session: Session) -> tuple[Project, MergeRequest]:
    project = make_project(db_session)
    merge_request = MergeRequest(project_id=project.id, iid=1, title="Parser", head_sha=HEAD)
    db_session.add(merge_request)
    db_session.flush()
    return project, merge_request


def run(session: Session, provider: Any, project: Project, mr: MergeRequest, **kw: Any) -> Any:
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json={**fixture("merge_requests.json")[0], "description": "Adds the parser"}
        )
        settings = ReviewSettings(**{**SETTINGS.__dict__, **kw})
        return ReviewService(session, client, provider, settings).review_mr(project, mr, HEAD)


def test_reviews_scores_and_stores_findings(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    older = Review(target_type="mr", mr_id=merge_request.id, reviewed_sha="a" * 40, status="done")
    db_session.add(older)
    db_session.flush()
    provider = FakeLLMProvider(sqli, cost_usd=0.03)

    review = run(db_session, provider, project, merge_request)

    assert review.status == "done"
    assert review.loc_reviewed == 31  # yarn.lock ignored
    assert float(review.score) == 50.0  # 100 - 22.5 = 77.5, capped by the critical
    assert (review.model, float(review.cost_usd)) == ("fake-model", 0.03)
    assert review.summary == "Parser added."
    findings = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).all()
    assert [(x.severity, x.title) for x in findings] == [("critical", "SQL injection")]
    assert findings[0].code_excerpt == "+new line 4\n+new line 5"
    assert "yarn.lock" not in provider.prompts[0].user
    assert "Adds the parser" in provider.prompts[0].user
    db_session.refresh(older)
    assert older.status == "superseded"


def test_same_sha_is_reviewed_once(db_session: Session, mr: tuple[Project, MergeRequest]) -> None:
    project, merge_request = mr
    provider = FakeLLMProvider()
    assert run(db_session, provider, project, merge_request).status == "done"
    assert run(db_session, provider, project, merge_request) is None
    assert len(provider.prompts) == 1


def test_skips_projects_that_forbid_external_llm(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    project.allow_external_llm = False
    provider = FakeLLMProvider()
    review = run(db_session, provider, project, merge_request)
    assert (review.status, review.error) == ("skipped", "project does not allow external LLM")
    assert provider.prompts == []


def test_skips_oversized_changes(db_session: Session, mr: tuple[Project, MergeRequest]) -> None:
    project, merge_request = mr
    provider = FakeLLMProvider()
    review = run(db_session, provider, project, merge_request, max_review_loc=10)
    assert (review.status, review.error) == ("skipped", "MR quá lớn: 31 LOC")
    assert provider.prompts == []


def test_llm_failure_marks_failed_and_can_be_retried(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr

    def boom(prompt: ReviewPrompt) -> Any:
        raise LLMError("claude failed after 4 attempts")

    failed = run(db_session, FakeLLMProvider(boom), project, merge_request)
    assert (failed.status, failed.error) == ("failed", "llm: claude failed after 4 attempts")
    retried = run(db_session, FakeLLMProvider(), project, merge_request)
    assert retried.id == failed.id and retried.status == "done"


def test_stale_running_review_is_taken_over(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    stuck = Review(
        target_type="mr",
        mr_id=merge_request.id,
        reviewed_sha=HEAD,
        rubric_version="v1",
        status="running",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(stuck)
    db_session.flush()
    assert run(db_session, FakeLLMProvider(), project, merge_request).id == stuck.id


def test_commit_review(db_session: Session) -> None:
    project = make_project(db_session)
    commit = Commit(sha=HEAD, project_id=project.id, message="fix: handle empty input\n")
    db_session.add(commit)
    db_session.flush()
    provider = FakeLLMProvider()
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(url__regex=r".*/repository/files/.*").mock(
            return_value=httpx.Response(200, text="def parse():\n    pass\n")
        )
        service = ReviewService(db_session, client, provider, SETTINGS)
        review = service.review_commit(project, commit)
    assert review is not None
    assert (review.target_type, review.commit_sha, review.status) == ("commit", HEAD, "done")
    assert "Commit: fix: handle empty input" in provider.prompts[0].user
    assert "### Context: app/review/parser.py" in provider.prompts[0].user


@pytest.mark.parametrize(("spent", "status"), [(7.5, "done"), (10.0, "failed")])
def test_monthly_budget_pauses_reviews(
    db_session: Session, mr: tuple[Project, MergeRequest], spent: float, status: str
) -> None:
    project, merge_request = mr
    db_session.add(
        Review(
            target_type="commit",
            reviewed_sha="9" * 40,
            status="done",
            cost_usd=spent,
            started_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    provider = FakeLLMProvider()
    review = run(db_session, provider, project, merge_request, monthly_budget_usd=10.0)
    assert review.status == status
    if status == "failed":
        assert review.error == "monthly LLM budget reached"
        assert provider.prompts == []
