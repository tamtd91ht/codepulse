from pathlib import Path
from typing import Any

import pytest

from app.review.postprocess import Finding
from app.review.prepare import parse_diff
from app.review.prompt import ReviewParseError
from app.review.providers import FakeLLMProvider, LLMError, ReviewPrompt
from app.review.verify import (
    Verdict,
    VerifyReport,
    apply_verdict,
    batches,
    build_verify_prompt,
    evidence_ok,
    initial_status,
    parse_verdicts,
    select_candidates,
    skipped_status,
    verify,
)


def finding(**kw: Any) -> Finding:
    base: dict[str, Any] = {
        "severity": "high",
        "category": "security",
        "file_path": "app/api.py",
        "line_start": 2,
        "line_end": 2,
        "title": "Missing tenant check",
        "message": "m",
        "suggestion": "",
        "confidence": 0.9,
        "code_excerpt": "+q = db.find(id)",
        "fingerprint": "fp-1",
        "rule_id": "rubric:1",
    }
    base.update(kw)
    return Finding(**base)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "api.py").write_text("import db\nq = db.find(id)\nreturn q\n")
    (tmp_path / "app" / "auth.py").write_text("def guard(ctx):\n    assert ctx.tenant\n" * 3)
    return tmp_path


DIFF = {
    "app/api.py": parse_diff(
        "app/api.py", "@@ -1,1 +1,3 @@\n import db\n+q = db.find(id)\n+return q\n"
    )
}


def test_candidates_are_severe_or_knowledge_findings_in_severity_then_confidence_order() -> None:
    findings = [
        finding(severity="low", title="low"),
        finding(severity="medium", confidence=0.5, title="m1"),
        finding(severity="critical", confidence=0.6, title="c"),
        finding(severity="info", rule_id="rk:inv-1", title="rk"),
        finding(severity="high", confidence=0.7, title="h1"),
        finding(severity="high", confidence=0.95, title="h2"),
        finding(severity="medium", confidence=0.9, title="m2"),
    ]
    order = [findings[i].title for i in select_candidates(findings, 12)]
    assert order == ["c", "h2", "h1", "m2", "m1", "rk"]
    assert [findings[i].title for i in select_candidates(findings, 2)] == ["c", "h2"]
    assert select_candidates(findings, 0) == []


def test_batches_group_the_same_file_five_at_a_time() -> None:
    findings = [finding(file_path="a.py", title=str(i)) for i in range(7)] + [
        finding(file_path="b.py", title="b")
    ]
    assert batches(findings, list(range(8))) == [[0, 1, 2, 3, 4], [5, 6], [7]]
    assert batches(findings, [7, 0]) == [[7], [0]]


def test_the_prompt_is_fresh_and_adversarial() -> None:
    prompt = build_verify_prompt(
        "demo/p", "MR: Add lookup", "## Invariants\n- rk:inv-1", [finding()], [0]
    )
    assert "prove it wrong or less severe" in prompt.system
    assert "cannot vouch for it" in prompt.system  # "+" lines are not evidence
    assert "[0] high security app/api.py:2-2 — Missing tenant check" in prompt.user
    assert "+q = db.find(id)" in prompt.user and "rk:inv-1" in prompt.user
    assert prompt.json_schema["title"] == "VerifyBatch"


def test_parse_verdicts_is_lenient_about_junk() -> None:
    batch = parse_verdicts(
        '{"verdicts": [{"index": "1", "verdict": "Rejected", "confidence": 3, "severity": "HIGH"},'
        ' {"index": true, "verdict": "maybe"}, "junk"]}'
    )
    first, second = batch.verdicts
    assert (first.index, first.verdict, first.confidence, first.severity) == (
        1,
        "rejected",
        1,
        "high",
    )
    assert (second.index, second.verdict) == (-1, "unverified")
    with pytest.raises(ReviewParseError):
        parse_verdicts("nope")


def verdict(**kw: Any) -> Verdict:
    return Verdict.model_validate({"index": 0, "verdict": "rejected", "reason": "r", **kw})


def test_evidence_must_be_existing_unchanged_lines(repo: Path) -> None:
    ok = evidence_ok
    assert ok(repo, verdict(evidence_file="app/auth.py", evidence_lines="1-2"), DIFF)
    assert ok(repo, verdict(evidence_file="app/auth.py", evidence_lines="2"), DIFF)
    assert ok(repo, verdict(evidence_file="app/auth.py", evidence_lines="4:3"), DIFF)  # swapped
    # Unchanged line of a changed file: fine. A "+" line of the diff: never.
    assert ok(repo, verdict(evidence_file="app/api.py", evidence_lines="1"), DIFF)
    assert not ok(repo, verdict(evidence_file="app/api.py", evidence_lines="2"), DIFF)
    assert not ok(repo, verdict(evidence_file="app/api.py", evidence_lines="1-3"), DIFF)
    assert not ok(repo, verdict(evidence_file="/app/api.py", evidence_lines="3"), DIFF)
    # Missing pieces.
    assert not ok(repo, verdict(evidence_file="app/auth.py"), DIFF)  # no lines
    assert not ok(repo, verdict(evidence_file="app/auth.py", evidence_lines="1-2", reason=""), DIFF)
    assert not ok(repo, verdict(evidence_lines="1"), DIFF)  # no file
    assert not ok(repo, verdict(evidence_file="app/auth.py", evidence_lines="40-50"), DIFF)
    assert not ok(repo, verdict(evidence_file="nope.py", evidence_lines="1"), DIFF)
    assert not ok(repo, verdict(evidence_file="../etc/passwd", evidence_lines="1"), DIFF)


def test_apply_verdict_outcomes(repo: Path) -> None:
    report = VerifyReport()
    confirmed = apply_verdict(
        finding(), verdict(verdict="confirmed", confidence=0.8), repo, DIFF, 0.01, report
    )
    assert (confirmed.verified, confirmed.status, confirmed.severity) == (
        "confirmed",
        "open",
        "high",
    )
    assert confirmed.verification == {
        "verdict": "confirmed",
        "reason": "r",
        "evidence": None,
        "confidence": 0.8,
        "cost_usd": 0.01,
    }
    rejected = apply_verdict(
        finding(), verdict(evidence_file="app/auth.py", evidence_lines="1-2"), repo, DIFF, 0, report
    )
    assert (rejected.verified, rejected.status) == ("rejected", "rejected")
    assert (
        rejected.verification is not None and rejected.verification["evidence"] == "app/auth.py:1-2"
    )
    # A rejection whose only evidence is the change itself is not accepted.
    weak = apply_verdict(
        finding(), verdict(evidence_file="app/api.py", evidence_lines="2"), repo, DIFF, 0, report
    )
    assert (weak.verified, weak.status) == ("unverified", "open")
    assert weak.verification is not None and weak.verification["ignored"].startswith("rejected")
    assert report.ignored_evidence == 1
    down = apply_verdict(
        finding(),
        verdict(
            verdict="downgraded", severity="low", evidence_file="app/auth.py", evidence_lines="1"
        ),
        repo,
        DIFF,
        0,
        report,
    )
    assert (down.verified, down.severity) == ("downgraded", "low")
    assert down.verification is not None and down.verification["from_severity"] == "high"
    # "Downgraded" to the same or a higher severity is not a downgrade.
    up = apply_verdict(
        finding(),
        verdict(
            verdict="downgraded",
            severity="critical",
            evidence_file="app/auth.py",
            evidence_lines="1",
        ),
        repo,
        DIFF,
        0,
        report,
    )
    assert up.verified == "unverified" and up.severity == "high"
    unknown = apply_verdict(finding(), verdict(verdict="maybe"), repo, DIFF, 0, report)
    assert unknown.verified == "unverified"


def test_initial_status_marks_severe_findings_unverified_and_the_rest_skipped() -> None:
    out = initial_status([finding(severity="critical"), finding(severity="low")])
    assert [f.verified for f in out] == ["unverified", "skipped"]


def test_skipped_status_leaves_every_finding_as_reported() -> None:
    """Verification not configured: nothing is `unverified` (that word is for a step that ran
    and could not settle a finding), so the pre-v4 verdict rules apply."""
    out = skipped_status([finding(severity="critical"), finding(severity="low")])
    assert [(f.severity, f.verified, f.status) for f in out] == [
        ("critical", "skipped", "open"),
        ("low", "skipped", "open"),
    ]


def answers(prompt: ReviewPrompt) -> dict[str, Any]:
    return {
        "verdicts": [
            {"index": 0, "verdict": "confirmed", "reason": "thật", "confidence": 0.9},
            {
                "index": 1,
                "verdict": "rejected",
                "reason": "guard() chặn",
                "evidence_file": "app/auth.py",
                "evidence_lines": "1-2",
            },
            {"index": 1, "verdict": "confirmed", "reason": "second word is ignored"},
            {"index": 7, "verdict": "rejected", "reason": "not in this batch"},
        ]
    }


def test_verify_runs_batches_and_records_cost(repo: Path) -> None:
    agent = FakeLLMProvider(answers, cost_usd=0.02)
    findings = [
        finding(severity="critical", title="c"),
        finding(title="h", fingerprint="fp-2"),
        finding(severity="low", title="l", fingerprint="fp-3"),
        finding(severity="medium", title="m", fingerprint="fp-4", file_path="app/auth.py"),
    ]
    seen: list[float] = []
    out, report = verify(
        findings,
        agent=agent,
        root=repo,
        diff_files=DIFF,
        project="demo/p",
        target="MR: x",
        knowledge="## Invariants",
        max_findings=12,
        max_turns=15,
        timeout_s=120,
        on_result=lambda r: seen.append(r.cost_usd),
    )
    assert [(f.title, f.verified, f.status) for f in out] == [
        ("c", "confirmed", "open"),
        ("h", "rejected", "rejected"),
        ("l", "skipped", "open"),
        ("m", "unverified", "open"),  # its batch answered about other indexes only
    ]
    assert out[0].verification is not None and out[0].verification["cost_usd"] == 0.01
    assert report.as_dict() == {
        "selected": 3,
        "calls": 2,
        "failed_calls": 0,
        "cost_usd": 0.04,
        "ignored_evidence": 0,
        "confirmed": 1,
        "downgraded": 0,
        "rejected": 1,
        "unverified": 1,
        "skipped": 1,
    }
    assert seen == [0.02, 0.02]
    calls = agent.repo_calls
    assert [c["max_turns"] for c in calls] == [15, 15]
    assert "[0] critical" in calls[0]["prompt"].user and "[3] medium" in calls[1]["prompt"].user
    assert "## Invariants" in calls[0]["prompt"].user


def test_a_failing_or_unusable_call_leaves_its_findings_unverified(repo: Path) -> None:
    def flaky(prompt: ReviewPrompt) -> Any:
        if "[0]" in prompt.user:
            raise LLMError("timeout")
        return "not json"

    findings = [finding(severity="critical"), finding(file_path="app/auth.py", fingerprint="fp-2")]
    out, report = verify(
        findings, agent=FakeLLMProvider(flaky), root=repo, diff_files=DIFF, project="p", target="t"
    )
    assert [f.verified for f in out] == ["unverified", "unverified"]
    assert (report.calls, report.failed_calls) == (2, 2)
