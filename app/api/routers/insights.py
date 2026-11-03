"""Engineering Insights API (docs/specs/20 §8-9): team-level flow, review load and pipelines.

Numbers are computed from the domain tables on every request, like /metrics/projects (§3.3).
They are aggregates of a dự án or repo: anyone granted the dự án reads them. A person's name
only shows to someone allowed to see that person's work there (`Principal.can_see_work`);
everyone else reads "a member" (§8, P1).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.api.access import visible_repo
from app.api.deps import get_session
from app.api.routers.overview import RANGE_MIN, OverviewPeriod, Window, _window
from app.api.security import CurrentPrincipal, Principal
from app.config import Settings, get_settings
from app.insights import INSIGHTS_VERSION
from app.insights import confidence as conf
from app.insights import pipelines as pipes
from app.insights.flow import (
    BOT_MARKER,
    STAGES,
    Flow,
    MrFacts,
    Note,
    Responder,
    Stage,
    flow,
    is_draft_title,
    size_bucket,
    summarise,
    waiting,
)
from app.insights.review_load import author_of, review_load
from app.models import (
    Account,
    BusinessProject,
    Commit,
    Connection,
    MergeRequest,
    MrCommit,
    MrNote,
    Pipeline,
    Project,
    User,
)
from app.scoring.aggregate import period_start_of

router = APIRouter(tags=["insights"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CHUNK = 1000  # ids per IN list
OLDEST_WAITING = 10
HIDDEN_NAME = "Một thành viên"
UNMAPPED_NAME = "Chưa gắn người"
MAX_RANGE_DAYS = 366


# -- scope -----------------------------------------------------------------------------------


def _repos(
    session: Session, principal: Principal, business_project_id: int | None, project_id: int | None
) -> list[Project]:
    """The tracked repos the request covers; a dự án or repo the viewer may not see is 404."""
    if project_id is not None:
        repo = visible_repo(session, principal, project_id)
        if business_project_id is not None and repo.business_project_id != business_project_id:
            raise HTTPException(404, "repo not found")
        return [repo]
    query = select(Project).where(Project.tracked).order_by(Project.path_with_namespace)
    if business_project_id is not None:
        bp = session.get(BusinessProject, business_project_id)
        if bp is None or not principal.can_view_project(bp.id):
            raise HTTPException(404, "dự án not found")
        query = query.where(Project.business_project_id == bp.id)
    elif (visible := principal.visible_projects()) is not None:
        query = query.where(Project.business_project_id.in_(visible))
    return list(session.scalars(query))


def _chunks(ids: Sequence[int]) -> Iterable[Sequence[int]]:
    for i in range(0, len(ids), CHUNK):
        yield ids[i : i + CHUNK]


# -- loading facts ---------------------------------------------------------------------------


def _load_mrs(
    session: Session,
    repo_ids: list[int],
    *,
    merged_between: tuple[datetime, datetime] | None = None,
    open_only: bool = False,
) -> list[MrFacts]:
    """Merged MRs in [lo, hi) of `merged_between`, or the open ones, with what §4.2 needs."""
    if not repo_ids:
        return []
    query = (
        select(MergeRequest, Account.user_id)
        .outerjoin(Account, Account.id == MergeRequest.author_account_id)
        .where(MergeRequest.project_id.in_(repo_ids))
    )
    if merged_between is not None:
        lo, hi = merged_between
        query = query.where(
            MergeRequest.state == "merged",
            MergeRequest.merged_at >= lo,
            MergeRequest.merged_at < hi,
        )
    if open_only:
        query = query.where(MergeRequest.state == "opened")
    rows = session.execute(query).all()
    ids = [mr.id for mr, _u in rows]
    notes = _load_notes(session, ids)
    first_commits = _first_commits(session, ids)
    return [
        MrFacts(
            id=mr.id,
            project_id=int(mr.project_id or 0),
            iid=mr.iid,
            title=mr.title,
            author_account_id=mr.author_account_id,
            author_user_id=user_id,
            state=mr.state,
            target_branch=mr.target_branch,
            opened_at=mr.created_at_src,
            merged_at=mr.merged_at,
            additions=mr.additions,
            deletions=mr.deletions,
            review_rounds=mr.review_rounds,
            first_commit_at=first_commits.get(mr.id),
            notes=tuple(notes.get(mr.id, ())),
        )
        for mr, user_id in rows
    ]


def _first_commits(session: Session, mr_ids: list[int]) -> dict[int, datetime]:
    """mr id -> its earliest non-merge commit's author date, for these MRs only."""
    out: dict[int, datetime] = {}
    for chunk in _chunks(mr_ids):
        rows = session.execute(
            select(MrCommit.mr_id, func.min(Commit.authored_at))
            .join(Commit, Commit.sha == MrCommit.sha)
            .where(MrCommit.mr_id.in_(chunk), Commit.is_merge.is_not(True))
            .group_by(MrCommit.mr_id)
        ).all()
        for mr_id, first_at in rows:
            if first_at is not None:
                out[int(mr_id)] = first_at
    return out


def _load_notes(session: Session, mr_ids: list[int]) -> dict[int, list[Note]]:
    """Notes that can be a response or a ready mark (§4.2.1). Other system notes (most of the
    rows) and CodePulse's own review comments are dropped in SQL; a comment's text is never
    read, only system notes need theirs."""
    out: dict[int, list[Note]] = defaultdict(list)
    body = func.lower(MrNote.body)
    system = MrNote.is_system.is_(True)
    for chunk in _chunks(mr_ids):
        rows = session.execute(
            select(
                MrNote.mr_id,
                MrNote.author_account_id,
                Account.user_id,
                case((system, MrNote.body), else_=literal("")),
                system,
                MrNote.created_at_src,
            )
            .outerjoin(Account, Account.id == MrNote.author_account_id)
            .where(
                MrNote.mr_id.in_(chunk),
                or_(
                    and_(
                        MrNote.is_system.is_not(True),
                        func.coalesce(MrNote.body, "").not_like(f"%{BOT_MARKER}%"),
                    ),
                    and_(
                        system,
                        or_(
                            body.contains("approved this merge request"),
                            body.contains("as **ready**"),
                            body.contains("as ready"),
                            body.contains("work in progress"),
                        ),
                    ),
                ),
            )
        ).all()
        for mr_id, account_id, user_id, text, is_system, created in rows:
            if mr_id is not None:
                out[int(mr_id)].append(
                    Note(account_id, user_id, text or "", bool(is_system), created)
                )
    return out


@dataclass
class Names:
    """Display names of people, and who may read them."""

    principal: Principal
    users: dict[int, str] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session, principal: Principal, keys: Iterable[Responder]) -> "Names":
        user_ids = sorted({k.user_id for k in keys if k.user_id is not None})
        names = cls(principal)
        for chunk in _chunks(user_ids):
            for uid, full, email in session.execute(
                select(User.id, User.full_name, User.primary_email).where(User.id.in_(chunk))
            ):
                names.users[int(uid)] = full or email or f"#{uid}"
        return names

    def visible(self, key: Responder, bp_ids: Iterable[int | None]) -> bool:
        """Every dự án the person's counted work lies in must let the viewer see their work."""
        return all(self.principal.can_see_work(key.user_id, bp) for bp in bp_ids)

    def person(self, key: Responder, bp_ids: Iterable[int | None]) -> dict[str, Any]:
        if not self.visible(key, bp_ids):
            return {"user_id": None, "name": HIDDEN_NAME, "hidden": True, "mapped": None}
        if key.user_id is not None:
            name = self.users.get(key.user_id, f"#{key.user_id}")
            return {"user_id": key.user_id, "name": name, "hidden": False, "mapped": True}
        return {"user_id": None, "name": UNMAPPED_NAME, "hidden": False, "mapped": False}


def _web_urls(session: Session, repos: list[Project]) -> dict[int, str]:
    """repo id -> its GitLab web root."""
    bases = {
        c.id: c.base_url.rstrip("/")
        for c in session.scalars(
            select(Connection).where(
                Connection.id.in_({r.connection_id for r in repos if r.connection_id})
            )
        )
    }
    return {
        r.id: f"{bases[r.connection_id]}/{r.path_with_namespace}"
        for r in repos
        if r.connection_id in bases
    }


def _confidence(
    repos: list[Project], merged: int, settings: Settings, now: datetime
) -> tuple[conf.Confidence, datetime | None]:
    c = conf.Confidence()
    conf.sample(c, merged, settings.insights_min_sample)
    as_of = conf.freshness(
        c,
        [(r.path_with_namespace, r.last_synced_at) for r in repos],
        now,
        settings.insights_stale_sync_hours,
    )
    return c, as_of


def _insights_window(
    period: OverviewPeriod, start: date | None, end: date | None, settings: Settings
) -> Window:
    """The overview's window, with a custom range capped at MAX_RANGE_DAYS (§3.3): every MR
    and note of the range (and the as-long range before it) is loaded per request."""
    days = (end - start).days + 1 if start is not None and end is not None else 0
    if period == "range" and days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"a range may span at most {MAX_RANGE_DAYS} days")
    return _window(period, start, end, settings)


def _window_info(w: Window) -> dict[str, Any]:
    return {
        "period_start": w.period_start.isoformat(),
        "period_end": w.period_end.isoformat(),
        "period_range": [w.lo.isoformat(), w.hi.isoformat()],
        "range_min": RANGE_MIN.isoformat(),
        "trend_period": w.trend_period,
        "trend_starts": [s.isoformat() for s in w.trend_starts],
    }


def _in(flows: list[Flow], lo: datetime, hi: datetime) -> list[Flow]:
    return [f for f in flows if f.mr.merged_at is not None and lo <= f.mr.merged_at < hi]


def _median(summary: dict[str, Any], stage: Stage) -> float | None:
    value: float | None = summary["stages"][stage]["median"]
    return value


def _diff(a: float | None, b: float | None) -> float | None:
    return round(a - b, 1) if a is not None and b is not None else None


# -- endpoints -------------------------------------------------------------------------------


@router.get("/insights/flow")
def insights_flow(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    business_project_id: int | None = None,
    project_id: int | None = None,
    period: OverviewPeriod = "week",
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """§4.3-4.6 and §4.8 for the repos in scope: totals with deltas, per repo, the trend, open
    MRs waiting for a first response, and pipelines."""
    w = _insights_window(period, start, end, settings)
    tz = ZoneInfo(settings.report_tz)
    now = datetime.now(UTC)
    min_sample = settings.insights_min_sample
    repos = _repos(session, principal, business_project_id, project_id)
    repo_ids = [r.id for r in repos]
    flows = [flow(m) for m in _load_mrs(session, repo_ids, merged_between=(w.load_lo, w.hi))]
    current = _in(flows, w.lo, w.hi)
    previous = _in(flows, w.prev_lo, w.prev_hi)
    totals = summarise(current, min_sample)
    before = summarise(previous, min_sample)

    trend_flows: dict[date, list[Flow]] = defaultdict(list)
    for f in _in(flows, w.trend_lo, w.trend_hi):
        if f.mr.merged_at is not None:  # always, by _in; narrows the type
            day = f.mr.merged_at.astimezone(tz).date()
            trend_flows[period_start_of(day, w.trend_period)].append(f)
    trend: list[dict[str, Any]] = []
    for s in w.trend_starts:
        bucket = summarise(trend_flows.get(s, []), min_sample)
        point: dict[str, Any] = {
            "period_start": s.isoformat(),
            "merged": bucket["merged"],
            "merged_without_review_rate": bucket["merged_without_review_rate"],
        }
        point.update({stage: _median(bucket, stage) for stage in STAGES})
        trend.append(point)

    by_repo: dict[int, list[Flow]] = defaultdict(list)
    for f in current:
        by_repo[f.mr.project_id].append(f)
    pipeline_rows = (
        session.execute(
            select(Pipeline.project_id, Pipeline.status, Pipeline.duration_sec, Pipeline.ref).where(
                Pipeline.project_id.in_(repo_ids),
                Pipeline.created_at_src >= w.lo,
                Pipeline.created_at_src < w.hi,
            )
        ).all()
        if repo_ids
        else []
    )
    default_branch = {r.id: r.default_branch for r in repos}
    facts_by_repo: dict[int, list[pipes.PipelineFact]] = defaultdict(list)
    for pid, status, duration, ref in pipeline_rows:
        if pid is None:
            continue
        facts_by_repo[int(pid)].append(
            pipes.PipelineFact(status, duration, ref is not None and ref == default_branch[pid])
        )

    urls = _web_urls(session, repos)
    bp_of = {r.id: r.business_project_id for r in repos}
    open_mrs = [
        m for m in _load_mrs(session, repo_ids, open_only=True) if not is_draft_title(m.title)
    ]
    waits = waiting(open_mrs, now)
    names = Names.load(
        session, principal, [author_of(flow(m)) for m, _age in waits[:OLDEST_WAITING]]
    )
    confidence, as_of = _confidence(repos, len(current), settings, now)
    return {
        "insights_version": INSIGHTS_VERSION,
        "period_type": period,
        **_window_info(w),
        "data_as_of": as_of.isoformat() if as_of else None,
        "confidence": confidence.as_dict(),
        "totals": {
            **totals,
            "delta": {
                "merged": totals["merged"] - before["merged"],
                "time_to_merge": _diff(
                    _median(totals, "time_to_merge"), _median(before, "time_to_merge")
                ),
                "pickup": _diff(_median(totals, "pickup"), _median(before, "pickup")),
                "merged_without_review_rate": _diff(
                    totals["merged_without_review_rate"], before["merged_without_review_rate"]
                ),
            },
        },
        "trend": trend,
        "repos": [
            {
                "id": r.id,
                "path_with_namespace": r.path_with_namespace,
                "business_project_id": r.business_project_id,
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
                **summarise(by_repo.get(r.id, []), min_sample),
                "pipelines": pipes.summarise(facts_by_repo.get(r.id, []), min_sample),
            }
            for r in repos
        ],
        "pipelines": pipes.summarise(
            [p for facts in facts_by_repo.values() for p in facts], min_sample
        ),
        "open": {
            "opened": len(open_mrs),
            "waiting_first_response": len(waits),
            "oldest": [
                {
                    "id": m.id,
                    "iid": m.iid,
                    "title": m.title,
                    "project_id": m.project_id,
                    "path_with_namespace": next(
                        (r.path_with_namespace for r in repos if r.id == m.project_id), None
                    ),
                    "web_url": (
                        f"{urls[m.project_id]}/-/merge_requests/{m.iid}"
                        if m.project_id in urls
                        else None
                    ),
                    "waiting_h": age,
                    "author": names.person(author_of(flow(m)), [bp_of.get(m.project_id)]),
                }
                for m, age in waits[:OLDEST_WAITING]
            ],
        },
    }


Only = Literal["unreviewed", "xl"]


@router.get("/insights/flow/mrs")
def insights_flow_mrs(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    business_project_id: int | None = None,
    project_id: int | None = None,
    period: OverviewPeriod = "week",
    start: date | None = None,
    end: date | None = None,
    stage: Stage = "time_to_merge",
    only: Only | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """The merged MRs behind /insights/flow (P3), slowest `stage` first (MRs without that
    stage last). `only`: MRs merged without review, or XL ones."""
    w = _insights_window(period, start, end, settings)
    repos = _repos(session, principal, business_project_id, project_id)
    repo_by_id = {r.id: r for r in repos}
    flows = [
        flow(m) for m in _load_mrs(session, [r.id for r in repos], merged_between=(w.lo, w.hi))
    ]
    if only == "unreviewed":
        flows = [f for f in flows if not f.reviewed]
    elif only == "xl":
        flows = [f for f in flows if size_bucket(f.mr.size) == "XL"]

    def key(f: Flow) -> tuple[bool, float, int]:
        value = f.stage(stage)
        return (value is None, -(value or 0.0), -f.mr.id)

    flows.sort(key=key)
    confidence, as_of = _confidence(repos, len(flows), settings, datetime.now(UTC))
    page = flows[offset : offset + limit]
    urls = _web_urls(session, repos)
    names = Names.load(session, principal, [author_of(f) for f in page])
    items: list[dict[str, Any]] = []
    for f in page:
        repo = repo_by_id[f.mr.project_id]
        stages: dict[str, float | None] = {s: _round(f.stage(s)) for s in STAGES}
        items.append(
            {
                "id": f.mr.id,
                "iid": f.mr.iid,
                "title": f.mr.title,
                "project_id": repo.id,
                "path_with_namespace": repo.path_with_namespace,
                "target_branch": f.mr.target_branch,
                "web_url": (
                    f"{urls[repo.id]}/-/merge_requests/{f.mr.iid}" if repo.id in urls else None
                ),
                "opened_at": f.mr.opened_at.isoformat() if f.mr.opened_at else None,
                "merged_at": f.mr.merged_at.isoformat() if f.mr.merged_at else None,
                "size": f.mr.size,
                "size_bucket": size_bucket(f.mr.size),
                "review_rounds": f.mr.review_rounds,
                "reviewed": f.reviewed,
                "reviewers": len(f.responders),
                **stages,
                "author": names.person(author_of(f), [repo.business_project_id]),
            }
        )
    return {
        "insights_version": INSIGHTS_VERSION,
        "period_type": period,
        **_window_info(w),
        "data_as_of": as_of.isoformat() if as_of else None,
        "confidence": confidence.as_dict(),
        "stage": stage,
        "only": only,
        "total": len(flows),
        "items": items,
    }


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


@router.get("/insights/review-load")
def insights_review_load(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    business_project_id: int | None = None,
    project_id: int | None = None,
    period: OverviewPeriod = "week",
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """§4.7: who responded to the MRs merged in the period, and to whose."""
    w = _insights_window(period, start, end, settings)
    now = datetime.now(UTC)
    repos = _repos(session, principal, business_project_id, project_id)
    bp_of = {r.id: r.business_project_id for r in repos}
    flows = [
        flow(m) for m in _load_mrs(session, [r.id for r in repos], merged_between=(w.lo, w.hi))
    ]
    load = review_load(flows)
    # The dự án each person's counted MRs lie in: their name shows only if all allow it.
    reviewer_bps: dict[Responder, set[int | None]] = defaultdict(set)
    author_bps: dict[Responder, set[int | None]] = defaultdict(set)
    for f in flows:
        bp = bp_of.get(f.mr.project_id)
        author_bps[author_of(f)].add(bp)
        for r in f.responders:
            reviewer_bps[r].add(bp)
    names = Names.load(session, principal, [*reviewer_bps, *author_bps])
    confidence, as_of = _confidence(repos, load.merged, settings, now)
    return {
        "insights_version": INSIGHTS_VERSION,
        "period_type": period,
        **_window_info(w),
        "data_as_of": as_of.isoformat() if as_of else None,
        "confidence": confidence.as_dict(),
        **load.as_dict(),
        "reviewers": [
            {
                **names.person(key, reviewer_bps[key]),
                "reviewed_mrs": n,
                "share": round(100.0 * n / load.merged, 1) if load.merged else None,
            }
            for key, n in load.reviewers
        ],
        "pairs": [
            {
                "reviewer": names.person(reviewer, reviewer_bps[reviewer]),
                "author": names.person(author, author_bps[author]),
                "mrs": n,
            }
            for reviewer, author, n in load.pairs
        ],
    }
