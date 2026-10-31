"""Closing critical/high findings a later change fixed (app/review/resolve.py)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.models import Commit, FindingClosure, Project, Review, ReviewFinding
from app.review.prepare import parse_diff
from app.review.providers import FakeLLMProvider, ReviewPrompt
from app.review.providers.fake import no_findings
from app.review.resolve import check, recent_open
from app.review.rubric import RUBRIC_VERSION
from app.review.service import ReviewService, ReviewSettings
from tests.collector.fake_gitlab import BASE, mount
from tests.collector.test_collect import make_project

HEAD = "d" * 40
MADE = datetime(2026, 10, 2, 9, tzinfo=UTC)
SETTINGS = ReviewSettings(
    ignore_paths=["*.lock"],
    sensitive_paths=[".env*"],
    max_review_loc=1500,
    max_tokens_per_chunk=12000,
    min_confidence=0.5,
)
DIFF = [parse_diff("app/review/parser.py", "@@ -1,2 +1,2 @@\n-q = 'x' + v\n+q = bind(v)\n")]


def answers(*items: dict[str, Any]) -> Any:
    """A responder: the review itself finds nothing, the resolve check answers `items`."""

    def respond(prompt: ReviewPrompt) -> Any:
        if "EARLIER FINDINGS" in prompt.user:
            return {"answers": list(items)}
        return no_findings(prompt)

    return respond


FIXED = {
    "index": 0,
    "verdict": "fixed",
    "reason": "Đã tham số hoá câu SQL.",
    "evidence_file": "app/review/parser.py",
    "evidence_lines": "1-2",
    "confidence": 0.9,
}


def finding(**kw: Any) -> ReviewFinding:
    return ReviewFinding(
        **{
            "severity": "critical",
            "category": "security",
            "file_path": "app/review/parser.py",
            "line_start": 1,
            "title": "SQL injection",
            "message": "Input concatenated into SQL.",
            "code_excerpt": "+q = 'x' + v",
            **kw,
        }
    )


def earlier(
    session: Session, project: Project, sha: str, when: datetime, *findings: ReviewFinding
) -> Review:
    session.add(Commit(sha=sha, project_id=project.id, authored_at=when))
    review = Review(
        target_type="commit",
        commit_sha=sha,
        reviewed_sha=sha,
        status="done",
        changed_at=when,
        finished_at=when,
        rubric_version=RUBRIC_VERSION,
    )
    session.add(review)
    session.flush()
    for f in findings:
        f.review_id = review.id
        session.add(f)
    session.flush()
    return review


def review_head(session: Session, project: Project, provider: Any, **kw: Any) -> Review:
    commit = Commit(sha=HEAD, project_id=project.id, message="fix: bind\n", authored_at=MADE)
    session.add(commit)
    session.flush()
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        router.get(url__regex=r".*/repository/files/.*").mock(
            return_value=httpx.Response(200, text="def parse():\n    pass\n")
        )
        mount(router)
        settings = ReviewSettings(**{**SETTINGS.__dict__, **kw})
        review = ReviewService(session, client, provider, settings).review_commit(project, commit)
    assert review is not None
    return review


# -- the check -----------------------------------------------------------------------------------


def test_only_a_confident_fix_with_evidence_in_the_diff_closes() -> None:
    findings = [finding(id=1), finding(id=2), finding(id=3), finding(id=4)]
    provider = FakeLLMProvider(
        lambda p: {
            "answers": [
                FIXED,
                {**FIXED, "index": 1, "confidence": 0.5},  # not sure enough
                {**FIXED, "index": 2, "evidence_file": "other.py"},  # fix not in this diff
                {"index": 3, "verdict": "partial", "reason": "Còn một chỗ."},
                {**FIXED, "index": 9},  # no such finding
            ]
        }
    )
    result = check(provider, "demo/p", "commit d", DIFF, findings)
    assert [(o.finding_id, o.verdict, o.closes) for o in result.outcomes] == [
        (1, "fixed", True),
        (2, "fixed", False),
        (3, "fixed", False),
        (4, "partial", False),
    ]
    assert result.outcomes[0].evidence == "app/review/parser.py:1-2"
    assert "+q = bind(v)" in provider.prompts[0].user  # the diff
    assert "+q = 'x' + v" in provider.prompts[0].user  # the stored excerpt, not the old file


def test_an_unreadable_answer_closes_nothing() -> None:
    result = check(FakeLLMProvider(lambda p: "no json here"), "r", "c", DIFF, [finding(id=1)])
    assert result.outcomes == []
    assert result.call is not None  # paid for all the same
    assert result.error is not None


# -- after a review --------------------------------------------------------------------------------


@pytest.mark.integration
def test_a_later_commit_closes_what_it_fixed(db_session: Session) -> None:
    project = make_project(db_session)
    recent = finding()
    earlier(db_session, project, "e" * 40, MADE - timedelta(hours=20), recent)
    old = finding(title="Old")
    earlier(db_session, project, "f" * 40, MADE - timedelta(days=5), old)  # outside N days
    medium = finding(severity="medium", title="Medium")
    earlier(db_session, project, "1" * 40, MADE - timedelta(hours=2), medium)  # not severe
    provider = FakeLLMProvider(answers(FIXED))

    review = review_head(db_session, project, provider, auto_close_days=2)

    assert review.status == "done"
    assert (recent.status, old.status, medium.status) == ("fixed", "open", "open")
    assert recent.severity == "critical"  # closed, not downgraded
    closure = db_session.scalars(select(FindingClosure)).one()
    assert (closure.kind, closure.status, closure.review_id) == ("auto", "closed", review.id)
    assert closure.shas == [HEAD]
    assert closure.evidence == "app/review/parser.py:1-2"
    assert review.basis is not None
    assert review.basis["auto_close"] == {"days": 2, "checked": [recent.id], "closed": [recent.id]}
    asked = provider.prompts[-1].user
    assert "SQL injection" in asked and "Old" not in asked and "Medium" not in asked
    # The check is paid by the review that ran it: in the month's budget with it.
    assert review.cost_usd == Decimal("0.0200")


@pytest.mark.integration
def test_switched_off_nothing_is_checked(db_session: Session) -> None:
    project = make_project(db_session)
    recent = finding()
    earlier(db_session, project, "e" * 40, MADE - timedelta(hours=20), recent)
    provider = FakeLLMProvider(answers(FIXED))

    review_head(db_session, project, provider, auto_close_days=0)

    assert recent.status == "open"
    assert not any("EARLIER FINDINGS" in p.user for p in provider.prompts)


@pytest.mark.integration
def test_a_partial_fix_leaves_it_open(db_session: Session) -> None:
    project = make_project(db_session)
    recent = finding()
    earlier(db_session, project, "e" * 40, MADE - timedelta(hours=20), recent)
    partial = {"index": 0, "verdict": "partial", "reason": "Còn chỗ khác.", "confidence": 0.9}

    review = review_head(db_session, project, FakeLLMProvider(answers(partial)), auto_close_days=2)

    assert recent.status == "open"
    assert db_session.scalars(select(FindingClosure)).first() is None
    assert review.basis is not None and review.basis["auto_close"]["closed"] == []


@pytest.mark.integration
def test_candidates_are_the_repos_recent_open_severe_ones(db_session: Session) -> None:
    project = make_project(db_session)
    touched = finding(file_path="a.py", title="touched")
    elsewhere = finding(file_path="b.py", title="elsewhere")
    earlier(db_session, project, "e" * 40, MADE - timedelta(hours=5), elsewhere)
    earlier(db_session, project, "f" * 40, MADE - timedelta(hours=10), touched)
    closed = finding(status="fixed", title="closed")
    later = finding(title="later")  # a change after this one cannot be fixed by it
    earlier(db_session, project, "1" * 40, MADE - timedelta(hours=1), closed)
    earlier(db_session, project, "2" * 40, MADE + timedelta(hours=1), later)
    found = recent_open(
        db_session,
        project.id,
        since=MADE - timedelta(days=1),
        until=MADE,
        touched=["a.py"],
        exclude_review=0,
    )
    assert [f.title for f in found] == ["touched", "elsewhere"]
