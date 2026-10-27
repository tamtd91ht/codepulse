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
from app.review.service import (
    KNOWLEDGE_DOMAIN_NOTE,
    ReviewService,
    ReviewSettings,
    merge_intents,
)
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
    # No agentic provider, so no verification was configured: the critical stands as reported
    # (`skipped`, the pre-v4 semantics): 100 - 30 * 0.9 = 73, capped at 50, and it blocks.
    assert float(review.score) == 50.0
    assert (review.verdict, review.risk_score) == ("block", 10)
    assert (review.model, float(review.cost_usd)) == ("fake-model", 0.03)
    assert review.summary == "Parser added."
    findings = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).all()
    assert [(x.severity, x.title) for x in findings] == [("critical", "SQL injection")]
    assert findings[0].code_excerpt == "+new line 4\n+new line 5"
    assert (findings[0].verified, findings[0].status) == ("skipped", "open")
    assert review.deep is not None and review.deep["mode"] == "fast"
    assert review.deep["verification"] == {"skipped": "không có provider có tool"}
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
    # No verification configured: the high stands as reported and needs a fix (19 §4.4).
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


# -- review flow v2 (docs/specs/19 §4): one checkout, agentic pass, verification -------------------


def archive_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"demo-p-{HEAD[:8]}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


AGENT_SETTINGS = ReviewSettings(
    **{
        **SETTINGS.__dict__,
        "agent_review": True,
        "agent_min_loc": 20,
        "agent_max_archive_bytes": 10_000_000,
    }
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


def agent_answer(prompt: ReviewPrompt) -> dict[str, Any]:
    """The agentic pass ([1]) and the verification ([2]) share one fake provider: the system
    prompt says which one is asking."""
    if "independent reviewer" in prompt.system:
        return {
            "verdicts": [
                {
                    "index": 0,
                    "verdict": "confirmed",
                    "reason": "chuỗi nối thẳng",
                    "confidence": 0.9,
                },
                {
                    "index": 1,
                    "verdict": "downgraded",
                    "severity": "medium",
                    "reason": "guard() chặn ở caller",
                    "evidence_file": "app/guard.py",
                    "evidence_lines": "1-2",
                    "confidence": 0.8,
                },
            ]
        }
    return {
        "summary": "Parser mới; đã đối chứng.",
        "intent": {
            "summary": "Thêm parser",
            "matches_intent": True,
            "altitude": "right",
            "concerns": [],
        },
        "applied_rules": [
            {"rule_id": "rk:inv-sql", "applies": True, "where": "app/review/parser.py:5"},
            {"rule_id": "rk:chk-migrations", "applies": False, "why": "không đổi migration"},
        ],
        "findings": [
            {
                "severity": "critical",
                "category": "security",
                "file_path": "app/review/parser.py",
                "line_start": 5,
                "line_end": 6,
                "title": "SQL injection",
                "message": "Input concatenated into SQL.",
                "confidence": 0.9,
                "rule_id": "rk:inv-sql",
                "evidence": "app/review/parser.py:5",
            },
            {  # no evidence: confidence capped at 0.6, no rule: rubric:4
                "severity": "high",
                "category": "bug",
                "file_path": "app/review/parser.py",
                "line_start": 6,
                "line_end": 6,
                "title": "Parser bỏ qua lỗi",
                "message": "m",
                "confidence": 0.8,
            },
            {  # outside the diff: informational for the author, never scored
                "severity": "high",
                "category": "security",
                "file_path": "app/guard.py",
                "line_start": 1,
                "line_end": 2,
                "title": "guard() không kiểm tra role",
                "message": "m",
                "confidence": 0.9,
                "evidence": "app/guard.py:2",
            },
        ],
        "project_fit": ["Đúng kiến trúc; nên đặt parser cạnh app/billing"],
    }


class ProfileOrChunk:
    """The plain provider: the profile call (by its system prompt) or the no-tool pass."""

    name = "fake"

    def __init__(self, chunk: FakeLLMProvider) -> None:
        self._chunk = chunk
        self._profile = FakeLLMProvider(lambda p: profile_answer())

    def review(self, prompt: ReviewPrompt) -> Any:
        if "onboarding brief" in prompt.system:
            return self._profile.review(prompt)
        return self._chunk.review(prompt)


def mount_repo(router: respx.MockRouter, iid: int = 1) -> None:
    mount(router)
    router.get(f"{API}/projects/1001/merge_requests/{iid}").respond(
        json=fixture("merge_requests.json")[0]
    )
    router.get(f"{API}/projects/1001/repository/archive.tar.gz").mock(
        return_value=httpx.Response(200, content=archive_bytes(REPO_FILES))
    )


def test_agentic_pass_then_verification_scores_only_what_survived(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    # A profile the Admin button built from the default branch: the fallback while the repo has
    # no compiled knowledge. The review never builds one from the sha it judges.
    project.profile = {**profile_answer(), "version": 1}
    project.profile_sha, project.profile_at = "e" * 40, datetime.now(UTC) - timedelta(days=60)
    agent = FakeLLMProvider(agent_answer)
    fast = FakeLLMProvider(sqli)
    plain = ProfileOrChunk(fast)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        service = ReviewService(db_session, client, plain, AGENT_SETTINGS, agent=agent)
        review = service.review_mr(project, merge_request, HEAD)

    assert review is not None and review.status == "done" and review.mode == "agent"
    assert fast.prompts == []  # the no-tool pass is a fallback only
    findings = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).all()
    assert sorted((f.title, f.severity, f.verified, f.status) for f in findings) == [
        ("Parser bỏ qua lỗi", "medium", "downgraded", "open"),
        ("SQL injection", "critical", "confirmed", "open"),
        ("guard() không kiểm tra role", "info", "skipped", "open"),
    ]
    by_title = {f.title: f for f in findings}
    sql, capped, note = (
        by_title["SQL injection"],
        by_title["Parser bỏ qua lỗi"],
        by_title["guard() không kiểm tra role"],
    )
    assert sql.rule_id == "rk:inv-sql" and sql.verification is not None
    assert sql.verification["verdict"] == "confirmed"
    assert "Bằng chứng: app/review/parser.py:5" in (sql.message or "")
    assert float(capped.confidence or 0) == 0.6 and capped.rule_id == "rubric:4"
    assert capped.verification is not None
    assert capped.verification["evidence"] == "app/guard.py:1-2"
    assert capped.verification["from_severity"] == "high"
    assert (note.message or "").startswith("[ngoài phần thay đổi]")
    # block on the confirmed critical; risk on confirmed + downgraded (10 + 1).
    assert (review.verdict, review.risk_score) == ("block", 11)
    # 100 - (30*0.9 + 5*0.6) = 70, then the cap of a confirmed critical.
    assert float(review.score or 0) == 50.0
    assert review.deep is not None
    assert review.deep["verification"]["confirmed"] == 1
    assert review.deep["verification"]["downgraded"] == 1
    assert review.deep["verification"]["calls"] == 1  # same file: one batch
    assert review.deep["applied_rules"][0]["rule_id"] == "rk:inv-sql"
    assert review.deep["project_fit"] == ["Đúng kiến trúc; nên đặt parser cạnh app/billing"]
    assert review.deep["files_seen"] == len(REPO_FILES)
    assert review.basis is not None and review.basis["applied_rules"] == [
        {"rule_id": "rk:inv-sql", "applies": True, "where": "app/review/parser.py:5"},
        {"rule_id": "rk:chk-migrations", "applies": False, "where": ""},
    ]
    assert review.basis["rubric"]["version"] == RUBRIC_VERSION == "v4"
    assert review.basis["profile_source"] == "cached"
    assert review.basis["profile"]["sha"] == "e" * 12
    assert review.summary == "Parser mới; đã đối chứng."
    assert review.intent is not None and review.intent["summary"] == "Thêm parser"
    main, verification = agent.repo_calls
    assert main["tools"] == ["Read", "Grep", "Glob"] and main["max_turns"] == 40
    assert "PROJECT KNOWLEDGE" in main["prompt"].user and "applied_rules" in main["prompt"].system
    assert "### Context: app/review/parser.py" in main["prompt"].user  # read from the checkout
    assert "Kind: backend-service · Domain: payments" in main["prompt"].user
    # The verification is a fresh prompt: the finding, never the main pass's reasoning.
    assert verification["max_turns"] == 15
    assert "[0] critical security app/review/parser.py:5-6" in verification["prompt"].user
    assert "Parser mới; đã đối chứng." not in verification["prompt"].user
    assert "applied_rules" not in verification["prompt"].user
    assert not Path(main["workdir"]).exists()  # the checkout is gone
    # The cached profile was read, never rebuilt or rewritten from the reviewed sha.
    assert plain._profile.prompts == []
    assert (project.profile_sha, project.profile["domain"]) == ("e" * 40, "payments")
    # agent pass + verification
    assert float(review.cost_usd or 0) == pytest.approx(0.02)


def test_agentic_failure_falls_back_to_the_no_tool_pass(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr

    def broken(prompt: ReviewPrompt) -> Any:
        raise LLMError("agent timeout")

    agent = FakeLLMProvider(broken)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        service = ReviewService(
            db_session, client, ProfileOrChunk(FakeLLMProvider(sqli)), AGENT_SETTINGS, agent=agent
        )
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and (review.status, review.mode) == ("done", "fast")
    assert review.deep is not None
    assert review.deep["fallback"] == {"from": "agentic", "error": "LLMError: agent timeout"}
    # The verification call failed too: the critical stays unverified -> a person decides.
    assert review.deep["verification"]["failed_calls"] == 1
    finding = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).one()
    assert (finding.severity, finding.verified) == ("critical", "unverified")
    assert (review.verdict, review.risk_score) == ("needs_human", 0)
    assert float(review.score or 0) == 73.0  # unverified at 0.9 counts; no confirmed critical cap


def test_unparseable_agent_answer_is_repaired_once_then_falls_back(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    agent = FakeLLMProvider(lambda p: "no json at all")
    # The plain provider answers the repair with garbage too, then runs the no-tool pass.
    fast = FakeLLMProvider(lambda p: "still no json" if "did not match" in p.user else sqli(p))
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        service = ReviewService(
            db_session, client, ProfileOrChunk(fast), AGENT_SETTINGS, agent=agent
        )
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.mode == "fast"
    assert review.deep is not None and review.deep["fallback"]["error"].startswith(
        "ReviewParseError: invalid after repair"
    )
    # The repair went to the plain provider (no tools); then the no-tool pass ran there too.
    assert [("did not match the schema" in p.user) for p in fast.prompts] == [True, False]


def test_a_small_diff_skips_the_agentic_pass_but_is_still_verified(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    agent = FakeLLMProvider(agent_answer)
    big_min = ReviewSettings(**{**AGENT_SETTINGS.__dict__, "agent_min_loc": 100})
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        service = ReviewService(db_session, client, FakeLLMProvider(sqli), big_min, agent=agent)
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.mode == "fast"
    assert review.deep is not None and review.deep["skipped"] == "diff dưới 100 LOC"
    (verification,) = agent.repo_calls
    assert "independent reviewer" in verification["prompt"].system
    finding = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).one()
    assert (finding.severity, finding.verified) == ("critical", "confirmed")
    assert review.verdict == "block" and float(review.score or 0) == 50.0


def test_checkout_failure_runs_the_no_tool_pass_with_findings_as_reported(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    small = ReviewSettings(**{**AGENT_SETTINGS.__dict__, "agent_max_archive_bytes": 100})
    agent = FakeLLMProvider(agent_answer)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        router.get(f"{API}/projects/1001/repository/archive.tar.gz").mock(
            return_value=httpx.Response(200, content=b"x" * 5000)
        )
        service = ReviewService(db_session, client, FakeLLMProvider(sqli), small, agent=agent)
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.mode == "fast"
    assert review.deep is not None and review.deep["error"] == "checkout"
    assert review.deep["verification"] == {"skipped": "không có bản sao mã nguồn"}
    assert agent.repo_calls == []
    # [2] could not run at all, so nothing is "unverified": the critical blocks as reported.
    finding = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).one()
    assert finding.verified == "skipped"
    assert (review.verdict, float(review.score or 0)) == ("block", 50.0)


def test_verification_off_keeps_the_findings_as_reported(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    """VERIFY=false is not a failed verification: a critical blocks, as before rubric v4."""
    project, merge_request = mr
    review = run(db_session, FakeLLMProvider(sqli), project, merge_request, verify=False)
    assert review.status == "done"
    assert review.deep["verification"] == {"skipped": "tắt"}
    finding = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).one()
    assert (finding.severity, finding.verified) == ("critical", "skipped")
    assert (review.verdict, review.risk_score, float(review.score)) == ("block", 10, 50.0)


def test_verify_max_findings_zero_skips_the_verification(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    agent = FakeLLMProvider(agent_answer)
    none = ReviewSettings(**{**AGENT_SETTINGS.__dict__, "verify_max_findings": 0})
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        service = ReviewService(
            db_session, client, ProfileOrChunk(FakeLLMProvider(sqli)), none, agent=agent
        )
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.mode == "agent"
    assert len(agent.repo_calls) == 1  # the main pass only
    assert review.deep is not None
    assert review.deep["verification"] == {"skipped": "VERIFY_MAX_FINDINGS=0"}
    verified = {
        f.title: f.verified
        for f in db_session.scalars(
            select(ReviewFinding).where(ReviewFinding.review_id == review.id)
        )
    }
    assert set(verified.values()) == {"skipped"}
    assert review.verdict == "block"


def test_secrets_in_the_models_text_never_reach_the_database(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    token = "sk-ant-api03-" + "k" * 40

    def leaky(prompt: ReviewPrompt) -> dict[str, Any]:
        answer = sqli(prompt)
        answer["findings"][0]["message"] = f"Dùng token {token} thẳng trong SQL."
        answer["findings"][0]["suggestion"] = f"Bỏ {token}."
        answer["intent"] = {"summary": f"Thêm {token}", "concerns": [f"lộ {token}"]}
        return answer

    review = run(db_session, FakeLLMProvider(leaky), project, merge_request)
    assert review.status == "done"
    finding = db_session.scalars(
        select(ReviewFinding).where(ReviewFinding.review_id == review.id)
    ).one()
    assert token not in (finding.message or "") and "***REDACTED***" in (finding.message or "")
    assert token not in (finding.suggestion or "")
    assert review.intent is not None and token not in str(review.intent)


# -- docs/specs/19 §4.6: three short transactions -----------------------------------------------


def test_short_transactions_release_the_claim_while_the_model_works(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    """The worker's mode: the claim is committed before the model is called (the FOR UPDATE
    lock is gone), the passes run with no transaction open, the result is committed after
    the save. The test session's savepoints make the commits real for the service."""
    project, merge_request = mr
    db_session.expire_on_commit = False  # as the worker's sessionmaker (app/db.py)
    seen: list[tuple[bool, str | None]] = []

    def answer(prompt: ReviewPrompt) -> dict[str, Any]:
        row = db_session.scalar(select(Review.status).where(Review.reviewed_sha == HEAD))
        seen.append((db_session.in_transaction(), row))
        db_session.commit()  # the read above opened one; leave it as the service does
        return sqli(prompt)

    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json=fixture("merge_requests.json")[0]
        )
        service = ReviewService(
            db_session, client, FakeLLMProvider(answer), SETTINGS, short_transactions=True
        )
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.status == "done"
    # The model was called with nothing open, and the claimed row was already committed.
    assert seen == [(False, "running")]
    assert not db_session.in_transaction()  # the save was committed too
    db_session.refresh(review)
    assert review.verdict == "block" and review.finished_at is not None


def test_a_crash_in_short_transactions_marks_the_review_failed(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    """The claim is committed, so a crash must not leave the row `running` until the stale
    takeover: it is marked failed (and can be rerun) before the error propagates."""
    project, merge_request = mr

    def boom(prompt: ReviewPrompt) -> Any:
        raise RuntimeError("worker lost its mind")

    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json=fixture("merge_requests.json")[0]
        )
        service = ReviewService(
            db_session, client, FakeLLMProvider(boom), SETTINGS, short_transactions=True
        )
        with pytest.raises(RuntimeError, match="worker lost its mind"):
            service.review_mr(project, merge_request, HEAD)
    review = db_session.scalars(select(Review).where(Review.reviewed_sha == HEAD)).one()
    assert review.status == "failed"
    assert (review.error or "").startswith("crash: RuntimeError: worker lost its mind")
    # And a rerun takes the failed row over as usual.
    again = run(db_session, FakeLLMProvider(sqli), project, merge_request)
    assert again.id == review.id and again.status == "done"


def test_the_repo_switch_disables_the_agentic_pass_only(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    project.deep_review = False
    agent = FakeLLMProvider(agent_answer)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        service = ReviewService(db_session, client, FakeLLMProvider(), AGENT_SETTINGS, agent=agent)
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.mode == "fast"
    assert review.deep is not None and "skipped" not in review.deep
    assert review.deep["verification"]["selected"] == 0  # nothing to verify
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

    def end(self, review_id: int, status: str | None = None, error: str | None = None) -> None:
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


def test_the_v2_flow_reports_checkout_agent_and_verify_stages(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    recorder = Recorder()
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        service = ReviewService(
            db_session,
            client,
            ProfileOrChunk(FakeLLMProvider(sqli)),
            AGENT_SETTINGS,
            agent=FakeLLMProvider(agent_answer),
            progress=recorder,
        )
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.mode == "agent"
    stages = [e[1] for e in recorder.events if e[0] == "stage"]
    assert stages == ["diff", "checkout", "agent", "verify", "verify", "save"]


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


def test_the_agentic_pass_streams_its_actions_to_the_live_view(
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
    agent = FakeLLMProvider(agent_answer, events=events)
    progress = MemoryProgress()
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        service = ReviewService(
            db_session,
            client,
            ProfileOrChunk(FakeLLMProvider(sqli)),
            AGENT_SETTINGS,
            agent=agent,
            progress=progress,
        )
        review = service.review_mr(project, merge_request, HEAD)

    assert review is not None and review.mode == "agent"
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


def test_without_knowledge_the_shared_claude_repo_is_quoted_as_guidance(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    """docs/specs/19 lộ trình step 1: while a project has no compiled knowledge, the .claude
    guidance of docs/specs/17/18 reaches both the agentic and the no-tool prompt."""
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
    agent = FakeLLMProvider(agent_answer)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        router.get(f"{API}/projects/2002").respond(json={"id": 2002, "default_branch": "main"})
        router.get(f"{API}/projects/2002/repository/commits/main").respond(json={"id": "c" * 40})
        router.get(f"{API}/projects/2002/repository/archive.tar.gz").mock(
            return_value=httpx.Response(200, content=archive_bytes(SHARED_FILES))
        )
        service = ReviewService(
            db_session, client, ProfileOrChunk(FakeLLMProvider(sqli)), AGENT_SETTINGS, agent=agent
        )
        review = service.review_mr(project, merge_request, HEAD)

    assert review is not None and review.mode == "agent" and review.basis is not None
    user = agent.repo_calls[0]["prompt"].user
    assert "PROJECT KNOWLEDGE (compiled review knowledge; cite rk:<id>):\n(none yet)" in user
    assert "Áp dụng skill deploy-checklist" in user  # the review procedure
    assert "Dự án: mọi service dùng tenantId từ JWT." in user
    assert "Không liên quan review." not in user
    guided = {g["path"]: g["status"] for g in review.basis["fast_guidance"]}
    assert guided["demo/claude-config:.claude/commands/review-mr.md"] == "used"
    assert "### General" in user and review.basis["checklists"][0] == "General"


def test_with_knowledge_the_claude_files_are_not_quoted(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr
    agent = FakeLLMProvider(agent_answer)

    class Knowing(ReviewService):
        def _knowledge_block(
            self, project: Project, prepared: Any
        ) -> tuple[str, dict[str, Any], bool]:
            text = "## Invariants\n- rk:inv-sql: no string SQL\n## DOMAIN\nDịch vụ thu phí."
            return text, {"version": 3, "stale": False}, True

    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount_repo(router)
        own = router.get(
            f"{API}/projects/1001/repository/archive.tar.gz", params={"path": ".claude"}
        )
        own.mock(
            return_value=httpx.Response(
                200, content=archive_bytes({".claude/CLAUDE.md": "Repo: luôn kiểm tra tenantId."})
            )
        )
        service = Knowing(
            db_session, client, ProfileOrChunk(FakeLLMProvider(sqli)), AGENT_SETTINGS, agent=agent
        )
        review = service.review_mr(project, merge_request, HEAD)
    assert review is not None and review.mode == "agent" and review.basis is not None
    user = agent.repo_calls[0]["prompt"].user
    assert "rk:inv-sql: no string SQL" in user
    assert "(none: see PROJECT KNOWLEDGE)" in user and "luôn kiểm tra tenantId" not in user
    assert not own.called  # the repo's .claude is not fetched at all
    assert review.basis["knowledge"] == {"version": 3, "stale": False}
    assert review.basis["fast_guidance"] == []
    # The knowledge's DOMAIN is the profile: no cached profile, nothing built from the sha.
    assert KNOWLEDGE_DOMAIN_NOTE in user and review.basis["profile_source"] == "knowledge"
    assert "profile" not in review.basis and project.profile is None
    # The verification gets the knowledge too, in short form.
    assert "rk:inv-sql: no string SQL" in agent.repo_calls[1]["prompt"].user


def test_the_no_tool_pass_reads_the_reviewed_repos_own_claude_folder(
    db_session: Session, mr: tuple[Project, MergeRequest]
) -> None:
    project, merge_request = mr  # no dự án, so no shared repo: only the repo's own .claude
    provider = FakeLLMProvider(sqli)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        # Replaces mount()'s "no .claude" route (respx keeps one route per pattern).
        own = router.get(
            f"{API}/projects/1001/repository/archive.tar.gz", params={"path": ".claude"}
        )
        own.mock(
            return_value=httpx.Response(
                200, content=archive_bytes({".claude/CLAUDE.md": "Repo: luôn kiểm tra tenantId."})
            )
        )
        router.get(f"{API}/projects/1001/merge_requests/1").respond(
            json=fixture("merge_requests.json")[0]
        )
        review = ReviewService(db_session, client, provider, SETTINGS).review_mr(
            project, merge_request, HEAD
        )
    assert own.calls.last.request.url.params["sha"] == HEAD
    assert "Repo: luôn kiểm tra tenantId." in provider.prompts[0].user
    assert review is not None and review.basis is not None
    (guided,) = review.basis["fast_guidance"]
    assert (guided["path"], guided["source"], guided["status"]) == (
        ".claude/CLAUDE.md",
        "repo",
        "used",
    )
