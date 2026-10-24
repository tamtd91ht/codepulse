"""What a notification says about a review: one JSON document, and a text rendering of it
for chat webhooks (Slack, Teams)."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    BusinessProject,
    Commit,
    Connection,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    User,
)

EVENTS = ("review.done", "review.failed", "review.severe")
EVENT_LABEL = {
    "review.done": "Review xong",
    "review.failed": "Review lỗi",
    "review.severe": "Review có critical/high",
    "ping": "Kiểm tra webhook",
}
SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERE = ("critical", "high")
TOP_FINDINGS = 5
# Reviews with status skipped (too large, nothing reviewable) are not a result: no event.
NOTIFIED_STATUSES = ("done", "failed")


def events_for(status: str, counts: dict[str, int]) -> list[str]:
    """The events a finished review raises; a severe review raises done and severe."""
    if status == "failed":
        return ["review.failed"]
    if status != "done":
        return []
    events = ["review.done"]
    if any(counts.get(s, 0) for s in SEVERE):
        events.append("review.severe")
    return events


def public_error(error: str | None) -> str | None:
    """The kind of failure, never its detail: GitLab errors carry internal URLs and LLM errors
    may echo the prompt (the diff), and neither may leave CodePulse (docs/specs/11)."""
    if not error:
        return None
    return error.split(":", 1)[0].strip()[:120] or None


def esc(value: object) -> str:
    """Slack/Teams text: & < > are markup (<!channel>, <url|text>), so derived text is escaped."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def finding_counts(session: Session, review_id: int) -> dict[str, int]:
    rows = session.execute(
        select(ReviewFinding.severity, func.count())
        .where(ReviewFinding.review_id == review_id)
        .group_by(ReviewFinding.severity)
    ).all()
    counts = {s: 0 for s in SEVERITIES}
    for severity, n in rows:
        counts[str(severity)] = int(n)
    counts["total"] = sum(counts[s] for s in SEVERITIES)
    return counts


def _target(session: Session, review: Review) -> tuple[Project | None, str | None, str]:
    """Project, GitLab web URL and a short label ("!7" / "commit ab12cd34") of the target."""
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        project = session.get(Project, mr.project_id) if mr and mr.project_id else None
        label, path = (f"!{mr.iid}", f"/-/merge_requests/{mr.iid}") if mr else ("MR", None)
    else:
        commit = session.get(Commit, review.commit_sha) if review.commit_sha else None
        project = session.get(Project, commit.project_id) if commit and commit.project_id else None
        sha = review.commit_sha or ""
        label, path = f"commit {sha[:8]}", f"/-/commit/{sha}"
    url = None
    if project and path and project.connection_id is not None:
        connection = session.get(Connection, project.connection_id)
        if connection:
            url = f"{connection.base_url}/{project.path_with_namespace}{path}"
    return project, url, label


def build(
    session: Session, review: Review, dashboard_url: str, now: datetime | None = None
) -> dict[str, Any]:
    """The JSON document sent for a review; `event` is filled in per delivery."""
    project, web_url, label = _target(session, review)
    counts = finding_counts(session, review.id)
    top = session.scalars(
        select(ReviewFinding)
        .where(ReviewFinding.review_id == review.id)
        .order_by(
            # critical first, then the most confident
            case({s: i for i, s in enumerate(SEVERITIES)}, value=ReviewFinding.severity, else_=9),
            ReviewFinding.confidence.desc().nulls_last(),
            ReviewFinding.id,
        )
        .limit(TOP_FINDINGS)
    ).all()
    author = session.get(User, review.author_user_id) if review.author_user_id else None
    business = (
        session.get(BusinessProject, project.business_project_id)
        if project and project.business_project_id is not None
        else None
    )
    return {
        "sent_at": (now or datetime.now(UTC)).isoformat(),
        "review": {
            "id": review.id,
            "status": review.status,
            "target_type": review.target_type,
            "label": f"{project.path_with_namespace} {label}" if project else label,
            "reviewed_sha": review.reviewed_sha,
            "score": float(review.score) if review.score is not None else None,
            "loc_reviewed": review.loc_reviewed,
            "summary": review.summary,
            "model": review.model,
            "rubric_version": review.rubric_version,
            "error": public_error(review.error),
            "finished_at": review.finished_at.isoformat() if review.finished_at else None,
            "findings": counts,
            "top_findings": [
                {
                    "severity": f.severity,
                    "category": f.category,
                    "title": f.title,
                    "file_path": f.file_path,
                    "line_start": f.line_start,
                }
                for f in top
            ],
            "url": f"{dashboard_url.rstrip('/')}/reviews/{review.id}",
            "web_url": web_url,
        },
        "project": (
            {"id": project.id, "path_with_namespace": project.path_with_namespace}
            if project
            else None
        ),
        "business_project": {"id": business.id, "name": business.name} if business else None,
        "author": {"user_id": author.id, "name": author.full_name} if author else None,
    }


def sample(dashboard_url: str, now: datetime | None = None) -> dict[str, Any]:
    """The payload of the admin's "Gửi thử" button: shaped like a real one, clearly fake."""
    return {
        "sent_at": (now or datetime.now(UTC)).isoformat(),
        "review": {
            "id": 0,
            "status": "done",
            "target_type": "mr",
            "label": "demo/app !1",
            "reviewed_sha": "0" * 40,
            "score": 88.0,
            "loc_reviewed": 42,
            "summary": "Đây là thông báo thử từ CodePulse.",
            "model": None,
            "rubric_version": "v1",
            "error": None,
            "finished_at": (now or datetime.now(UTC)).isoformat(),
            "findings": {**{s: 0 for s in SEVERITIES}, "high": 1, "total": 1},
            "top_findings": [
                {
                    "severity": "high",
                    "category": "security",
                    "title": "Ví dụ finding",
                    "file_path": "app/example.py",
                    "line_start": 1,
                }
            ],
            "url": f"{dashboard_url.rstrip('/')}/reviews/0",
            "web_url": None,
        },
        "project": {"id": 0, "path_with_namespace": "demo/app"},
        "business_project": None,
        "author": None,
    }


def text(event: str, payload: dict[str, Any]) -> str:
    """Markdown-ish text for chat webhooks: one headline, counts, top findings, link."""
    review = payload["review"]
    business = payload.get("business_project")
    prefix = f"[{esc(business['name'])}] " if business else ""
    score = f"{review['score']:.0f}/100" if review.get("score") is not None else "—"
    headline = f"*{prefix}{esc(review['label'])} — {EVENT_LABEL.get(event, event)}"
    headline += f": {score}*" if review["status"] == "done" else "*"
    counts = review.get("findings") or {}
    parts = [f"{counts[s]} {s}" for s in SEVERITIES if counts.get(s)]
    facts = [", ".join(parts) if parts else "không có finding"]
    if review.get("loc_reviewed") is not None:
        facts.append(f"{review['loc_reviewed']} LOC")
    author = payload.get("author")
    if author and author.get("name"):
        facts.append(f"tác giả {esc(author['name'])}")
    lines = [headline, " · ".join(facts)]
    if review["status"] == "failed" and review.get("error"):
        lines.append(f"Lỗi: {esc(review['error'][:200])}")
    for f in review.get("top_findings") or []:
        where = f" ({esc(f['file_path'])}:{f['line_start']})" if f.get("file_path") else ""
        lines.append(
            f"• [{f['severity']}] {esc(f['category'])}: {esc(f.get('title') or '')}{where}"
        )
    lines.append(f"<{review['url']}|Xem review>")
    return "\n".join(lines)
