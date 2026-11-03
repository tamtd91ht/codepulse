"""DORA metrics of a set of repos (docs/specs/20 §4.10, decision D2). Team level only: nothing
here is ever attributed to a person (DORA's own guidance).

Production (D2): the repo's `production_environment` when an admin set one; else an environment
whose GitLab tier is `production`; else one named "production" or "prod".
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.insights.stats import share, spread

PRODUCTION_NAMES = frozenset({"production", "prod"})
FINISHED = frozenset({"success", "failed"})


def is_production(environment: str | None, tier: str | None, override: str | None) -> bool:
    if not environment:
        return False
    if override:
        return environment == override
    if tier:
        return tier == "production"
    return environment.lower() in PRODUCTION_NAMES


@dataclass(frozen=True)
class Dep:
    id: int
    project_id: int
    environment: str
    status: str
    sha: str | None
    finished_at: datetime | None


@dataclass(frozen=True)
class Shipped:
    """An MR and the first successful production deployment that carried it."""

    mr_id: int
    project_id: int
    first_commit_at: datetime | None
    merged_at: datetime | None
    deployed_at: datetime


def _hours(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds() / 3600.0)


@dataclass
class Events:
    """Failures and recoveries of one production environment, oldest first."""

    failed: list[Dep] = field(default_factory=list)
    rolled_back: list[Dep] = field(default_factory=list)  # changes a later deployment undid
    rollbacks: list[Dep] = field(default_factory=list)  # deployments that put an older sha back
    recoveries: list[tuple[datetime, float]] = field(default_factory=list)  # (recovered at, h)


def events(deployments: Iterable[Dep]) -> dict[tuple[int, str], Events]:
    """Per (repo, environment). A rollback is a successful deployment of a sha an earlier
    success already had, other than the sha deployed right before it; that previous deployment
    is then a failed change. A failure (failed deployment, or the change rolled back) is
    recovered by the next successful deployment. Known limit: shipping a rolled-back sha again
    later (A, B, A, B) counts the second B as a rollback of A."""
    by_env: dict[tuple[int, str], list[tuple[datetime, Dep]]] = defaultdict(list)
    for d in deployments:
        if d.status in FINISHED and d.finished_at is not None:
            by_env[(d.project_id, d.environment)].append((d.finished_at, d))
    out: dict[tuple[int, str], Events] = {}
    for key, timed in by_env.items():
        timed.sort(key=lambda pair: (pair[0], pair[1].id))
        ev = Events()
        seen: set[str] = set()
        last_success: tuple[datetime, Dep] | None = None
        failing_since: datetime | None = None
        for at, d in timed:
            if d.status == "failed":
                ev.failed.append(d)
                failing_since = failing_since or at
                continue
            if d.sha and d.sha in seen and last_success and last_success[1].sha != d.sha:
                ev.rollbacks.append(d)
                ev.rolled_back.append(last_success[1])
                failing_since = failing_since or last_success[0]
            if failing_since is not None:
                ev.recoveries.append((at, (at - failing_since).total_seconds() / 3600.0))
                failing_since = None
            if d.sha:
                seen.add(d.sha)
            last_success = (at, d)
        out[key] = ev
    return out


def summarise(
    deployments: Sequence[Dep],
    shipped: Sequence[Shipped],
    lo: datetime,
    hi: datetime,
    tz: ZoneInfo,
    min_sample: int,
    precomputed: dict[tuple[int, str], Events] | None = None,
) -> dict[str, Any]:
    """§4.10 over [lo, hi). `deployments`: the production deployments of the repos, with some
    history before `lo` (a rollback needs the earlier sha); counts take those finished inside.
    `precomputed`: `events(deployments)` already worked out (one pass serves many windows)."""

    def inside(when: datetime | None) -> bool:
        return when is not None and lo <= when < hi

    success = [d for d in deployments if d.status == "success" and inside(d.finished_at)]
    failed = [d for d in deployments if d.status == "failed" and inside(d.finished_at)]
    ev = list((precomputed if precomputed is not None else events(deployments)).values())
    rolled_back = [d for e in ev for d in e.rolled_back if inside(d.finished_at)]
    rollbacks = [d for e in ev for d in e.rollbacks if inside(d.finished_at)]
    recovery = [h for e in ev for at, h in e.recoveries if inside(at)]
    days = max(1.0, (hi - lo).total_seconds() / 86400.0)
    in_window = [s for s in shipped if inside(s.deployed_at)]
    days_with = {d.finished_at.astimezone(tz).date() for d in success if d.finished_at}
    return {
        "deployments": len(success),
        "failed": len(failed),
        "rollbacks": len(rollbacks),
        "per_week": round(len(success) * 7.0 / days, 2),
        "deploy_days": len(days_with),
        # Failed deployments and rolled-back changes over every finished deployment.
        "change_failure_rate": share(len(failed) + len(rolled_back), len(success) + len(failed)),
        "recovery_h": spread(recovery, min_sample),
        "lead_time_h": spread(
            (_hours(s.first_commit_at, s.deployed_at) for s in in_window), min_sample
        ),
        "deploy_h": spread((_hours(s.merged_at, s.deployed_at) for s in in_window), min_sample),
        "shipped_mrs": len(in_window),
    }


def first_shipped(
    links: Iterable[tuple[int, int, datetime | None]],
) -> dict[int, tuple[int, datetime]]:
    """mr id -> (deployment id, finished at) of the earliest successful production deployment
    carrying it, from (mr id, deployment id, finished at) rows."""
    out: dict[int, tuple[int, datetime]] = {}
    for mr_id, dep_id, at in links:
        if at is not None and (mr_id not in out or at < out[mr_id][1]):
            out[mr_id] = (dep_id, at)
    return out


# -- incidents (docs/specs/20 §5.3, D3) -------------------------------------------------------


@dataclass(frozen=True)
class IncidentFact:
    id: int
    project_id: int
    opened_at: datetime | None
    closed_at: datetime | None
    open_now: bool


def incident_summary(
    incidents: Sequence[IncidentFact],
    lo: datetime,
    hi: datetime,
    deployments: int,
    min_sample: int,
) -> dict[str, Any]:
    """Incidents opened in [lo, hi), per successful production deployment; time to restore of
    those closed in the window (opened -> closed); and how many are open now."""

    def inside(when: datetime | None) -> bool:
        return when is not None and lo <= when < hi

    opened = [i for i in incidents if inside(i.opened_at)]
    closed = [i for i in incidents if inside(i.closed_at)]
    return {
        "opened": len(opened),
        "closed": len(closed),
        "open_now": sum(1 for i in incidents if i.open_now),
        "per_deployment": round(len(opened) / deployments, 2) if deployments else None,
        "restore_h": spread((_hours(i.opened_at, i.closed_at) for i in closed), min_sample),
    }
