"""Team-level DPI reports (docs/specs/25 §8): R3 Quality & Debt, R4 Delivery. Aggregates of
the repos one sees, never per person."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dpi.service import next_month, previous_month
from app.evidence.snapshot import month_bounds
from app.insights.knowledge import module_of
from app.models import (
    Commit,
    CommitFile,
    Issue,
    JiraProject,
    MergeRequest,
    Pipeline,
    Project,
    Review,
    ReviewFinding,
)

SEVERITIES = ("critical", "high", "medium", "low")
WEIGHT = {"critical": 8, "high": 4, "medium": 2, "low": 1, "info": 0}
NOT_COUNTED = ("false_positive", "rejected")
HOTSPOT_DAYS = 90
TOP = 15
AGE_BUCKETS = ((7, "≤ 7 ngày"), (30, "8-30 ngày"), (90, "31-90 ngày"), (None, "> 90 ngày"))


def _repo_id() -> Any:
    """The repo of a review: its MR's, or its commit's."""
    mr_repo = select(MergeRequest.project_id).where(MergeRequest.id == Review.mr_id)
    commit_repo = select(Commit.project_id).where(Commit.sha == Review.commit_sha)
    return func.coalesce(mr_repo.scalar_subquery(), commit_repo.scalar_subquery())


def _coverage(session: Session, ids: list[int], lo: datetime, hi: datetime) -> dict[int, float]:
    """repo -> average coverage of the head pipelines of the MRs merged in [lo, hi)."""
    rows = session.execute(
        select(MergeRequest.project_id, func.avg(Pipeline.coverage))
        .join(Pipeline, Pipeline.sha == MergeRequest.head_sha)
        .where(
            MergeRequest.project_id.in_(ids),
            MergeRequest.state == "merged",
            MergeRequest.merged_at >= lo,
            MergeRequest.merged_at < hi,
            Pipeline.coverage.is_not(None),
        )
        .group_by(MergeRequest.project_id)
    ).all()
    return {int(p): float(v) for p, v in rows if p is not None and v is not None}


def quality(
    session: Session, repos: list[Project], month: date, settings: Settings
) -> dict[str, Any]:
    tz = ZoneInfo(settings.report_tz)
    lo, hi = month_bounds(month, tz)
    ids = [r.id for r in repos] or [0]
    names = {r.id: r.path_with_namespace for r in repos}
    repo_of = _repo_id()
    counted = ReviewFinding.status.is_(None) | ReviewFinding.status.not_in(NOT_COUNTED)

    # New this month (found by the month's reviews) vs older findings still open.
    new = Counter(
        {
            str(sev): int(n)
            for sev, n in session.execute(
                select(ReviewFinding.severity, func.count())
                .join(Review, Review.id == ReviewFinding.review_id)
                .where(
                    Review.status == "done",
                    Review.counted_at >= lo,
                    Review.counted_at < hi,
                    repo_of.in_(ids),
                    counted,
                )
                .group_by(ReviewFinding.severity)
            )
        }
    )
    old_open = Counter(
        {
            str(sev): int(n)
            for sev, n in session.execute(
                select(ReviewFinding.severity, func.count())
                .join(Review, Review.id == ReviewFinding.review_id)
                .where(
                    Review.status == "done",
                    Review.counted_at < lo,
                    repo_of.in_(ids),
                    ReviewFinding.status == "open",
                )
                .group_by(ReviewFinding.severity)
            )
        }
    )

    # Coverage on the month's MRs, against the month before.
    prev_lo, prev_hi = month_bounds(previous_month(month), tz)
    now_cov = _coverage(session, ids, lo, hi)
    before = _coverage(session, ids, prev_lo, prev_hi)
    coverage = [
        {
            "project": names.get(pid),
            "coverage": round(value, 1),
            "delta": round(value - before[pid], 1) if pid in before else None,
        }
        for pid, value in sorted(now_cov.items(), key=lambda kv: kv[1])
    ]

    # Hotspots: files changed often in the last 90 days x findings on them.
    since = hi - timedelta(days=HOTSPOT_DAYS)
    changes = {
        (int(pid), str(path)): int(n)
        for pid, path, n in session.execute(
            select(Commit.project_id, CommitFile.path, func.count())
            .join(Commit, Commit.sha == CommitFile.sha)
            .where(
                Commit.project_id.in_(ids),
                Commit.authored_at >= since,
                Commit.authored_at < hi,
                Commit.is_merge.is_not(True),
            )
            .group_by(Commit.project_id, CommitFile.path)
        )
        if pid is not None
    }
    found = {
        (int(pid), str(path)): int(n)
        for pid, path, n in session.execute(
            select(repo_of, ReviewFinding.file_path, func.count())
            .join(Review, Review.id == ReviewFinding.review_id)
            .where(
                Review.status == "done",
                Review.counted_at >= since,
                Review.counted_at < hi,
                repo_of.in_(ids),
                counted,
            )
            .group_by(repo_of, ReviewFinding.file_path)
        )
        if pid is not None and path
    }
    hotspots = sorted(
        (
            {
                "project": names.get(pid),
                "path": path,
                "changes": n,
                "findings": found[(pid, path)],
                "score": n * found[(pid, path)],
            }
            for (pid, path), n in changes.items()
            if found.get((pid, path))
        ),
        key=lambda h: -int(h["score"] or 0),
    )[:TOP]

    # Debt: open findings per module, weighted by severity.
    debt: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    for pid, path, sev in session.execute(
        select(repo_of, ReviewFinding.file_path, ReviewFinding.severity)
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(Review.status == "done", repo_of.in_(ids), ReviewFinding.status == "open")
    ):
        if pid is None:
            continue
        debt[(int(pid), module_of(path or "", settings.knowledge_module_depth))][str(sev)] += 1
    modules = sorted(
        (
            {
                "project": names.get(pid),
                "module": module,
                **{s: c.get(s, 0) for s in SEVERITIES},
                "weight": sum(WEIGHT.get(s, 0) * n for s, n in c.items()),
            }
            for (pid, module), c in debt.items()
        ),
        key=lambda d: -int(d["weight"] or 0),
    )[: TOP * 2]
    return {
        "repos": len(repos),
        "findings_new": {s: new.get(s, 0) for s in SEVERITIES},
        "findings_old_open": {s: old_open.get(s, 0) for s in SEVERITIES},
        "coverage": coverage,
        "hotspots": hotspots,
        "debt": modules,
    }


def delivery(
    session: Session,
    repos: list[Project],
    now: datetime,
    source: str = "gitlab",
    jira_projects: list[JiraProject] | None = None,
) -> dict[str, Any]:
    """`source`: the DPI task source (docs/specs/27 §9). Jira: the open issues are those of
    `jira_projects` (the ones one sees), each a row "Jira · KEY" next to the repos."""
    ids = [r.id for r in repos] or [0]
    names: dict[tuple[str, int], str] = {("repo", r.id): r.path_with_namespace for r in repos}
    today = now.date()
    wip = {
        ("repo", int(pid)): int(n)
        for pid, n in session.execute(
            select(MergeRequest.project_id, func.count())
            .where(MergeRequest.project_id.in_(ids), MergeRequest.state == "opened")
            .group_by(MergeRequest.project_id)
        )
        if pid is not None
    }
    if source == "jira":
        jira = jira_projects or []
        names |= {("jira", j.id): f"Jira · {j.key}" for j in jira}
        where = [
            Issue.source == "jira",
            Issue.jira_project_id.in_([j.id for j in jira] or [0]),
        ]
    else:
        where = [Issue.source == "gitlab", Issue.project_id.in_(ids)]
    open_issues = session.execute(
        select(
            Issue.source,
            func.coalesce(Issue.project_id, Issue.jira_project_id),
            Issue.created_at_src,
            Issue.labels,
            Issue.due_date,
        ).where(*where, Issue.state == "opened")
    ).all()
    aging = Counter[str]()
    blockers = Counter[str]()
    overdue = 0
    per_repo: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    for src, owner, created, labels, due in open_issues:
        pid = ("jira" if src == "jira" else "repo", int(owner))
        if created is not None:
            age = (now - created).days
            label = next(text for limit, text in AGE_BUCKETS if limit is None or age <= limit)
            aging[label] += 1
        blocked = False
        for name in labels or []:
            low = name.lower()
            if low == "blocked" or low.startswith("blocked::"):
                reason = name.split("::", 1)[1] if "::" in name else "không ghi lý do"
                blockers[reason] += 1
                blocked = True
        if blocked:  # an issue once per row, whatever its number of blocked labels
            per_repo[pid]["blocked"] += 1
        if due is not None and due < today:
            overdue += 1
            per_repo[pid]["overdue"] += 1
        per_repo[pid]["open"] += 1
    return {
        "as_of": now.isoformat(),
        "wip_mrs": sum(wip.values()),
        "open_issues": len(open_issues),
        "overdue_issues": overdue,
        "aging": [{"bucket": text, "issues": aging.get(text, 0)} for _l, text in AGE_BUCKETS],
        "blockers": [{"reason": r, "issues": n} for r, n in blockers.most_common()],
        "repos": sorted(
            (
                {
                    "project": names.get(pid),
                    "wip_mrs": wip.get(pid, 0),
                    "open_issues": per_repo[pid].get("open", 0),
                    "overdue": per_repo[pid].get("overdue", 0),
                    "blocked": per_repo[pid].get("blocked", 0),
                }
                for pid in set(wip) | set(per_repo)
            ),
            key=lambda r: -(r["wip_mrs"] + r["open_issues"]),
        ),
    }


__all__ = ["delivery", "next_month", "quality"]
