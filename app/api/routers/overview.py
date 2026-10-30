"""Per-dự-án dashboards (outside docs/specs/10, requested by the user 2026-10-24).

Aggregates over `reviews` grouped by the repo's dự án for one period (week or month, REPORT_TZ),
with the previous period for deltas and a short trend. These are anonymous aggregates like team
numbers (docs/specs/11): anyone granted the dự án may read them (admins: every dự án and the
repos outside one, docs/specs/09 "Phân quyền theo dự án"); per-person data stays scoped.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.routers.reviews import _target, scoped
from app.api.schemas import ReviewOut
from app.api.security import CurrentPrincipal
from app.config import Settings, get_settings
from app.models import (
    BusinessProject,
    Commit,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
)
from app.scoring.aggregate import (
    PeriodType,
    next_period,
    period_bounds,
    period_start_of,
    previous_period,
)
from app.scoring.verdict import VERDICTS

router = APIRouter(tags=["overview"])
SessionDep = Annotated[Session, Depends(get_session)]
Period = Literal["week", "month"]
SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERE = ("critical", "high")
TREND_PERIODS = 8
SERIES_PERIODS = 12
UNASSIGNED = "Chưa gán dự án"


@dataclass
class Bucket:
    reviews: int = 0
    score_sum: float = 0.0
    scored: int = 0
    loc: int = 0
    severity: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SEVERITIES, 0))
    verdicts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(VERDICTS, 0))

    @property
    def avg_score(self) -> float | None:
        return round(self.score_sum / self.scored, 1) if self.scored else None

    @property
    def pass_rate(self) -> float | None:
        judged = sum(self.verdicts.values())
        return round(100.0 * self.verdicts["approve"] / judged, 1) if judged else None

    @property
    def severe(self) -> int:
        return self.severity["critical"] + self.severity["high"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "reviews": self.reviews,
            "avg_score": self.avg_score,
            "loc": self.loc,
            **self.severity,
            **self.verdicts,
            "pass_rate": self.pass_rate,
        }


def _review_project() -> Any:
    """review_id -> project_id, for MR reviews and direct-commit reviews alike."""
    mr = select(Review.id.label("review_id"), MergeRequest.project_id.label("project_id")).join(
        MergeRequest, MergeRequest.id == Review.mr_id
    )
    commit = select(Review.id.label("review_id"), Commit.project_id.label("project_id")).join(
        Commit, Commit.sha == Review.commit_sha
    )
    return union_all(mr, commit).subquery("review_project")


Row = tuple[int, int | None, int, float | None, int | None, datetime | None, str | None]


def _done_reviews(
    session: Session,
    lo: datetime,
    hi: datetime,
    project_ids: list[int] | None = None,
    bp_ids: set[int] | None = None,
) -> list[Row]:
    """(project_id, business_project_id, review_id, score, loc, counted_at, verdict), [lo, hi)
    of Review.counted_at (when the change was made)."""
    rp = _review_project()
    query = (
        select(
            Project.id,
            Project.business_project_id,
            Review.id,
            Review.score,
            Review.loc_reviewed,
            Review.counted_at,
            Review.verdict,
        )
        .join(rp, rp.c.review_id == Review.id)
        .join(Project, Project.id == rp.c.project_id)
        .where(Review.status == "done", Review.counted_at >= lo, Review.counted_at < hi)
    )
    if project_ids is not None:
        query = query.where(Project.id.in_(project_ids))
    if bp_ids is not None:  # the viewer's dự án (repos outside one are left out too)
        query = query.where(Project.business_project_id.in_(bp_ids))
    return [
        (int(p), bp, int(r), float(s) if s is not None else None, loc, fin, v)
        for p, bp, r, s, loc, fin, v in session.execute(query).all()
    ]


def _severities(session: Session, review_ids: list[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = defaultdict(lambda: dict.fromkeys(SEVERITIES, 0))
    if not review_ids:
        return out
    rows = session.execute(
        select(ReviewFinding.review_id, ReviewFinding.severity, func.count())
        .where(ReviewFinding.review_id.in_(review_ids))
        .group_by(ReviewFinding.review_id, ReviewFinding.severity)
    ).all()
    for review_id, severity, n in rows:
        if review_id is not None and severity in out[int(review_id)]:
            out[int(review_id)][str(severity)] = int(n)
    return out


@dataclass
class Open:
    """Open findings of one severity group (any period): the backlog that matters now."""

    critical: int = 0
    high: int = 0

    @property
    def severe(self) -> int:
        return self.critical + self.high

    def add(self, other: "Open") -> None:
        self.critical += other.critical
        self.high += other.high

    def as_dict(self) -> dict[str, int]:
        return {"open_severe": self.severe, "open_critical": self.critical, "open_high": self.high}


def _open_severe(session: Session, bp_ids: set[int] | None = None) -> dict[int, Open]:
    """Open critical and high findings per project (any period), in the dự án `bp_ids`."""
    rp = _review_project()
    query = (
        select(Project.id, ReviewFinding.severity, func.count())
        .select_from(ReviewFinding)
        .join(Review, Review.id == ReviewFinding.review_id)
        .join(rp, rp.c.review_id == Review.id)
        .join(Project, Project.id == rp.c.project_id)
        .where(
            Review.status == "done",
            ReviewFinding.status == "open",
            ReviewFinding.severity.in_(SEVERE),
        )
        .group_by(Project.id, ReviewFinding.severity)
    )
    if bp_ids is not None:
        query = query.where(Project.business_project_id.in_(bp_ids))
    rows = session.execute(query).all()
    out: dict[int, Open] = defaultdict(Open)
    for p, severity, n in rows:
        setattr(out[int(p)], str(severity), int(n))
    return out


def _open_of(open_by_project: dict[int, Open], project_ids: Any) -> Open:
    total = Open()
    for project_id in project_ids:
        total.add(open_by_project.get(project_id, Open()))
    return total


def _last_reviewed(
    session: Session, hi: datetime, bp_ids: set[int] | None
) -> dict[int | None, datetime]:
    """dự án -> when its latest finished review counts (Review.counted_at), before `hi`: what a
    period without reviews points back to."""
    rp = _review_project()
    query = (
        select(Project.business_project_id, func.max(Review.counted_at))
        .join(rp, rp.c.review_id == Review.id)
        .join(Project, Project.id == rp.c.project_id)
        .where(Review.status == "done", Review.counted_at < hi)
        .group_by(Project.business_project_id)
    )
    if bp_ids is not None:
        query = query.where(Project.business_project_id.in_(bp_ids))
    return {bp: when for bp, when in session.execute(query).tuples() if when is not None}


def _bucket(rows: list[Row], sev: dict[int, dict[str, int]], key: Any) -> dict[Any, Bucket]:
    buckets: dict[Any, Bucket] = defaultdict(Bucket)
    for project_id, bp_id, review_id, score, loc, counted_at, verdict in rows:
        b = buckets[key(project_id, bp_id, counted_at)]
        b.reviews += 1
        if verdict in b.verdicts:
            b.verdicts[verdict] += 1
        if score is not None:
            b.score_sum += score
            b.scored += 1
        b.loc += loc or 0
        for s, n in sev.get(review_id, {}).items():
            b.severity[s] += n
    return buckets


def _delta(cur: Bucket, prev: Bucket) -> dict[str, float | None]:
    return {
        "reviews": cur.reviews - prev.reviews,
        "avg_score": (
            round(cur.avg_score - prev.avg_score, 1)
            if cur.avg_score is not None and prev.avg_score is not None
            else None
        ),
        "severe": cur.severe - prev.severe,
        "critical": cur.severity["critical"] - prev.severity["critical"],
        "high": cur.severity["high"] - prev.severity["high"],
    }


def _resolve_period(
    period: Period, start: date | None, settings: Settings
) -> tuple[date, ZoneInfo]:
    tz = ZoneInfo(settings.report_tz)
    today = datetime.now(UTC).astimezone(tz).date()
    period_start = period_start_of(start or today, period)
    if period_start > period_start_of(today, period):
        raise HTTPException(422, "start is in the future")
    return period_start, tz


def _trend_starts(period_start: date, period: PeriodType, n: int) -> list[date]:
    starts = [period_start]
    for _ in range(n - 1):
        starts.insert(0, previous_period(starts[0], period))
    return starts


@router.get("/metrics/projects")
def projects_overview(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    period: Period = "week",
    start: date | None = None,
) -> dict[str, Any]:
    """Every dự án (plus repos without one) for one period, with deltas and an 8-period trend."""
    period_start, tz = _resolve_period(period, start, settings)
    lo, hi = period_bounds(period_start, period, tz)
    prev_lo, prev_hi = period_bounds(previous_period(period_start, period), period, tz)
    trend_starts = _trend_starts(period_start, period, TREND_PERIODS)
    trend_lo = period_bounds(trend_starts[0], period, tz)[0]

    visible = principal.visible_projects()  # None: every dự án and the unassigned repos

    def shown(bp_id: int | None) -> bool:
        return visible is None or bp_id in visible

    rows = _done_reviews(session, trend_lo, hi, bp_ids=visible)
    sev = _severities(session, [r[2] for r in rows])
    by_bp = lambda p, bp, fin: bp  # noqa: E731 - small keyed aggregations below
    current = _bucket([r for r in rows if r[5] is not None and lo <= r[5] < hi], sev, by_bp)
    previous = _bucket(
        [r for r in rows if r[5] is not None and prev_lo <= r[5] < prev_hi], sev, by_bp
    )

    def bucket_period(fin: datetime) -> date:
        return period_start_of(fin.astimezone(tz).date(), period)

    trend = _bucket(
        [r for r in rows if r[5] is not None],
        sev,
        lambda p, bp, fin: (bp, bucket_period(fin)),
    )
    projects = [
        (project_id, bp_id)
        for project_id, bp_id in session.execute(
            select(Project.id, Project.business_project_id).where(Project.tracked)
        ).tuples()
        if shown(bp_id)
    ]
    all_repos = [
        (project_id, bp_id)
        for project_id, bp_id in session.execute(
            select(Project.id, Project.business_project_id)
        ).tuples()
        if shown(bp_id)
    ]
    open_by_project = _open_severe(session, visible)
    last_reviewed = _last_reviewed(session, hi, visible)
    repos_by_bp: dict[int | None, int] = defaultdict(int)
    for _project_id, bp_id in projects:
        repos_by_bp[bp_id] += 1
    # Open findings of every repo, tracked or not: the dự án's detail page and the findings
    # list its count links to include repos no longer tracked too.
    open_by_bp: dict[int | None, Open] = defaultdict(Open)
    for project_id, bp_id in all_repos:
        open_by_bp[bp_id].add(open_by_project.get(int(project_id), Open()))
    names = {
        bp.id: bp
        for bp in session.scalars(select(BusinessProject).order_by(BusinessProject.name))
        if shown(bp.id)
    }

    def entry(bp_id: int | None) -> dict[str, Any]:
        cur = current.get(bp_id, Bucket())
        prev = previous.get(bp_id, Bucket())
        bp = names.get(bp_id) if bp_id is not None else None
        return {
            "id": bp_id,
            "name": bp.name if bp else UNASSIGNED,
            "description": bp.description if bp else None,
            "repos": repos_by_bp.get(bp_id, 0),
            **cur.as_dict(),
            "severe": cur.severe,
            **open_by_bp.get(bp_id, Open()).as_dict(),
            "delta": _delta(cur, prev),
            "trend": [(trend.get((bp_id, s)) or Bucket()).avg_score for s in trend_starts],
            # Latest review up to this period's end: a period without any links back to it.
            "last_review_at": when.isoformat() if (when := last_reviewed.get(bp_id)) else None,
        }

    ids: list[int | None] = list(names)
    if repos_by_bp.get(None) or current.get(None):
        ids.append(None)
    total_cur = Bucket()
    total_prev = Bucket()
    for b in current.values():
        total_cur.reviews += b.reviews
        total_cur.score_sum += b.score_sum
        total_cur.scored += b.scored
        total_cur.loc += b.loc
        for s in SEVERITIES:
            total_cur.severity[s] += b.severity[s]
    for b in previous.values():
        total_prev.reviews += b.reviews
        total_prev.score_sum += b.score_sum
        total_prev.scored += b.scored
        for s in SEVERITIES:
            total_prev.severity[s] += b.severity[s]
    return {
        "period_type": period,
        "period_start": period_start.isoformat(),
        # The period's [from, to) instants: the dashboard links its "new" counts to the findings
        # list with them, so both count the same reviews.
        "period_range": [lo.isoformat(), hi.isoformat()],
        "trend_starts": [s.isoformat() for s in trend_starts],
        "totals": {
            **total_cur.as_dict(),
            "severe": total_cur.severe,
            **_open_of(open_by_project, list(open_by_project)).as_dict(),
            "projects": len(names),
            "repos": len(projects),
            "delta": _delta(total_cur, total_prev),
        },
        "projects": sorted(
            (entry(i) for i in ids),
            key=lambda e: (e["id"] is None, -(e["reviews"] or 0), str(e["name"])),
        ),
    }


@router.get("/metrics/projects/{bp_id}")
def project_detail(
    bp_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    period: Period = "week",
    start: date | None = None,
) -> dict[str, Any]:
    """One dự án: per-repo numbers, a 12-period series, finding categories, recent reviews."""
    bp = session.get(BusinessProject, bp_id)
    if bp is None or not principal.can_view_project(bp.id):
        raise HTTPException(404, "dự án not found")
    period_start, tz = _resolve_period(period, start, settings)
    lo, hi = period_bounds(period_start, period, tz)
    prev_lo, prev_hi = period_bounds(previous_period(period_start, period), period, tz)
    series_starts = _trend_starts(period_start, period, SERIES_PERIODS)
    series_lo = period_bounds(series_starts[0], period, tz)[0]

    repos = list(
        session.scalars(
            select(Project)
            .where(Project.business_project_id == bp.id)
            .order_by(Project.path_with_namespace)
        )
    )
    repo_ids = [p.id for p in repos]
    rows = _done_reviews(session, series_lo, hi, repo_ids)
    sev = _severities(session, [r[2] for r in rows])
    in_period = [r for r in rows if r[5] is not None and lo <= r[5] < hi]
    cur = _bucket(in_period, sev, lambda p, bp_, fin: "all").get("all", Bucket())
    prev = _bucket(
        [r for r in rows if r[5] is not None and prev_lo <= r[5] < prev_hi],
        sev,
        lambda p, bp_, fin: "all",
    ).get("all", Bucket())
    by_repo = _bucket(in_period, sev, lambda p, bp_, fin: p)
    by_period = _bucket(
        [r for r in rows if r[5] is not None],
        sev,
        lambda p, bp_, fin: period_start_of(fin.astimezone(tz).date(), period),
    )
    open_by_project = _open_severe(session)
    last_review: dict[int, datetime] = {}
    for project_id, _bp, _rid, _score, _loc, fin, _verdict in rows:
        if fin is not None and (project_id not in last_review or fin > last_review[project_id]):
            last_review[project_id] = fin

    review_ids = [r[2] for r in in_period]
    categories = (
        session.execute(
            select(ReviewFinding.category, func.count())
            .where(ReviewFinding.review_id.in_(review_ids))
            .group_by(ReviewFinding.category)
            .order_by(func.count().desc())
        ).all()
        if review_ids
        else []
    )

    # Recent reviews the viewer may see (developer: own; lead: team; admin: all).
    rp = _review_project()
    recent_query = scoped(
        select(Review)
        .join(rp, rp.c.review_id == Review.id)
        .where(rp.c.project_id.in_(repo_ids), Review.status.in_(("done", "failed", "skipped")))
        .order_by(Review.id.desc())
        .limit(10),
        principal,
    )
    recent = []
    for review in session.scalars(recent_query):
        project, url, label = _target(session, review)
        recent.append(
            {
                **ReviewOut.model_validate(review).model_dump(),
                "target": label,
                "project": project.path_with_namespace if project else None,
                "web_url": url,
            }
        )

    return {
        "project": {"id": bp.id, "name": bp.name, "description": bp.description},
        "period_type": period,
        "period_start": period_start.isoformat(),
        "period_range": [lo.isoformat(), hi.isoformat()],
        "totals": {
            **cur.as_dict(),
            "severe": cur.severe,
            **_open_of(open_by_project, repo_ids).as_dict(),
            "repos": len(repos),
            "delta": _delta(cur, prev),
        },
        "repos": [
            {
                "id": p.id,
                "path_with_namespace": p.path_with_namespace,
                "tracked": p.tracked,
                **(by_repo.get(p.id) or Bucket()).as_dict(),
                "severe": (by_repo.get(p.id) or Bucket()).severe,
                **open_by_project.get(p.id, Open()).as_dict(),
                "last_review_at": last_review.get(p.id),
            }
            for p in repos
        ],
        "series": [
            {
                "period_start": s.isoformat(),
                **(by_period.get(s) or Bucket()).as_dict(),
                "severe": (by_period.get(s) or Bucket()).severe,
            }
            for s in series_starts
        ],
        "categories": [{"category": str(c), "count": int(n)} for c, n in categories],
        "recent": recent,
        "next_period_start": next_period(period_start, period).isoformat(),
    }
