"""Knowledge risk: how many people a module depends on (docs/specs/20 §4.9).

The API aggregates `commit_files` in SQL per (repo, module, author); this module turns those rows
into a bus factor and the two flags. A module one person holds is a risk for the team, never
an achievement of that person (P8).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

ROOT_MODULE = "(root)"
# §4.9: the top owner counts as gone after this long without a commit anywhere.
ORPHAN_AFTER = timedelta(days=60)
TOP_OWNERS = 3


def module_of(path: str, depth: int) -> str:
    """The first `depth` directories of `path`; a file at the repo root is ROOT_MODULE."""
    dirs = path.split("/")[:-1]
    return "/".join(dirs[:depth]) if dirs else ROOT_MODULE


@dataclass(frozen=True)
class Author:
    """The person when mapped (user_id), else the git e-mail account: two unmapped e-mails are
    two people here, since one bucket would hide a single owner."""

    user_id: int | None
    account_id: int | None


@dataclass(frozen=True)
class ModuleAuthorRow:
    project_id: int
    module: str
    author: Author
    churn: int
    commits: int


@dataclass(frozen=True)
class ModuleTotalRow:
    project_id: int
    module: str
    churn: int
    commits: int
    last_change_at: datetime | None


@dataclass
class Module:
    project_id: int
    module: str
    churn: int
    commits: int
    last_change_at: datetime | None
    bus_factor: int
    owners: list[tuple[Author, float]] = field(default_factory=list)  # share %, largest first
    single_owner: bool = False
    orphaned: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "module": self.module,
            "churn": self.churn,
            "commits": self.commits,
            "last_change_at": self.last_change_at.isoformat() if self.last_change_at else None,
            "bus_factor": self.bus_factor,
            "top_share": self.owners[0][1] if self.owners else None,
            "single_owner": self.single_owner,
            "orphaned": self.orphaned,
        }


def bus_factor(churns: Iterable[int], threshold: float) -> int:
    """Fewest authors whose churn together reaches `threshold` of the total (0 when no churn)."""
    ordered = sorted((c for c in churns if c > 0), reverse=True)
    total = sum(ordered)
    if not total:
        return 0
    covered = 0
    for n, churn in enumerate(ordered, start=1):
        covered += churn
        if covered >= threshold * total:
            return n
    return len(ordered)  # pragma: no cover - threshold <= 1 always ends above


def modules(
    totals: Iterable[ModuleTotalRow],
    authors: Iterable[ModuleAuthorRow],
    *,
    threshold: float,
    min_commits: int,
    end: datetime,
    last_commit: Mapping[Author, datetime],
    inactive_users: set[int],
) -> list[Module]:
    """One `Module` per (repo, module) with churn, flags first, then the busiest.

    `last_commit`: each author's latest commit anywhere up to `end`; `inactive_users`: people
    switched off in CodePulse (users.active = false)."""
    by_module: dict[tuple[int, str], list[ModuleAuthorRow]] = defaultdict(list)
    for row in authors:
        by_module[(row.project_id, row.module)].append(row)
    out: list[Module] = []
    for total in totals:
        if total.churn <= 0:
            continue  # renames only: nothing to know
        rows = sorted(
            by_module.get((total.project_id, total.module), []),
            key=lambda r: (
                -r.churn,
                r.author.user_id is None,
                r.author.user_id or 0,
                r.author.account_id or 0,
            ),
        )
        m = Module(
            project_id=total.project_id,
            module=total.module,
            churn=total.churn,
            commits=total.commits,
            last_change_at=total.last_change_at,
            bus_factor=bus_factor((r.churn for r in rows), threshold),
            owners=[(r.author, round(100.0 * r.churn / total.churn, 1)) for r in rows[:TOP_OWNERS]],
        )
        # One person above the threshold; an even split (50/50) is two owners, not one.
        top_share = rows[0].churn / total.churn if rows else 0.0
        m.single_owner = top_share > threshold and m.commits >= min_commits
        if rows and m.commits >= min_commits:  # a module that small flags nothing
            top = rows[0].author
            seen = last_commit.get(top)
            gone = top.user_id is not None and top.user_id in inactive_users
            m.orphaned = gone or seen is None or end - seen > ORPHAN_AFTER
        out.append(m)
    out.sort(key=lambda m: (not (m.single_owner or m.orphaned), -m.churn, m.module))
    return out
