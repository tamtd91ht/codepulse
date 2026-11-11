"""Review on request from Claude Code (docs/specs/26 §22, user 2026-11-11): the developer
commits and pushes, then Claude asks CodePulse to review those commits; the result reaches them on
the web (the review page, the in-app notification of a finished review) and back in Claude Code
(`get_review_status`, then `get_commit_findings`).

`request_review` follows the dashboard's "Yêu cầu review" rules exactly (the shared
`app.api.routers.commits.queue_commit`): permission `review.request` over the author, a tracked
repo with review on, no merge commit, no second review of a commit already reviewed or queued
unless `again` (then `review.rerun`). On top, as for close requests (§8): every commit must
already be on GitLab in that repo (the full SHA GitLab returns must start with what was sent),
GitLab is asked with no DB transaction open and within a deadline, and a person may ask for at
most `mcp_reviews_per_day` reviews in 24 hours through MCP (Admin > Cấu hình). Reviews it queues
carry trigger "mcp" and go to the queue served first (a person is waiting).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.admin_settings import effective
from app.api.audit import audit
from app.api.routers.closures import parse_commit
from app.api.routers.commits import QueueOutcome, queue_commit
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.collector.gitlab.convert import Converter
from app.collector.sync import Enqueuer
from app.config import Settings
from app.mcp import queries
from app.mcp.render import clean, inline
from app.models import AuditLog, Commit, Connection, Project, Review, ReviewFinding, User

MAX_STATUS_IDS = 20
QUOTA_WINDOW = timedelta(hours=24)
TRIGGER = "mcp"
QUOTA_ACTION = "mcp.review_quota"
STATUS_LABEL = {
    "queued": "Trong hàng đợi",
    "running": "Đang review",
    "done": "Đã xong",
    "failed": "Lỗi",
    "skipped": "Bỏ qua",
    "superseded": "Đã có round mới",
}
VERDICT_LABEL = {"approve": "Có thể merge", "fix_required": "Cần sửa", "block": "Chặn merge"}


def parse_shas(commits: list[str] | None, most: int) -> list[str]:
    """1..`most` distinct hashes (≥7 hex) or GitLab commit links, lower-cased, order kept."""
    out: list[str] = []
    for raw in commits or []:
        if not raw.strip():
            continue
        try:
            sha = parse_commit(raw)
        except ValueError as exc:
            raise queries.McpError("invalid_argument", str(exc)) from None
        if sha not in out:
            out.append(sha)
    if not out:
        raise queries.McpError(
            "invalid_argument", "cần ít nhất một commit (SHA ≥7 ký tự hoặc link commit GitLab)"
        )
    if len(out) > most:
        raise queries.McpError("invalid_argument", f"tối đa {most} commit mỗi lần gọi")
    return out


def quota(session: Session, settings: Settings, user_id: int, now: datetime) -> tuple[int, int]:
    """(limit, used): reviews this person had queued through MCP in the last 24 hours.

    Counted from append-only audit rows QUOTA_ACTION (`count` reserved before queueing, a
    negative `count` given back for the commits that were not queued), not from review rows:
    a failed review queued again keeps its old row (its trigger and creation time), which would
    let retries go uncounted."""
    limit = int(effective(session, settings)["mcp_reviews_per_day"])
    used = int(
        session.scalar(
            select(func.coalesce(func.sum(AuditLog.details["count"].as_integer()), 0)).where(
                AuditLog.action == QUOTA_ACTION,
                AuditLog.actor_user_id == user_id,
                AuditLog.at > now - QUOTA_WINDOW,
            )
        )
        or 0
    )
    return limit, max(0, used)


def fetch_pushed(
    client: GitLabClient, external_id: int, path: str, shas: list[str], deadline_s: float
) -> list[dict[str, Any]]:
    """Each commit as GitLab returns it; `commit_not_pushed` for the first it does not have,
    `invalid_argument` when GitLab read the string as a branch/tag name, `gitlab_unavailable`
    otherwise. Called with no DB transaction open."""
    out: list[dict[str, Any]] = []
    with client, client.deadline(deadline_s):
        for sha in shas:
            try:
                info = client.commit(external_id, sha)
            except GitLabError as exc:
                if exc.status_code == 404:
                    raise queries.McpError(
                        "commit_not_pushed",
                        f"commit {sha[:12]} chưa có trên GitLab ở repo {path}: chạy `git push` "
                        "rồi gọi lại",
                    ) from None
                raise queries.McpError(
                    "gitlab_unavailable",
                    f"không đọc được commit {sha[:12]} trên GitLab ({exc}): thử lại sau",
                ) from None
            got = str(info.get("id") or "").lower() if isinstance(info, dict) else ""
            if len(got) < 40 or not got.startswith(sha):
                raise queries.McpError(
                    "invalid_argument",
                    f"{sha[:40]} không phải mã commit ở repo {path} (GitLab hiểu nó là tên "
                    "nhánh/tag): truyền SHA của commit đã push",
                )
            out.append(info)
    return out


@dataclass
class Requested:
    project: Project
    outcome: QueueOutcome
    limit: int
    used: int


def check_request(
    session: Session, principal: Any, settings: Settings, repo: str, count: int, now: datetime
) -> tuple[Project, Connection]:
    """Every refusal the database can give before GitLab is asked."""
    if not principal.can("review.request"):
        raise queries.McpError(
            "forbidden", "tài khoản của bạn không có quyền 'Yêu cầu review commit/MR'"
        )
    project = queries.resolve_repo(session, principal, repo)
    if not project.tracked:
        raise queries.McpError(
            "repo_not_tracked", f"repo {project.path_with_namespace} chưa được CodePulse theo dõi"
        )
    if not project.review_enabled:
        raise queries.McpError(
            "review_disabled", f"repo {project.path_with_namespace} đang tắt review (Admin > Repo)"
        )
    connection = session.get(Connection, project.connection_id) if project.connection_id else None
    if connection is None:
        raise queries.McpError("repo_unknown", "repo chưa gắn kết nối GitLab")
    _within_quota(*quota(session, settings, principal.user_id, now), count)
    return project, connection


def _within_quota(limit: int, used: int, count: int) -> None:
    if used + count > limit:
        raise queries.McpError(
            "limit_reached",
            f"đã yêu cầu {used}/{limit} review qua MCP trong 24 giờ qua (Admin > Cấu hình "
            f"'mcp_reviews_per_day'); yêu cầu này cần thêm {count}",
        )


def reserve_quota(
    session: Session, settings: Settings, user_id: int, count: int, now: datetime
) -> tuple[int, int]:
    """The quota again with the caller's `users` row locked (`SELECT … FOR UPDATE`), then
    `count` reserved and committed at once: requests sent at the same moment see the reservation
    and cannot all slip past the limit, whatever the queueing commits later (a new round
    commits on its own). `refund_quota` gives back what was not queued."""
    session.execute(select(User.id).where(User.id == user_id).with_for_update())
    limit, used = quota(session, settings, user_id, now)
    _within_quota(limit, used, count)
    audit(session, user_id, QUOTA_ACTION, None, count=count)
    session.commit()
    return limit, used


def refund_quota(session: Session, user_id: int, reserved: int, queued: int) -> None:
    if reserved > queued:
        audit(session, user_id, QUOTA_ACTION, None, count=queued - reserved)


def queue_reviews(
    session: Session,
    principal: Any,
    settings: Settings,
    project: Project,
    raws: list[dict[str, Any]],
    *,
    again: bool,
    queue: Enqueuer,
) -> QueueOutcome:
    """The shared dashboard rules for each commit, trigger "mcp"."""
    converter = Converter(session, project, settings.ignore_paths)
    outcome = QueueOutcome()
    for raw in raws:
        queue_commit(
            session,
            principal,
            project,
            converter,
            raw,
            again=again,
            queue=queue,
            settings=settings,
            trigger=TRIGGER,
            outcome=outcome,
        )
    return outcome


# -- what the tools hand back ----------------------------------------------------------------------


def _counts(session: Session, review_ids: list[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    if not review_ids:
        return out
    rows = session.execute(
        select(ReviewFinding.review_id, ReviewFinding.severity, func.count())
        .where(ReviewFinding.review_id.in_(review_ids))
        .group_by(ReviewFinding.review_id, ReviewFinding.severity)
    ).all()
    for review_id, severity, n in rows:
        out.setdefault(int(review_id or 0), {})[str(severity)] = int(n)
    return out


def _open_severe(session: Session, review_ids: list[int]) -> dict[int, int]:
    if not review_ids:
        return {}
    rows = session.execute(
        select(ReviewFinding.review_id, func.count())
        .where(
            ReviewFinding.review_id.in_(review_ids),
            ReviewFinding.severity.in_(("critical", "high")),
            ReviewFinding.status == "open",
        )
        .group_by(ReviewFinding.review_id)
    ).all()
    return {int(r or 0): int(n) for r, n in rows}


def review_data(
    review: Review, counts: dict[str, int], open_severe: int, base: str
) -> dict[str, Any]:
    done = review.status == "done"
    return {
        "id": review.id,
        "commit_sha": review.commit_sha,
        "round": review.round,
        "status": review.status,
        "status_label": STATUS_LABEL.get(review.status, review.status),
        "finished": review.status in ("done", "failed", "skipped", "superseded"),
        "trigger": review.trigger,
        "requested_at": review.created_at.isoformat() if review.created_at else None,
        "started_at": review.started_at.isoformat() if review.started_at else None,
        "finished_at": review.finished_at.isoformat() if review.finished_at else None,
        "score": float(review.score) if done and review.score is not None else None,
        "verdict": review.verdict if done else None,
        "verdict_label": VERDICT_LABEL.get(review.verdict or "") if done else None,
        "findings": counts if done else None,
        "open_critical_high": open_severe if done else None,
        "error": clean(review.error) if review.status in ("failed", "skipped") else None,
        "link": f"{base}/reviews/{review.id}",
    }


def _line(item: dict[str, Any]) -> str:
    sha = (item.get("commit_sha") or "")[:12]
    head = f"- Review #{item['id']} · commit `{sha}` · **{item['status_label']}**"
    if item["status"] == "done":
        counts = item["findings"] or {}
        parts = [f"{counts.get(s, 0)} {s}" for s in ("critical", "high", "medium", "low")]
        score = f"điểm {item['score']:.0f}" if item["score"] is not None else "chưa có điểm"
        head += f" · {score} · {item['verdict_label'] or item['verdict'] or '—'} · " + ", ".join(
            parts
        )
        if item["open_critical_high"]:
            head += (
                f"\n  → {item['open_critical_high']} critical/high còn mở: gọi "
                f'get_commit_findings(sha="{sha}") để xem chi tiết và sửa'
            )
        else:
            head += "\n  → không còn critical/high nào phải sửa"
    elif item["status"] in ("failed", "skipped"):
        head += f" · lý do: {inline(item['error'] or '—')}"
    head += f"\n  {item['link']}"
    return head


def request_result(req: Requested, session: Session, base: str) -> tuple[str, dict[str, Any]]:
    outcome = req.outcome
    ids = sorted(set(outcome.review_ids.values()))
    rows = {r.id: r for r in session.scalars(select(Review).where(Review.id.in_(ids)))}
    queued: list[dict[str, Any]] = [
        {"sha": sha, "review_id": outcome.review_ids.get(sha), "again": sha in outcome.again}
        for sha in outcome.queued
    ]
    skipped: list[dict[str, Any]] = [
        {
            "sha": s["sha"],
            "reason": s["reason"],
            "review_id": outcome.review_ids.get(s["sha"]),
            "status": rows[outcome.review_ids[s["sha"]]].status
            if outcome.review_ids.get(s["sha"]) in rows
            else None,
        }
        for s in outcome.skipped
    ]
    for item in queued + skipped:
        item["link"] = f"{base}/reviews/{item['review_id']}" if item["review_id"] else None
    used = req.used + len(outcome.queued)
    lines = [
        f"# Yêu cầu review ở {inline(req.project.path_with_namespace)}",
        "",
        f"Đã xếp hàng {len(queued)} commit, bỏ qua {len(skipped)}. Đã dùng {used}/{req.limit} "
        "lượt review qua MCP trong 24 giờ.",
    ]
    for item in queued:
        again = " (round mới)" if item["again"] else ""
        lines.append(
            f"- `{item['sha'][:12]}`: trong hàng đợi{again} · review #{item['review_id']} "
            f"{item['link'] or ''}".rstrip()
        )
    for item in skipped:
        tail = f" · review #{item['review_id']} {item['link']}" if item["review_id"] else ""
        lines.append(f"- `{item['sha'][:12]}`: bỏ qua, {inline(item['reason'])}{tail}")
    waiting = [i["review_id"] for i in queued if i["review_id"]]
    if waiting:
        lines += [
            "",
            "Review chạy nền (thường 1-10 phút tuỳ độ lớn). Kết quả hiện trên trang CodePulse ở "
            "các link trên và người yêu cầu nhận thông báo trên web khi xong. Để xem tiến độ từ "
            f"đây: get_review_status(review_ids={waiting}) — gọi lại sau ~1 phút, đừng gọi dồn; "
            "khi trạng thái là Đã xong, gọi get_commit_findings(sha=…) để lấy finding cần sửa.",
        ]
    data = {
        "repo": req.project.path_with_namespace,
        "queued": queued,
        "skipped": skipped,
        "quota": {"limit": req.limit, "used": used},
        "next": {"tool": "get_review_status", "review_ids": waiting} if waiting else None,
    }
    return "\n".join(lines), data


def status_result(session: Session, reviews: list[Review], base: str) -> tuple[str, dict[str, Any]]:
    ids = [r.id for r in reviews]
    counts, severe = _counts(session, ids), _open_severe(session, ids)
    items = [review_data(r, counts.get(r.id, {}), severe.get(r.id, 0), base) for r in reviews]
    pending = [i["id"] for i in items if not i["finished"]]
    lines = ["# Trạng thái review", ""] + [_line(i) for i in items]
    if pending:
        lines += ["", f"Còn {len(pending)} review chưa xong: gọi lại sau ~1 phút."]
    return "\n".join(lines), {"reviews": items, "pending": pending}


def find_reviews(
    session: Session,
    principal: Any,
    review_ids: list[int] | None,
    commits: list[str] | None,
    repo: str | None,
) -> list[Review]:
    """The reviews asked about: by id, or the latest round of each commit (in `repo` when
    given). Each must be visible to the caller (the review page's own check)."""
    out: list[Review] = []
    ids = list(dict.fromkeys(review_ids or []))
    shas = parse_shas(commits, MAX_STATUS_IDS) if commits else []
    if not ids and not shas:
        raise queries.McpError("invalid_argument", "cần review_ids hoặc commits")
    if len(ids) + len(shas) > MAX_STATUS_IDS:
        raise queries.McpError("invalid_argument", f"tối đa {MAX_STATUS_IDS} review mỗi lần gọi")
    for review_id in ids:
        review = session.get(Review, review_id)
        if review is None:
            raise queries.McpError("not_found", f"không có review #{review_id}")
        queries._visible(session, principal, review)
        out.append(review)
    project = queries.resolve_repo(session, principal, repo) if repo and shas else None
    for sha in shas:
        query = (
            select(Review)
            .join(Commit, Commit.sha == Review.commit_sha)
            .where(Review.commit_sha.startswith(sha, autoescape=True))
        )
        if project is not None:
            query = query.where(Commit.project_id == project.id)
        elif (seen := principal.visible_projects()) is not None:
            # Repos the caller cannot see do not exist for them (docs/specs/09): a newer review
            # there must neither hide their own nor show that it exists.
            query = query.where(
                or_(
                    Commit.project_id.in_(
                        select(Project.id).where(Project.business_project_id.in_(seen))
                    ),
                    Review.author_user_id == principal.user_id,
                    Review.requested_by_user_id == principal.user_id,
                )
            )
        review = session.scalars(query.order_by(Review.round.desc(), Review.id.desc())).first()
        if review is None:
            raise queries.McpError(
                "not_found",
                f"commit {sha[:12]} chưa có review nào: gọi request_review(repo, commits) trước",
            )
        queries._visible(session, principal, review)
        if review.id not in {r.id for r in out}:
            out.append(review)
    return out


def now_utc() -> datetime:
    return datetime.now(UTC)
