import io
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.models import (
    BusinessProject,
    Commit,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    User,
)
from app.review.progress import MemoryProgress
from app.review.providers import FakeLLMProvider, LLMError, ReviewPrompt
from app.review.rubric import RUBRIC_VERSION
from app.review.service import ReviewService, ReviewSettings, merge_intents
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
        rubric_version=RUBRIC_VERSION,
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
        # Registered before mount(): respx uses the first matching route.
        router.get(url__regex=r".*/repository/files/.*").mock(
            return_value=httpx.Response(200, text="def parse():\n    pass\n")
        )
        mount(router)
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


def techlead(prompt: ReviewPrompt) -> dict[str, Any]:
    """A v2-shaped answer: a finding that cites a team rule, plus design & intent notes."""
    return {
        "summary": "Parser added.",
        "score": 70,
        "intent": {
            "summary": "Thêm parser cho review.",
            "matches_intent": True,
            "altitude": "right",
            "concerns": ["Parser nằm trong app/review thay vì app/collector"],
        },
        "findings": [
            {
                "severity": "high",
                "category": "style",
                "file_path": "app/review/parser.py",
                "line_start": 5,
                "line_end": 5,
                "title": "Query without tenantId",
                "message": "m",
                "confidence": 0.8,
                "rule_id": "Quy tắc dự án OmiCRM",
            }
        ],
    }


def test_rules_reach_the_prompt_and_verdict_intent_rule_id_are_stored(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    from app.models import BusinessProject

    project, merge_request = mr
    bp = BusinessProject(name="OmiCRM", review_rules="- tenantId on every query")
    db_session.add(bp)
    db_session.flush()
    project.business_project_id = bp.id
    project.review_rules_paths = ".claude/rules/money.md"
    lead = User(full_name="Lead", primary_email="lead@x.vn")
    db_session.add(lead)
    db_session.flush()
    provider = FakeLLMProvider(techlead)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        router.get(url__regex=r".*/repository/files/\.claude%2Frules%2Fmoney\.md/raw").mock(
            return_value=httpx.Response(200, text="# Money safety\nidempotency")
        )
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json={**fixture("merge_requests.json")[0], "description": "Adds the parser"}
        )
        service = ReviewService(db_session, client, provider, SETTINGS)
        review = service.review_mr(
            project, merge_request, HEAD, trigger="dashboard", requested_by=lead.id
        )

    assert review is not None and review.status == "done"
    user_prompt = provider.prompts[0].user
    assert "## Quy tắc dự án OmiCRM\n- tenantId on every query" in user_prompt
    assert "## .claude/rules/money.md\n# Money safety" in user_prompt
    assert "Team rules" in user_prompt and "rule_id" in user_prompt
    assert (review.verdict, review.risk_score) == ("fix_required", 3)
    assert review.intent == {
        "summary": "Thêm parser cho review.",
        "matches_intent": True,
        "altitude": "right",
        "concerns": ["Parser nằm trong app/review thay vì app/collector"],
    }
    assert (review.trigger, review.requested_by_user_id) == ("dashboard", lead.id)
    finding = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).one()
    assert finding.rule_id == "Quy tắc dự án OmiCRM"
    assert review.rubric_version == RUBRIC_VERSION
    # The basis records what the review was checked against: names and hashes, never text.
    basis = review.basis
    assert basis is not None and basis["rubric"]["version"] == RUBRIC_VERSION
    assert [(r["kind"], r["name"], r["status"]) for r in basis["rules"]] == [
        ("business_project", "Quy tắc dự án OmiCRM", "used"),
        ("file", ".claude/rules/money.md", "used"),
    ]
    assert all(len(r["hash"]) == 10 for r in basis["rules"])
    assert "tenantId" not in str(basis)


def test_merge_intents_takes_the_worst_verdict_and_unions_concerns() -> None:
    from app.review.schema import Intent

    assert merge_intents([]) is None
    merged = merge_intents(
        [
            Intent(summary="A", matches_intent=True, altitude="right", concerns=["x"]),
            Intent(summary="B", matches_intent=False, altitude="under", concerns=["x", "y"]),
        ]
    )
    assert merged == {
        "summary": "A",
        "matches_intent": False,
        "altitude": "under",
        "concerns": ["x", "y"],
    }


def test_a_rerun_of_a_failed_review_records_the_new_trigger(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr

    def boom(prompt: ReviewPrompt) -> Any:
        raise LLMError("claude failed")

    failed = run(db_session, FakeLLMProvider(boom), project, merge_request)
    assert (failed.status, failed.trigger) == ("failed", "webhook")
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json=fixture("merge_requests.json")[0]
        )
        service = ReviewService(db_session, client, FakeLLMProvider(), SETTINGS)
        again = service.review_mr(project, merge_request, HEAD, trigger="rerun", requested_by=999)
    assert again is not None and again.id == failed.id and again.status == "done"
    assert (again.trigger, again.requested_by_user_id) == ("rerun", None)  # 999 is nobody


def test_rules_files_touched_by_the_diff_are_not_used(db_session: Session) -> None:
    from app.review.rules import build_rules

    project = Project(
        external_id=1, path_with_namespace="demo/p", review_rules_paths="rules.md, other.md"
    )
    text = build_rules(None, project, lambda p: f"content of {p}", skip={"rules.md"})
    assert "content of rules.md" not in text and "không dùng" in text
    assert "content of other.md" in text


# -- deep review (docs/specs/18) -------------------------------------------------------------------


def archive_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"demo-p-{HEAD[:8]}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


DEEP_SETTINGS = ReviewSettings(
    **{**SETTINGS.__dict__, "deep_review": True, "deep_max_archive_bytes": 10_000_000}
)
REPO_FILES = {
    "README.md": "# demo\nMột dịch vụ thu phí.",
    "CLAUDE.md": "Rules: every query carries tenantId.",
    ".claude/rules/critical/money.md": "# Money\n- idempotent",
    "app/review/parser.py": "import re\n" * 10,
    "app/guard.py": "def guard(ctx):\n    assert ctx.tenant\n",
}


def profile_answer() -> dict[str, Any]:
    return {
        "kind": "backend-service",
        "summary": "Dịch vụ thu phí.",
        "domain": "payments",
        "features": ["thu phí"],
        "stack": {"languages": ["Python"]},
        "security": {"tenancy": "tenantId từ JWT"},
        "risk_areas": ["app/billing"],
        "confidence": 0.7,
    }


def deep_answer(prompt: ReviewPrompt) -> dict[str, Any]:
    return {
        "summary": "Parser mới; đã đối chứng.",
        "intent": {
            "summary": "Thêm parser",
            "matches_intent": True,
            "altitude": "right",
            "concerns": [],
        },
        "verifications": [
            {
                "index": 0,
                "decision": "downgrade",
                "severity": "medium",
                "reason": "guard() chặn ở caller",
                "evidence_file": "app/guard.py",
                "evidence_lines": "1-2",
            }
        ],
        "findings": [
            {
                "severity": "high",
                "category": "bug",
                "file_path": "app/review/parser.py",
                "line_start": 6,
                "line_end": 6,
                "title": "Parser bỏ qua lỗi",
                "message": "m",
                "confidence": 0.8,
                "evidence": "app/review/parser.py:6",
            }
        ],
        "project_fit": ["Đúng kiến trúc; nên đặt parser cạnh app/billing"],
    }


class ProfileOrChunk:
    """The plain provider: the profile call (by its system prompt) or the first-pass chunk."""

    name = "fake"

    def __init__(self, chunk: FakeLLMProvider) -> None:
        self._chunk = chunk
        self._profile = FakeLLMProvider(lambda p: profile_answer())

    def review(self, prompt: ReviewPrompt) -> Any:
        if "onboarding brief" in prompt.system:
            return self._profile.review(prompt)
        return self._chunk.review(prompt)


def mount_deep(router: respx.MockRouter, iid: int = 1) -> None:
    mount(router)
    router.get(f"{API}/projects/1001/merge_requests/{iid}").respond(
        json=fixture("merge_requests.json")[0]
    )
    router.get(f"{API}/projects/1001/repository/archive.tar.gz").mock(
        return_value=httpx.Response(200, content=archive_bytes(REPO_FILES))
    )


def test_deep_review_verifies_candidates_adds_findings_and_caches_the_profile(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    agent = FakeLLMProvider(deep_answer)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_deep(router)
        service = ReviewService(
            db_session, client, ProfileOrChunk(FakeLLMProvider(sqli)), DEEP_SETTINGS, agent=agent
        )
        review = service.review_mr(project, merge_request, HEAD)

    assert review is not None and review.status == "done" and review.mode == "deep"
    findings = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).all()
    assert sorted((f.title, f.severity) for f in findings) == [
        ("Parser bỏ qua lỗi", "high"),
        ("SQL injection", "medium"),  # downgraded with evidence
    ]
    assert (review.verdict, review.risk_score) == ("fix_required", 4)
    assert review.deep is not None and review.deep["kept"] == 1 and review.deep["added"] == 1
    assert review.deep["downgraded"][0]["evidence"] == "app/guard.py:1-2"
    assert review.deep["project_fit"] == ["Đúng kiến trúc; nên đặt parser cạnh app/billing"]
    assert review.deep["instruction_sources"] == ["CLAUDE.md", ".claude/rules/critical/money.md"]
    assert review.basis is not None and review.basis["instructions"] == [
        {
            "path": "CLAUDE.md",
            "kind": "instructions",
            "source": "repo",
            "status": "used",
            "quoted": 36,
        },
    ]
    # Nothing in the change is about money: the rule is in the index, for the agent to Read.
    assert review.basis["claude_repo"] is None and review.basis["instructions_not_loaded"] == [
        {
            "path": ".claude/rules/critical/money.md",
            "source": "repo",
            "kind": "rules",
            "reason": "catalog",
            "read": False,
        }
    ]
    assert review.basis["rubric"]["version"] == RUBRIC_VERSION and review.basis["profile"]
    assert review.summary == "Parser mới; đã đối chứng."
    assert review.intent is not None and review.intent["summary"] == "Thêm parser"
    (call,) = agent.repo_calls
    assert call["tools"] == ["Read", "Grep", "Glob"] and call["max_turns"] == 40
    assert "Kind: backend-service · Domain: payments" in call["prompt"].user
    assert "### CLAUDE.md" in call["prompt"].user
    assert "- .claude/rules/critical/money.md (rules critical/money, của repo)" in (
        call["prompt"].user
    )
    assert "[0] critical security" in call["prompt"].user
    assert not Path(call["workdir"]).exists()  # the checkout is gone
    assert project.profile is not None and project.profile["domain"] == "payments"
    assert project.profile_sha == HEAD and project.profile_at is not None
    assert float(review.cost_usd or 0) == pytest.approx(0.03)  # chunk + profile + deep


def test_deep_review_failure_keeps_the_first_pass(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr

    def broken(prompt: ReviewPrompt) -> Any:
        raise LLMError("deep timeout")

    agent = FakeLLMProvider(broken)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_deep(router)
        service = ReviewService(
            db_session, client, ProfileOrChunk(FakeLLMProvider(sqli)), DEEP_SETTINGS, agent=agent
        )
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and (review.status, review.mode) == ("done", "fast")
    assert review.deep == {"error": "LLMError", "detail": "deep timeout"}
    assert review.verdict == "block"  # the first pass's critical stands

    # Archive too large: skipped before any model call, first pass stands.
    other = MergeRequest(project_id=project.id, iid=2, title="Other", head_sha="e" * 40)
    db_session.add(other)
    db_session.flush()
    small = ReviewSettings(**{**DEEP_SETTINGS.__dict__, "deep_max_archive_bytes": 100})
    agent = FakeLLMProvider(deep_answer)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_deep(router, iid=2)
        router.get(f"{API}/projects/1001/repository/archive.tar.gz").mock(
            return_value=httpx.Response(200, content=b"x" * 5000)
        )
        service = ReviewService(
            db_session, client, ProfileOrChunk(FakeLLMProvider()), small, agent=agent
        )
        review2 = service.review_mr(project, other, "e" * 40)
    assert review2 is not None and review2.mode == "fast"
    assert review2.deep is not None and review2.deep["error"] == "checkout"
    assert agent.repo_calls == []


def test_deep_review_respects_the_repo_switch(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    project.deep_review = False
    agent = FakeLLMProvider(deep_answer)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_deep(router)
        service = ReviewService(db_session, client, FakeLLMProvider(), DEEP_SETTINGS, agent=agent)
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.mode == "fast" and review.deep is None
    assert agent.repo_calls == []


def test_a_finished_head_is_not_reviewed_twice_unless_forced(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    first = run(db_session, FakeLLMProvider(sqli), project, merge_request)
    assert (first.status, first.round) == ("done", 1)

    def review(**kw: Any) -> Any:
        with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
            mount(router)
            router.get(f"{API}/projects/1001/merge_requests/1").respond(
                json=fixture("merge_requests.json")[0]
            )
            service = ReviewService(db_session, client, FakeLLMProvider(), SETTINGS)
            return service.review_mr(project, merge_request, HEAD, **kw)

    assert review() is None or review().id == first.id  # no automatic second review
    assert db_session.scalars(select(Review)).all() == [first]
    # A forced review needs the MR open at this head, checked when the job runs.
    assert review(force=True) is None
    merge_request.state = "opened"
    assert review(force=True, expected_round=2) is None  # a stale/duplicate request: no-op

    again = review(force=True, trigger="rerun", expected_round=1)
    assert again is not None and again.id != first.id
    assert (again.round, again.status, again.trigger) == (2, "done", "rerun")
    db_session.refresh(first)
    assert (first.status, first.superseded_by) == ("superseded", again.id)
    # The first round keeps its findings: history is never deleted.
    kept = db_session.scalars(select(ReviewFinding).where(ReviewFinding.review_id == first.id))
    assert [f.title for f in kept] == ["SQL injection"]
    # The same request again (a duplicate job) finds round 2 and does nothing.
    assert review(force=True, expected_round=1) is None


def test_a_failed_new_round_leaves_the_old_round_counted(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    merge_request.state = "opened"
    first = run(db_session, FakeLLMProvider(sqli), project, merge_request)

    def boom(prompt: ReviewPrompt) -> Any:
        raise LLMError("claude failed")

    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json=fixture("merge_requests.json")[0]
        )
        service = ReviewService(db_session, client, FakeLLMProvider(boom), SETTINGS)
        failed = service.review_mr(project, merge_request, HEAD, force=True)
    assert failed is not None and (failed.status, failed.round) == ("failed", 2)
    db_session.refresh(first)
    assert (first.status, first.superseded_by) == ("done", failed.id)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def start(self, review_id: int, info: dict[str, Any]) -> None:
        self.events.append(("start", info))

    def stage(self, review_id: int, stage: str, detail: str = "", **extra: Any) -> None:
        self.events.append(("stage", stage))

    def end(self, review_id: int, status: str | None = None) -> None:
        self.events.append(("end", (review_id, status)))

    def event(self, review_id: int, entries: list[dict[str, Any]]) -> None:
        self.events.append(("event", entries))


def test_a_review_reports_its_stages_to_the_live_view(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json=fixture("merge_requests.json")[0]
        )
        service = ReviewService(
            db_session, client, FakeLLMProvider(sqli), SETTINGS, progress=recorder
        )
        review = service.review_mr(project, merge_request, HEAD, trigger="dashboard")
    assert review is not None and review.status == "done"
    kind, info = recorder.events[0]
    assert kind == "start" and (info["kind"], info["ref"], info["title"]) == ("mr", "1", "Parser")
    assert info["trigger"] == "dashboard" and info["project"] == project.path_with_namespace
    stages = [e[1] for e in recorder.events if e[0] == "stage"]
    assert stages == ["diff", "llm", "save"]
    assert recorder.events[-1] == ("end", (review.id, "done"))


def test_the_live_view_is_cleared_when_a_review_fails(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    recorder = Recorder()

    def boom(prompt: ReviewPrompt) -> Any:
        raise LLMError("claude failed")

    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json=fixture("merge_requests.json")[0]
        )
        service = ReviewService(
            db_session, client, FakeLLMProvider(boom), SETTINGS, progress=recorder
        )
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.status == "failed"
    assert recorder.events[-1] == ("end", (review.id, "failed"))


def test_a_review_skipped_before_the_llm_records_no_basis(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    project.review_rules = "- tenantId everywhere"
    project.allow_external_llm = False
    review = run(db_session, FakeLLMProvider(sqli), project, merge_request)
    assert review.status == "skipped" and review.basis is None


def test_deep_review_streams_its_actions_to_the_live_view(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    events: list[dict[str, Any]] = [
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5"},
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Grep", "input": {"pattern": "guard"}}]
            },
        },
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "SECRET"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1},
    ]
    agent = FakeLLMProvider(deep_answer, events=events)
    progress = MemoryProgress()
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_deep(router)
        service = ReviewService(
            db_session,
            client,
            ProfileOrChunk(FakeLLMProvider(sqli)),
            DEEP_SETTINGS,
            agent=agent,
            progress=progress,
        )
        review = service.review_mr(project, merge_request, HEAD)

    assert review is not None and review.mode == "deep"
    entries = progress.live(review.id)
    assert [(e["kind"], e.get("target")) for e in entries] == [
        ("start", None),
        ("tool", "guard"),
        ("end", None),
    ]
    assert "SECRET" not in str(entries)


SHARED_FILES = {
    # The repo *is* the .claude folder: skills/, agents/, CLAUDE.md at its root.
    "CLAUDE.md": "Dự án: mọi service dùng tenantId từ JWT.",
    "skills/code-review/SKILL.md": "---\nname: code-review\n---\nKiểm tra idempotency.",
    "skills/java-conventions/SKILL.md": "Đặt tên package theo domain.",
    "skills/deploy/SKILL.md": "Không liên quan review.",
    "agents/security-reviewer.md": "Soát quyền truy cập.",
    "commands/review-mr.md": "---\ndescription: Review MR\n---\nÁp dụng skill deploy-checklist.",
}


def test_deep_review_reads_the_shared_claude_repo_of_the_dự_án(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    business = BusinessProject(
        name="Thu phí",
        claude_repo_connection_id=project.connection_id,
        claude_repo_external_id=2002,
        claude_repo_path="demo/claude-config",
        claude_repo_include="skills/java-conventions/SKILL.md",
    )
    db_session.add(business)
    db_session.flush()
    project.business_project_id = business.id
    agent = FakeLLMProvider(deep_answer)
    fast = FakeLLMProvider(sqli)
    plain = ProfileOrChunk(fast)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_deep(router)
        router.get(f"{API}/projects/2002").respond(json={"id": 2002, "default_branch": "main"})
        router.get(f"{API}/projects/2002/repository/commits/main").respond(json={"id": "c" * 40})
        router.get(f"{API}/projects/2002/repository/archive.tar.gz").mock(
            return_value=httpx.Response(200, content=archive_bytes(SHARED_FILES))
        )
        service = ReviewService(db_session, client, plain, DEEP_SETTINGS, agent=agent)
        review = service.review_mr(project, merge_request, HEAD)

    # The first pass already gets the shared configuration this change needs, and the profile
    # reads the shared CLAUDE.md under the shared repo's name.
    (first,) = fast.prompts
    assert "Áp dụng skill deploy-checklist" in first.user  # the review procedure
    assert "Dự án: mọi service dùng tenantId từ JWT." in first.user
    assert "Không liên quan review." not in first.user
    guided = {g["path"]: g["status"] for g in review.basis["fast_guidance"]}
    assert guided["demo/claude-config:.claude/commands/review-mr.md"] == "used"
    assert "### General" in first.user and review.basis["checklists"][0] == "General"
    (profile_call,) = plain._profile.prompts
    assert "### demo/claude-config:.claude/CLAUDE.md\nDự án: mọi service" in profile_call.user

    assert review is not None and review.mode == "deep" and review.basis is not None
    assert review.basis["claude_repo"] == {
        "repo": "demo/claude-config",
        "ref": "main",
        "include": ["skills/java-conventions/SKILL.md"],
        "sha": "c" * 12,
        "files": 6,
        "status": "used",
        "mounted": True,
    }
    mount = ".codepulse/claude-shared"
    shared = [
        (i["path"], i["kind"], i["status"], bool(i.get("procedure")))
        for i in review.basis["instructions"]
        if i["source"] == "shared"
    ]
    assert shared[:3] == [
        (f"{mount}/commands/review-mr.md", "command", "used", True),  # the review procedure
        (f"{mount}/skills/java-conventions/SKILL.md", "skill", "used", False),  # pinned
        (f"{mount}/CLAUDE.md", "instructions", "used", False),
    ]
    # Named for review: worth quoting, after those (their order follows the score).
    assert sorted(shared[3:]) == [
        (f"{mount}/agents/security-reviewer.md", "agent", "used", False),
        (f"{mount}/skills/code-review/SKILL.md", "skill", "used", False),
    ]
    (deploy,) = [
        (d["source"], d["kind"], d["reason"], d["read"])
        for d in review.basis["instructions_not_loaded"]
        if d["path"] == f"{mount}/skills/deploy/SKILL.md"
    ]
    assert deploy == ("shared", "skill", "catalog", False)
    call = agent.repo_calls[0]
    user = call["prompt"].user
    # The procedure first; shared files sit where the agent can Read them, and before the repo's.
    assert user.index("Áp dụng skill deploy-checklist") < user.index(f"### {mount}/CLAUDE.md")
    assert user.index(f"### {mount}/CLAUDE.md") < user.index("### CLAUDE.md")
    assert "Kiểm tra idempotency" in user and f"### {mount}/skills/deploy/SKILL.md" not in user
    assert f"- {mount}/skills/deploy/SKILL.md (skill deploy" in user  # indexed, not quoted


class ReadingAgent(FakeLLMProvider):
    """A deep-review agent that Reads `paths` (relative to the checkout) before answering."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(deep_answer)
        self._paths = paths

    def review_in_repo(self, prompt: ReviewPrompt, workdir: str, **kwargs: Any) -> Any:
        reads = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": f"{workdir}/{p}"}}
            for p in self._paths
        ]
        self._events = [{"type": "assistant", "message": {"content": reads}}]
        return super().review_in_repo(prompt, workdir, **kwargs)


def test_deep_review_names_incomplete_quotes_and_quotes_a_copy_once(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    long_skill = "Đo p99 của mọi endpoint dưới 200ms.\n" * 300
    business = BusinessProject(
        name="Thu phí",
        claude_repo_connection_id=project.connection_id,
        claude_repo_external_id=2002,
        claude_repo_path="demo/claude-config",
        claude_repo_include="skills/perf/SKILL.md",
    )
    db_session.add(business)
    db_session.flush()
    project.business_project_id = business.id
    mount_path = ".codepulse/claude-shared"
    perf = f"{mount_path}/skills/perf/SKILL.md"
    agent = ReadingAgent([perf])
    # The repo carries a copy of a shared skill.
    repo_files = {
        **REPO_FILES,
        ".claude/skills/code-review/SKILL.md": SHARED_FILES["skills/code-review/SKILL.md"],
    }
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json=fixture("merge_requests.json")[0]
        )
        router.get(f"{API}/projects/1001/repository/archive.tar.gz").mock(
            return_value=httpx.Response(200, content=archive_bytes(repo_files))
        )
        router.get(f"{API}/projects/2002").respond(json={"id": 2002, "default_branch": "main"})
        router.get(f"{API}/projects/2002/repository/commits/main").respond(json={"id": "c" * 40})
        router.get(f"{API}/projects/2002/repository/archive.tar.gz").mock(
            return_value=httpx.Response(
                200,
                content=archive_bytes({**SHARED_FILES, "skills/perf/SKILL.md": long_skill}),
            )
        )
        service = ReviewService(
            db_session, client, ProfileOrChunk(FakeLLMProvider(sqli)), DEEP_SETTINGS, agent=agent
        )
        review = service.review_mr(project, merge_request, HEAD)

    assert review is not None and review.mode == "deep" and review.basis is not None
    user = agent.repo_calls[0]["prompt"].user
    # The agent is told the long skill is incomplete, with its real size.
    assert f"- {perf}: only the first ~8000 of {len(long_skill)} characters are quoted" in user
    # The repo's copy of a shared skill is quoted once, and reported as a duplicate.
    assert user.count("Kiểm tra idempotency") == 1
    (copy,) = [d for d in review.basis["instructions_not_loaded"] if d["reason"] == "duplicate"]
    assert copy["path"] == ".claude/skills/code-review/SKILL.md"
    assert copy["same_as"] == f"{mount_path}/skills/code-review/SKILL.md"
    # The basis says the agent opened the whole skill.
    (record,) = [i for i in review.basis["instructions"] if i["path"] == perf]
    assert (record["status"], record["quoted"], record["read"]) == ("partial", 8000, True)


def test_an_unreadable_shared_repo_does_not_stop_the_deep_review(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    business = BusinessProject(
        name="Thu phí",
        claude_repo_connection_id=project.connection_id,
        claude_repo_external_id=2002,
        claude_repo_path="demo/claude-config",
        claude_repo_ref="main",
    )
    db_session.add(business)
    db_session.flush()
    project.business_project_id = business.id
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_deep(router)
        router.get(f"{API}/projects/2002/repository/commits/main").respond(404)
        service = ReviewService(
            db_session,
            client,
            ProfileOrChunk(FakeLLMProvider(sqli)),
            DEEP_SETTINGS,
            agent=FakeLLMProvider(deep_answer),
        )
        review = service.review_mr(project, merge_request, HEAD)

    assert review is not None and review.mode == "deep" and review.basis is not None
    assert review.basis["claude_repo"]["status"] == "error"
    assert {i["source"] for i in review.basis["instructions"]} == {"repo"}
