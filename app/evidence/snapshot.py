"""What the system gathers for one person's month (docs/specs/20 §6.2).

Lists, counts and links, never a score (D1). AI findings come in only once the independent check
confirmed them (spec 19 §4.3), from the latest finished review of each change (superseded rounds
are left out), and only as evidence (P7). Built live while the pack is a draft (GET shows a fresh
one), frozen when the person submits it.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.insights import INSIGHTS_VERSION
from app.insights.flow import STAGES, Flow, flow, size_bucket
from app.insights.load import load_mrs
from app.insights.stats import quantile
from app.models import (
    Account,
    Connection,
    FindingDispute,
    MergeRequest,
    MrNote,
    Project,
    Review,
    ReviewFinding,
)

MAX_MRS = 100
MAX_REVIEWS_GIVEN = 30
MAX_FINDINGS = 20
NOTABLE = 5
SEVERE = ("critical", "high")


def month_bounds(period_start: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[1st of the month, 1st of the next) as instants in `tz`."""
    nxt = date(period_start.year + period_start.month // 12, period_start.month % 12 + 1, 1)
    return datetime.combine(period_start, time.min, tz), datetime.combine(nxt, time.min, tz)


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def _mr_row(f: Flow, repos: dict[int, Project], urls: dict[int, str]) -> dict[str, Any]:
    repo = repos.get(f.mr.project_id)
    return {
        "id": f.mr.id,
        "iid": f.mr.iid,
        "title": f.mr.title,
        "project_id": f.mr.project_id,
        "path_with_namespace": repo.path_with_namespace if repo else None,
        "web_url": (
            f"{urls[f.mr.project_id]}/-/merge_requests/{f.mr.iid}"
            if f.mr.project_id in urls
            else None
        ),
        "merged_at": f.mr.merged_at.isoformat() if f.mr.merged_at else None,
        "size": f.mr.size,
        "size_bucket": size_bucket(f.mr.size),
        "review_rounds": f.mr.review_rounds,
        "reviewed": f.reviewed,
        **{s: _round(f.stage(s)) for s in STAGES},
    }


def month_flows(
    session: Session, period_start: date, tz: ZoneInfo, repo_ids: list[int] | None = None
) -> list[Flow]:
    """MRs merged in the month (in `repo_ids`, default every repo) as flows; one load serves
    every person of a batch (app/evidence/jobs.py)."""
    lo, hi = month_bounds(period_start, tz)
    if repo_ids is None:
        repo_ids = list(session.scalars(select(Project.id)))
    return [flow(m) for m in load_mrs(session, repo_ids, merged_between=(lo, hi))]


def worked_repos(session: Session, user_id: int, lo: datetime, hi: datetime) -> list[int]:
    """Repos with an MR merged in [lo, hi) that the person opened or wrote a note on: the only
    ones their pack needs (with every MR of those repos, for the repo's pickup p75)."""
    accounts = select(Account.id).where(Account.user_id == user_id)
    merged = and_(
        MergeRequest.state == "merged", MergeRequest.merged_at >= lo, MergeRequest.merged_at < hi
    )
    authored = select(MergeRequest.project_id).where(
        merged, MergeRequest.author_account_id.in_(accounts)
    )
    noted = (
        select(MergeRequest.project_id)
        .join(MrNote, MrNote.mr_id == MergeRequest.id)
        .where(merged, MrNote.author_account_id.in_(accounts))
    )
    return sorted({int(pid) for pid in session.scalars(authored.union(noted)) if pid is not None})


def active_people(flows: list[Flow]) -> set[int]:
    """People who merged an MR or responded to someone else's in the month."""
    people = {f.mr.author_user_id for f in flows if f.mr.author_user_id is not None}
    people |= {r.user_id for f in flows for r in f.responders if r.user_id is not None}
    return people


def build(
    session: Session,
    user_id: int,
    period_start: date,
    tz: ZoneInfo,
    stale_hours: int = 48,
    flows: list[Flow] | None = None,
) -> dict[str, Any]:
    lo, hi = month_bounds(period_start, tz)
    repos = {r.id: r for r in session.scalars(select(Project))}
    bases = {c.id: c.base_url.rstrip("/") for c in session.scalars(select(Connection))}
    urls = {
        r.id: f"{bases[r.connection_id]}/{r.path_with_namespace}"
        for r in repos.values()
        if r.connection_id in bases
    }
    # Every MR merged in the month: theirs, those they reviewed, and the repo's pickup p75.
    if flows is None:
        flows = month_flows(session, period_start, tz, worked_repos(session, user_id, lo, hi))
    mine = [f for f in flows if f.mr.author_user_id == user_id]
    reviewed = [
        f
        for f in flows
        if f.mr.author_user_id != user_id and any(r.user_id == user_id for r in f.responders)
    ]
    pickups: dict[int, list[float]] = defaultdict(list)
    for f in flows:
        if f.pickup_h is not None:
            pickups[f.mr.project_id].append(f.pickup_h)
    p75 = {pid: quantile(values, 0.75) for pid, values in pickups.items() if len(values) >= 4}

    rows = sorted(
        (_mr_row(f, repos, urls) for f in mine), key=lambda r: r["merged_at"] or "", reverse=True
    )
    notable: list[dict[str, Any]] = []

    def mark(f: Flow | None, why: str) -> None:
        if f is not None and len(notable) < NOTABLE and all(n["id"] != f.mr.id for n in notable):
            notable.append({"id": f.mr.id, "why": why})

    mark(max(mine, key=lambda f: f.mr.size or 0, default=None), "Lớn nhất trong tháng")
    mark(
        max(
            (f for f in mine if (f.mr.review_rounds or 0) > 1),
            key=lambda f: f.mr.review_rounds or 0,
            default=None,
        ),
        "Nhiều vòng review nhất",
    )
    waited = [
        f
        for f in mine
        if f.pickup_h is not None
        and (bar := p75.get(f.mr.project_id)) is not None
        and f.pickup_h > bar
    ]
    mark(max(waited, key=lambda f: f.pickup_h or 0, default=None), "Chờ review lâu hơn thường lệ")

    reviews_given = sorted(
        (_mr_row(f, repos, urls) for f in reviewed),
        key=lambda r: r["merged_at"] or "",
        reverse=True,
    )
    authors = {f.mr.author_user_id or -(f.mr.author_account_id or 0) for f in reviewed}

    # AI findings on their own work in the month, only the confirmed critical / high ones.
    found = session.execute(
        select(ReviewFinding, Review.id)
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(
            Review.author_user_id == user_id,
            Review.status == "done",
            Review.counted_at >= lo,
            Review.counted_at < hi,
            ReviewFinding.severity.in_(SEVERE),
            ReviewFinding.verified == "confirmed",
            func.coalesce(ReviewFinding.status, "open") != "false_positive",
        )
        .order_by(ReviewFinding.id.desc())
    ).all()
    disputes = session.execute(
        select(FindingDispute.decision).where(
            FindingDispute.raised_by == user_id,
            FindingDispute.created_at >= lo,
            FindingDispute.created_at < hi,
        )
    ).scalars()
    decisions = list(disputes)

    # Repos they worked in whose last sync is older than the month's data needs.
    needed = min(hi, datetime.now(UTC)) - timedelta(hours=stale_hours)
    active_repos = {f.mr.project_id for f in mine + reviewed}
    stale = sorted(
        repos[pid].path_with_namespace
        for pid in active_repos
        if (synced := repos[pid].last_synced_at) is None or synced < needed
    )
    return {
        "version": INSIGHTS_VERSION,
        "built_at": datetime.now(UTC).isoformat(),
        "period": period_start.strftime("%Y-%m"),
        "window": [lo.isoformat(), hi.isoformat()],
        "merged": {"count": len(rows), "items": rows[:MAX_MRS]},
        "notable": notable,
        "reviews_given": {
            "count": len(reviews_given),
            "authors": len(authors),
            "items": reviews_given[:MAX_REVIEWS_GIVEN],
        },
        "quality": {
            "confirmed_severe": len(found),
            "fixed": sum(1 for f, _r in found if f.status == "fixed"),
            "items": [
                {
                    "id": f.id,
                    "review_id": review_id,
                    "title": f.title,
                    "severity": f.severity,
                    "category": f.category,
                    "status": f.status,
                    "file_path": f.file_path,
                    "line_start": f.line_start,
                }
                for f, review_id in found[:MAX_FINDINGS]
            ],
            "disputes_raised": len(decisions),
            "disputes_accepted": sum(1 for d in decisions if d == "accepted"),
        },
        "waiting": [
            {
                "id": f.mr.id,
                "pickup": _round(f.pickup_h),
                "repo_p75": _round(p75.get(f.mr.project_id)),
            }
            for f in sorted(waited, key=lambda f: -(f.pickup_h or 0))
        ],
        "stale_repos": stale,
    }
