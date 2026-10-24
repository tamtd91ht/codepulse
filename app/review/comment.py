"""MR summary comment (docs/specs/06 "Đăng comment lên MR", task 3.7).

One note per MR, updated on every re-review. docs/specs/03 has no column for the note id, so the
note carries a hidden marker and is found again by it.
"""

import logging
from collections import Counter

from app.collector.gitlab.client import GitLabClient, GitLabError
from app.models import Review, ReviewFinding
from app.review.postprocess import SEVERITY_RANK
from app.scoring.verdict import VERDICT_LABEL

log = logging.getLogger(__name__)

MARKER = "<!-- codepulse-review -->"
TOP_FINDINGS = 5
SEVERITY_ICONS = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
VERDICT_ICONS = {"approve": "✅", "fix_required": "⚠️", "block": "❌"}


def render(review: Review, findings: list[ReviewFinding]) -> str:
    counts = Counter(f.severity for f in findings)
    score = f"{float(review.score):.0f}/100" if review.score is not None else "—"
    verdict = ""
    if review.verdict:
        label = VERDICT_LABEL.get(review.verdict, review.verdict)
        icon = VERDICT_ICONS.get(review.verdict, "")
        verdict = f" · {icon} **{label}** (risk {review.risk_score or 0})"
    lines = [
        MARKER,
        f"### CodePulse review: {score}{verdict}",
        "",
        " · ".join(f"{SEVERITY_ICONS[s]} {s}: {counts.get(s, 0)}" for s in SEVERITY_RANK),
        "",
    ]
    if review.summary:
        lines += [review.summary, ""]
    intent = review.intent or {}
    if intent.get("summary"):
        drift = (
            "" if intent.get("matches_intent") in (None, True) else " · **lệch so với mô tả MR**"
        )
        lines += [f"**Thay đổi làm gì:** {intent['summary']}{drift}", ""]
        for concern in intent.get("concerns") or []:
            lines.append(f"- 🧭 {concern}")
        if intent.get("concerns"):
            lines.append("")
    top = sorted(findings, key=lambda f: (-SEVERITY_RANK[f.severity], -(f.confidence or 0)))
    if top:
        lines.append("| Mức | Vị trí | Vấn đề |")
        lines.append("|---|---|---|")
        for f in top[:TOP_FINDINGS]:
            where = f"`{f.file_path}:{f.line_start}`"
            title = (f.title or "").replace("|", "\\|")
            lines.append(f"| {SEVERITY_ICONS[f.severity]} {f.severity} | {where} | {title} |")
        if len(top) > TOP_FINDINGS:
            lines.append(f"\n… và {len(top) - TOP_FINDINGS} vấn đề khác trên dashboard.")
    else:
        lines.append("Không phát hiện vấn đề nào.")
    lines += [
        "",
        f"<sub>Commit `{review.reviewed_sha[:8]}` · rubric {review.rubric_version} · "
        "điểm là tín hiệu coaching, không phải căn cứ duy nhất để đánh giá.</sub>",
    ]
    return "\n".join(lines)


def post_or_update(
    client: GitLabClient, project_external_id: int, iid: int, body: str
) -> int | None:
    """Edit our earlier note, else create one. Never raises: the comment is optional."""
    try:
        existing = next(
            (
                n
                for n in client.mr_notes(project_external_id, iid)
                if not n.get("system") and MARKER in (n.get("body") or "")
            ),
            None,
        )
        if existing:
            client.update_mr_note(project_external_id, iid, int(existing["id"]), body)
            return int(existing["id"])
        return int(client.create_mr_note(project_external_id, iid, body)["id"])
    except GitLabError as exc:
        log.warning(
            "could not post MR comment",
            extra={"project": project_external_id, "iid": iid, "status": exc.status_code},
        )
        return None
