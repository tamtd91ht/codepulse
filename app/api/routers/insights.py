"""Engineering Insights API (docs/specs/20 §8-9): team-level flow, review load and pipelines.

Numbers are computed from the domain tables on every request, like /metrics/projects (§3.3).
They are aggregates of a dự án or repo: anyone granted the dự án reads them. A person's name
only shows to someone allowed to see that person's work there (`Principal.can_see_work`);
everyone else reads "a member" (§8, P1).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.access import visible_repo
from app.api.deps import get_session
from app.api.routers.overview import RANGE_MIN, OverviewPeriod, Window, _window
from app.api.security import Principal, require
from app.config import Settings, get_settings
from app.insights import INSIGHTS_VERSION, dora
from app.insights import confidence as conf
from app.insights import knowledge as kn
from app.insights import pipelines as pipes
from app.insights.flow import (
    STAGES,
    Flow,
    Responder,
    Stage,
    flow,
    is_draft_title,
    size_bucket,
    summarise,
    waiting,
)
from app.insights.load import (
    chunks,
    component_resolvers,
    file_coverage,
    first_commits,
    knowledge_tree,
    load_mrs,
)
from app.insights.review_load import author_of, review_load
from app.models import (
    BusinessProject,
    Connection,
    Deployment,
    Incident,
    MergeRequest,
    Pipeline,
    Project,
    RepoComponentMap,
    User,
)
from app.scoring.aggregate import period_bounds, period_start_of

router = APIRouter(tags=["insights"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# docs/specs/21: Luồng làm việc by permission (built-in: lead and above). Names inside still
# follow `can_see_work` and no per-person composite exists, whatever the sets say (§3, P1-P8).
InsightsViewer = Annotated[Principal, require("insights.view")]
OLDEST_WAITING = 10
HIDDEN_NAME = "Một thành viên"
UNMAPPED_NAME = "Chưa gắn người"
MAX_RANGE_DAYS = 366
# Below this share of a repo's commits with file rows, knowledge risk is "low" confidence.
FILE_COVERAGE_MIN = 0.8


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


# -- loading facts ---------------------------------------------------------------------------


@dataclass
class Names:
    """Display names of people, and who may read them."""

    principal: Principal
    users: dict[int, str] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session, principal: Principal, keys: Iterable[Responder]) -> "Names":
        user_ids = sorted({k.user_id for k in keys if k.user_id is not None})
        names = cls(principal)
        for chunk in chunks(user_ids):
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
    principal: InsightsViewer,
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
    flows = [flow(m) for m in load_mrs(session, repo_ids, merged_between=(w.load_lo, w.hi))]
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
            select(
                Pipeline.project_id,
                Pipeline.status,
                Pipeline.duration_sec,
                Pipeline.ref,
                Pipeline.created_at_src,
            ).where(
                Pipeline.project_id.in_(repo_ids),
                Pipeline.created_at_src >= w.prev_lo,
                Pipeline.created_at_src < w.hi,
            )
        ).all()
        if repo_ids
        else []
    )
    default_branch = {r.id: r.default_branch for r in repos}
    facts_by_repo: dict[int, list[pipes.PipelineFact]] = defaultdict(list)
    facts_before: list[pipes.PipelineFact] = []
    for pid, status, duration, ref, created in pipeline_rows:
        if pid is None or created is None:
            continue
        fact = pipes.PipelineFact(status, duration, ref is not None and ref == default_branch[pid])
        if w.lo <= created < w.hi:
            facts_by_repo[int(pid)].append(fact)
        elif w.prev_lo <= created < w.prev_hi:
            facts_before.append(fact)
    pipelines_now = pipes.summarise(
        [p for facts in facts_by_repo.values() for p in facts], min_sample
    )
    pipelines_before = pipes.summarise(facts_before, min_sample)

    urls = _web_urls(session, repos)
    bp_of = {r.id: r.business_project_id for r in repos}
    open_mrs = [
        m for m in load_mrs(session, repo_ids, open_only=True) if not is_draft_title(m.title)
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
                "xl_share": _diff(totals["size"]["xl_share"], before["size"]["xl_share"]),
                "pipeline_success_rate": _diff(
                    pipelines_now["success_rate"], pipelines_before["success_rate"]
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
        "pipelines": pipelines_now,
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
    principal: InsightsViewer,
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
    flows = [flow(m) for m in load_mrs(session, [r.id for r in repos], merged_between=(w.lo, w.hi))]
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
    principal: InsightsViewer,
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
    flows = [flow(m) for m in load_mrs(session, [r.id for r in repos], merged_between=(w.lo, w.hi))]
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


# The tree is cut here (parents come first): a dự án of many large repos stays readable.
MAX_TREE_NODES = 3000


def _cut_tree(tree: list[kn.Module]) -> list[kn.Module]:
    """The first MAX_TREE_NODES nodes (parents first), plus every warning and its ancestors:
    the list of warnings is never cut."""
    if len(tree) <= MAX_TREE_NODES:
        return tree
    by_id = {(m.project_id, m.module): m for m in tree}
    keep = {(m.project_id, m.module) for m in tree[:MAX_TREE_NODES]}
    for m in tree:
        if m.counted:
            node: kn.Module | None = m
            while node is not None and (node.project_id, node.module) not in keep:
                keep.add((node.project_id, node.module))
                node = by_id.get((node.project_id, node.parent)) if node.parent else None
    return [m for m in tree if (m.project_id, m.module) in keep]


@router.get("/insights/knowledge")
def insights_knowledge(
    principal: InsightsViewer,
    session: SessionDep,
    settings: SettingsDep,
    business_project_id: int | None = None,
    project_id: int | None = None,
    end: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    """§4.9: modules of the repos in scope over the KNOWLEDGE_WINDOW_DAYS up to `end` (default
    today), their bus factor and flags; owners named only to whoever may see their work."""
    tz = ZoneInfo(settings.report_tz)
    today = datetime.now(UTC).astimezone(tz).date()
    last_day = min(end or today, today)
    hi = datetime.combine(last_day + timedelta(days=1), time.min, tz)
    lo = hi - timedelta(days=settings.knowledge_window_days)
    repos = _repos(session, principal, business_project_id, project_id)
    repo_ids = [r.id for r in repos]
    resolvers = component_resolvers(session, repo_ids)
    tree = knowledge_tree(
        session,
        repo_ids,
        lo,
        hi,
        threshold=settings.knowledge_bus_threshold,
        min_commits=settings.knowledge_min_commits,
        half_life_days=settings.knowledge_half_life_days,
        bulk_files=settings.knowledge_bulk_files,
        resolvers=resolvers,
    )
    # §4.9b's flat list: the component level, warnings first.
    found = sorted(
        (m for m in tree if m.tier == "component"),
        key=lambda m: (not m.counted, not kn.flagged(m), m.kind == "docs", -m.commits, m.module),
    )
    coverage = file_coverage(session, repo_ids, lo, hi)
    shown = found[:limit]
    nodes = _cut_tree(tree)
    names = Names.load(
        session,
        principal,
        [Responder(a.user_id) for m in [*shown, *nodes] for a, _share in m.owners],
    )
    repo_by_id = {r.id: r for r in repos}

    def view(m: kn.Module) -> dict[str, Any]:
        repo = repo_by_id[m.project_id]
        return {
            **m.as_dict(),
            "path_with_namespace": repo.path_with_namespace,
            "owners": [
                {**names.person(Responder(a.user_id), [repo.business_project_id]), "share": pct}
                for a, pct in m.owners
            ],
        }

    c = conf.Confidence()
    as_of = conf.freshness(
        c,
        [(r.path_with_namespace, r.last_synced_at) for r in repos],
        datetime.now(UTC),
        settings.insights_stale_sync_hours,
    )
    # Repos with commits in the window but file rows for too few of them (synced before
    # commit_files existed, backfill not run): their bus factor covers less than the window.
    active = {pid: n for pid, (n, _with) in coverage.items() if n}
    partial = sorted(
        repo_by_id[pid].path_with_namespace
        for pid, (n, with_files) in coverage.items()
        if n and with_files < FILE_COVERAGE_MIN * n
    )
    if active and not found:
        c.add(
            "insufficient",
            "no_file_data",
            "Chưa có dữ liệu file cho các commit trong cửa sổ "
            "(admin chạy `codepulse insights backfill-files`)",
        )
    elif partial:
        c.add(
            "low",
            "partial_file_data",
            f"{len(partial)} repo thiếu dữ liệu file cho nhiều commit trong cửa sổ",
            repos=partial[:10],
        )
    return {
        "insights_version": INSIGHTS_VERSION,
        "window": [lo.isoformat(), hi.isoformat()],
        "window_days": settings.knowledge_window_days,
        "module_depth": settings.knowledge_module_depth,
        "half_life_days": settings.knowledge_half_life_days,
        "bulk_files": settings.knowledge_bulk_files,
        "maps": {
            str(pid): {
                "source": "llm" if r.groups else "auto",
                "components": sum(1 for c in r.auto.components.values() if c.kind != "noise"),
                "layered": bool(r.auto.layered_roots),
            }
            for pid, r in resolvers.items()
        },
        "bus_threshold": settings.knowledge_bus_threshold,
        "min_commits": settings.knowledge_min_commits,
        "data_as_of": as_of.isoformat() if as_of else None,
        "confidence": c.as_dict(),
        # §4.9c: warnings are counted once, at the highest level they hold.
        "totals": {
            "modules": len(found),
            "nodes": len(tree),
            "warnings": sum(1 for m in tree if m.counted),
            "single_owner": sum(1 for m in tree if m.counted and m.single_owner),
            "unattributed": sum(1 for m in tree if m.counted and m.unattributed),
            "orphaned": sum(1 for m in tree if m.counted and m.orphaned),
            "inherited": sum(1 for m in tree if m.inherited),
            "shown": len(shown),
        },
        "orphan_after_days": kn.ORPHAN_AFTER.days,
        "modules": [view(m) for m in shown],
        "nodes": [view(m) for m in nodes],
        "nodes_truncated": len(nodes) < len(tree),
    }


@router.get("/insights/components")
def insights_components(
    principal: InsightsViewer, session: SessionDep, project_id: int
) -> dict[str, Any]:
    """§4.9b: what a repo is made of, as knowledge risk measures it (the automatic map,
    regrouped by the AI grouping when there is one), and the state of that grouping."""
    from app.workers.component_jobs import running

    repos = _repos(session, principal, None, project_id)
    if not repos:
        raise HTTPException(404, "repo not found")
    repo = repos[0]
    resolver = component_resolvers(session, [repo.id])[repo.id]
    row = session.get(RepoComponentMap, repo.id)
    shown: dict[str, dict[str, Any]] = {}
    for comp in sorted(resolver.auto.components.values(), key=lambda c: -c.files):
        place = resolver.of(comp.samples[0]) if comp.samples else None
        if place is None:
            continue
        item = shown.setdefault(
            place.id,
            {
                "id": place.id,
                "name": place.name,
                "description": place.description,
                "kind": place.kind,
                "source": place.source,
                "files": 0,
                "tests": 0,
                "parts": [],
                "samples": [],
            },
        )
        item["files"] += comp.files
        item["tests"] += comp.tests
        if place.source == "llm":
            item["parts"].append(comp.name)
        item["samples"] += comp.samples[: max(0, 5 - len(item["samples"]))]
    paths = sum(c.files for c in resolver.auto.components.values())
    live = running(row)
    stuck = row is not None and row.status in ("queued", "running") and not live
    return {
        "project_id": repo.id,
        "path_with_namespace": repo.path_with_namespace,
        "source": "llm" if resolver.groups else "auto",
        "paths": paths,
        "layered_roots": resolver.auto.layered_roots,
        "unmapped_share": round(100 * resolver.unmapped_share(), 1),
        "ai": None
        if row is None
        else {
            "status": "failed" if stuck else row.status,
            "error": "Phân tích dừng giữa chừng; thử lại." if stuck else row.error,
            "built_at": row.built_at.isoformat() if row.built_at else None,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "confidence": row.confidence,
            "model": row.model if principal.is_admin else None,
            # Paths new since the grouping: past 20 % it no longer describes the repo well.
            "stale": bool(row.groups and row.paths_seen and paths > 1.2 * row.paths_seen),
        },
        "allow_external_llm": repo.allow_external_llm is not False,
        "can_rebuild": principal.is_admin and not live,
        "components": sorted(
            shown.values(), key=lambda c: (c["kind"] in ("noise", "docs"), -c["files"])
        ),
    }


# -- DORA (docs/specs/20 §4.10, task 9.8) -----------------------------------------------------

# History before the window that the rollback rule may look back on.
DORA_HISTORY = timedelta(days=90)
RECENT_FAILURES = 10


def _production(
    session: Session, repos: list[Project], lo: datetime, hi: datetime
) -> tuple[list[dora.Dep], dict[int, list[int]]]:
    """Finished production deployments (D2) of `repos` in [lo, hi), and the MR iids each
    successful one shipped. Production is filtered in SQL (override, tier, name)."""
    if not repos:
        return [], {}
    named = func.lower(Deployment.environment).in_(dora.PRODUCTION_NAMES)
    clauses = []
    plain = [r.id for r in repos if not r.production_environment]
    if plain:
        clauses.append(
            and_(
                Deployment.project_id.in_(plain),
                or_(
                    Deployment.environment_tier == "production",
                    and_(Deployment.environment_tier.is_(None), named),
                ),
            )
        )
    for r in repos:
        if r.production_environment:
            clauses.append(
                and_(
                    Deployment.project_id == r.id,
                    Deployment.environment == r.production_environment,
                )
            )
    rows = session.scalars(
        select(Deployment).where(
            or_(*clauses), Deployment.finished_at_src >= lo, Deployment.finished_at_src < hi
        )
    )
    deps: list[dora.Dep] = []
    iids: dict[int, list[int]] = {}
    for d in rows:
        deps.append(dora.Dep(d.id, d.project_id, d.environment, d.status, d.sha, d.finished_at_src))
        if d.status == "success" and d.mr_iids:
            iids[d.id] = [int(i) for i in d.mr_iids]
    return deps, iids


def _incidents(
    session: Session, repos: list[Project], since: datetime
) -> list[tuple[dora.IncidentFact, Incident]]:
    """Incidents of `repos` opened or closed from `since` on, or still open (docs/specs/20
    §5.3)."""
    if not repos:
        return []
    rows = session.scalars(
        select(Incident).where(
            Incident.project_id.in_([r.id for r in repos]),
            or_(
                Incident.created_at_src >= since,
                Incident.closed_at_src >= since,
                Incident.state == "opened",
            ),
        )
    )
    return [
        (
            dora.IncidentFact(
                i.id, i.project_id, i.created_at_src, i.closed_at_src, i.state == "opened"
            ),
            i,
        )
        for i in rows
    ]


def _shipped(
    session: Session, deps: list[dora.Dep], iids: dict[int, list[int]]
) -> list[dora.Shipped]:
    """Each MR the successful deployments in `deps` carried, at its first one of them. The
    iids resolve to MRs now, so an MR collected after its deployment still counts."""
    by_id = {d.id: d for d in deps}
    wanted: dict[int, set[int]] = defaultdict(set)  # repo -> iids
    for dep_id, numbers in iids.items():
        wanted[by_id[dep_id].project_id].update(numbers)
    mr_of: dict[tuple[int, int], int] = {}
    for repo_id, wanted_iids in wanted.items():
        for chunk in chunks(sorted(wanted_iids)):
            for mr_id, iid in session.execute(
                select(MergeRequest.id, MergeRequest.iid).where(
                    MergeRequest.project_id == repo_id, MergeRequest.iid.in_(chunk)
                )
            ):
                mr_of[(repo_id, int(iid))] = int(mr_id)
    links = [
        (mr_of[key], dep_id, by_id[dep_id].finished_at)
        for dep_id, numbers in iids.items()
        for n in numbers
        if (key := (by_id[dep_id].project_id, n)) in mr_of
    ]
    first = dora.first_shipped(links)
    if not first:
        return []
    mr_ids = list(first)
    merged: dict[int, tuple[int, datetime | None]] = {}
    for chunk in chunks(mr_ids):
        for mr_id, repo, merged_at in session.execute(
            select(MergeRequest.id, MergeRequest.project_id, MergeRequest.merged_at).where(
                MergeRequest.id.in_(chunk)
            )
        ):
            merged[int(mr_id)] = (int(repo or 0), merged_at)
    commits = first_commits(session, mr_ids)
    return [
        dora.Shipped(
            mr_id=mr_id,
            project_id=merged.get(mr_id, (0, None))[0],
            first_commit_at=commits.get(mr_id),
            merged_at=merged.get(mr_id, (0, None))[1],
            deployed_at=at,
        )
        for mr_id, (_dep, at) in first.items()
    ]


@router.get("/insights/dora")
def insights_dora(
    principal: InsightsViewer,
    session: SessionDep,
    settings: SettingsDep,
    business_project_id: int | None = None,
    project_id: int | None = None,
    period: OverviewPeriod = "week",
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """§4.10 for the repos in scope: deployment frequency, lead time for changes, change
    failure rate, recovery time; with deltas, a trend, per repo and the latest failures."""
    w = _insights_window(period, start, end, settings)
    tz = ZoneInfo(settings.report_tz)
    min_sample = settings.insights_min_sample
    repos = _repos(session, principal, business_project_id, project_id)
    deps, iids = _production(session, repos, w.load_lo - DORA_HISTORY, w.hi)
    shipped = _shipped(session, deps, iids)
    ev = dora.events(deps)  # once: every window below reads the same history
    environments: dict[int, list[str]] = defaultdict(list)
    if repos:
        for pid, env in session.execute(
            select(Deployment.project_id, Deployment.environment)
            .where(Deployment.project_id.in_([r.id for r in repos]))
            .distinct()
            .order_by(Deployment.environment)
        ):
            environments[int(pid)].append(str(env))

    def over(lo: datetime, hi: datetime) -> dict[str, Any]:
        return dora.summarise(deps, shipped, lo, hi, tz, min_sample, ev)

    totals = over(w.lo, w.hi)
    before = over(w.prev_lo, w.prev_hi)
    trend = []
    for s in w.trend_starts:
        lo, hi = period_bounds(s, w.trend_period, tz)
        point = over(lo, hi)
        trend.append(
            {
                "period_start": s.isoformat(),
                "deployments": point["deployments"],
                "change_failure_rate": point["change_failure_rate"],
                "lead_time_h": point["lead_time_h"]["median"],
            }
        )
    by_repo = []
    for r in repos:
        mine = [d for d in deps if d.project_id == r.id]
        summary = dora.summarise(
            mine,
            [x for x in shipped if x.project_id == r.id],
            w.lo,
            w.hi,
            tz,
            min_sample,
            {k: v for k, v in ev.items() if k[0] == r.id},
        )
        by_repo.append(
            {
                "id": r.id,
                "path_with_namespace": r.path_with_namespace,
                "production_environment": r.production_environment,
                # Every environment the repo deployed to, for the admin's choice (D2).
                "environments": environments.get(r.id, []),
                "production_detected": sorted({d.environment for d in mine}),
                **summary,
            }
        )
    rolled = {d.id for e in ev.values() for d in e.rolled_back}
    paths = {r.id: r.path_with_namespace for r in repos}
    failures = sorted(
        (
            d
            for d in deps
            if (d.status == "failed" or d.id in rolled)
            and d.finished_at is not None
            and w.lo <= d.finished_at < w.hi
        ),
        key=lambda d: d.finished_at or w.lo,
        reverse=True,
    )[:RECENT_FAILURES]
    now = datetime.now(UTC)
    c = conf.Confidence()
    as_of = conf.freshness(
        c,
        [(r.path_with_namespace, r.last_synced_at) for r in repos],
        now,
        settings.insights_stale_sync_hours,
    )
    incidents = _incidents(session, repos, w.prev_lo)
    facts = [i for i, _row in incidents]
    incident_now = dora.incident_summary(facts, w.lo, w.hi, totals["deployments"], min_sample)
    incident_before = dora.incident_summary(
        facts, w.prev_lo, w.prev_hi, before["deployments"], min_sample
    )
    recent_incidents = sorted(
        (row for fact, row in incidents if fact.opened_at and w.lo <= fact.opened_at < w.hi),
        key=lambda r: r.created_at_src or w.lo,
        reverse=True,
    )[:RECENT_FAILURES]
    if not deps:
        c.add(
            "insufficient",
            "no_deployments",
            "Chưa có deployment production nào (GitLab environment tier production, hoặc tên "
            "production/prod; admin đặt riêng cho repo nếu khác)",
        )
    return {
        "insights_version": INSIGHTS_VERSION,
        "period_type": period,
        **_window_info(w),
        "data_as_of": as_of.isoformat() if as_of else None,
        "confidence": c.as_dict(),
        "totals": {
            **totals,
            "delta": {
                "deployments": totals["deployments"] - before["deployments"],
                "change_failure_rate": _diff(
                    totals["change_failure_rate"], before["change_failure_rate"]
                ),
                "lead_time_h": _diff(
                    totals["lead_time_h"]["median"], before["lead_time_h"]["median"]
                ),
                "recovery_h": _diff(totals["recovery_h"]["median"], before["recovery_h"]["median"]),
            },
        },
        "incidents": {
            **incident_now,
            "delta": {
                "opened": incident_now["opened"] - incident_before["opened"],
                "restore_h": _diff(
                    incident_now["restore_h"]["median"], incident_before["restore_h"]["median"]
                ),
            },
            "recent": [
                {
                    "id": r.id,
                    "iid": r.iid,
                    "title": r.title,
                    "project_id": r.project_id,
                    "path_with_namespace": paths.get(r.project_id),
                    "web_url": r.web_url if r.web_url and r.web_url.startswith("http") else None,
                    "state": r.state,
                    "opened_at": r.created_at_src.isoformat() if r.created_at_src else None,
                    "closed_at": r.closed_at_src.isoformat() if r.closed_at_src else None,
                    "fix_mrs": len(r.fix_mr_iids or []),
                }
                for r in recent_incidents
            ],
        },
        "trend": trend,
        "repos": by_repo,
        "failures": [
            {
                "id": d.id,
                "project_id": d.project_id,
                "path_with_namespace": paths.get(d.project_id),
                "environment": d.environment,
                "sha": d.sha,
                "kind": "failed" if d.status == "failed" else "rolled_back",
                "finished_at": d.finished_at.isoformat() if d.finished_at else None,
            }
            for d in failures
        ],
    }
