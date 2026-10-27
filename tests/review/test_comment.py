import json
from decimal import Decimal

import httpx
import respx

from app.collector.gitlab.client import GitLabClient
from app.models import Review, ReviewFinding
from app.review.comment import MARKER, post_or_update, render
from tests.collector.fake_gitlab import API, BASE

NOTES = f"{API}/projects/7/merge_requests/3/notes"


def finding(
    severity: str,
    title: str,
    confidence: float = 0.9,
    verified: str = "skipped",
    status: str = "open",
) -> ReviewFinding:
    return ReviewFinding(
        severity=severity,
        category="bug",
        file_path="app/x.py",
        line_start=10,
        title=title,
        confidence=Decimal(str(confidence)),
        verified=verified,
        status=status,
    )


def review() -> Review:
    return Review(
        reviewed_sha="d" * 40, rubric_version="v1", score=Decimal("42.5"), summary="Tóm tắt."
    )


def test_render_lists_counts_and_top_five() -> None:
    findings = [finding("low", f"low {i}") for i in range(6)] + [finding("critical", "SQL|inj")]
    body = render(review(), findings)
    assert body.startswith(MARKER)
    assert "CodePulse review: 42/100" in body
    assert "critical: 1" in body and "low: 6" in body and "high: 0" in body
    table = [line for line in body.splitlines() if line.startswith("| ")][1:]
    assert len(table) == 5
    assert "SQL\\|inj" in table[0]  # most severe first, pipe escaped for the table
    assert "và 2 vấn đề khác" in body
    assert "`dddddddd`" in body


def test_render_without_findings() -> None:
    assert "Không phát hiện vấn đề nào." in render(review(), [])


def test_render_needs_human_lists_unverified_findings_and_hides_rejected_ones() -> None:
    r = review()
    r.verdict, r.risk_score = "needs_human", 0
    findings = [
        finding("critical", "SQL injection", verified="unverified"),
        finding("high", "Race", verified="confirmed"),
        finding("high", "Ghost", verified="rejected", status="rejected"),
        finding("low", "Naming"),
    ]
    body = render(r, findings)
    assert "⚠️ **cần người xem**" in body and "CẦN NGƯỜI XEM" not in body
    assert "⚠️ **Cần người xem** — chưa kiểm chứng được:" in body
    assert "- 🔴 `app/x.py:10` — SQL injection" in body
    assert "| 🔴 critical ? |" in body and "| 🟠 high ✔ |" in body
    assert "Ghost" not in body and "high: 1" in body
    assert "1 finding bị bác khi kiểm chứng độc lập" in body


@respx.mock
def test_creates_then_updates_the_same_note() -> None:
    notes: list[dict[str, object]] = [{"id": 1, "body": "LGTM", "system": False}]

    def create(request: httpx.Request) -> httpx.Response:
        note = {"id": 99, "system": False, **json.loads(request.content)}
        notes.append(note)
        return httpx.Response(201, json=note)

    respx.get(NOTES).mock(side_effect=lambda r: httpx.Response(200, json=notes))
    respx.post(NOTES).mock(side_effect=create)
    update = respx.put(f"{NOTES}/99").respond(json={"id": 99})

    with GitLabClient(BASE, "t") as client:
        assert post_or_update(client, 7, 3, f"{MARKER}\nfirst") == 99
        assert post_or_update(client, 7, 3, f"{MARKER}\nsecond") == 99
    assert len(notes) == 2
    assert json.loads(update.calls.last.request.content) == {"body": f"{MARKER}\nsecond"}


@respx.mock
def test_gitlab_errors_never_fail_the_review() -> None:
    respx.get(NOTES).respond(json=[])
    respx.post(NOTES).respond(403)
    with GitLabClient(BASE, "t") as client:
        assert post_or_update(client, 7, 3, "x") is None
