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
    # §4.9b: decayed, bulk-damped touches; the share basis when set (else churn, bản 1).
    weight: float = 0.0


@dataclass(frozen=True)
class ModuleTotalRow:
    project_id: int
    module: str
    churn: int
    commits: int
    last_change_at: datetime | None
    weight: float = 0.0
    # §4.9b: the component as shown (name, what it is, its kind, auto or AI-grouped).
    name: str | None = None
    description: str = ""
    kind: str = "code"
    source: str = "auto"
    # §4.9c: the tree (level 0 = repo), the parent node id and the tier.
    level: int = 0
    parent: str | None = None
    tier: str = "component"


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
    # The top holder is a git account not linked to a person: link it before concluding.
    unattributed: bool = False
    name: str | None = None
    description: str = ""
    kind: str = "code"
    source: str = "auto"
    unlinked_share: float = 0.0  # % held by accounts not linked to a person
    # §4.9c, set by `fold_tree`.
    level: int = 0
    parent: str | None = None
    tier: str = "component"
    weight: float = 0.0
    contributors: int = 0
    share_of_parent: float | None = None  # % of the parent's touches
    inherited: bool = False  # the parent is flagged with the same top holder
    counted: bool = False  # one of the warnings the team should act on
    why: str | None = None  # why it is counted, or why not

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "module": self.module,
            "name": self.name or self.module,
            "description": self.description,
            "kind": self.kind,
            "source": self.source,
            "unattributed": self.unattributed,
            "unlinked_share": round(self.unlinked_share, 1),
            "level": self.level,
            "parent": self.parent,
            "tier": self.tier,
            "contributors": self.contributors,
            "share_of_parent": None
            if self.share_of_parent is None
            else round(self.share_of_parent, 1),
            "inherited": self.inherited,
            "counted": self.counted,
            "why": self.why,
            "churn": self.churn,
            "commits": self.commits,
            "last_change_at": self.last_change_at.isoformat() if self.last_change_at else None,
            "bus_factor": self.bus_factor,
            "top_share": self.owners[0][1] if self.owners else None,
            "single_owner": self.single_owner,
            "orphaned": self.orphaned,
        }


def bus_factor(churns: Iterable[float], threshold: float) -> int:
    """Fewest authors whose churn together reaches `threshold` of the total (0 when no churn)."""
    ordered = sorted((c for c in churns if c > 0), reverse=True)
    total = sum(ordered)
    if not total:
        return 0
    covered = 0.0
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
        if total.kind == "noise":
            continue  # generated, vendored, lockfiles: no one needs to hold them
        weighted = total.weight > 0

        def basis(r: ModuleAuthorRow | ModuleTotalRow, weighted: bool = weighted) -> float:
            return r.weight if weighted else float(r.churn)

        whole = basis(total)
        if whole <= 0:
            continue  # renames only: nothing to know
        rows = sorted(
            by_module.get((total.project_id, total.module), []),
            key=lambda r: (
                -basis(r),
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
            bus_factor=bus_factor((basis(r) for r in rows), threshold),
            owners=[(r.author, round(100.0 * basis(r) / whole, 1)) for r in rows[:TOP_OWNERS]],
            name=total.name,
            description=total.description,
            kind=total.kind,
            source=total.source,
            unlinked_share=100.0 * sum(basis(r) for r in rows if r.author.user_id is None) / whole,
            level=total.level,
            parent=total.parent,
            tier=total.tier,
            weight=whole,
            contributors=len(rows),
        )
        if total.kind == "docs":
            out.append(m)  # shown, never flagged
            continue
        # One person above the threshold; an even split (50/50) is two owners, not one.
        top_share = basis(rows[0]) / whole if rows else 0.0
        m.single_owner = top_share > threshold and m.commits >= min_commits
        if m.single_owner and rows[0].author.user_id is None:
            m.single_owner, m.unattributed = False, True
        if rows and m.commits >= min_commits:  # a module that small flags nothing
            top = rows[0].author
            seen = last_commit.get(top)
            gone = top.user_id is not None and top.user_id in inactive_users
            m.orphaned = gone or seen is None or end - seen > ORPHAN_AFTER
        out.append(m)
    out.sort(
        key=lambda m: (
            not (m.single_owner or m.orphaned or m.unattributed),
            m.kind == "docs",
            -m.commits,
            m.module,
        )
    )
    return out


# §4.9c: below this share of its parent's touches a node is too small to be a warning.
MIN_SHARE_OF_PARENT = 0.05
# A repo with this few contributors in the window depends on them everywhere: warn once.
SMALL_TEAM = 2


def flagged(m: Module) -> bool:
    return m.single_owner or m.orphaned or m.unattributed


def fold_tree(nodes: list[Module]) -> list[Module]:
    """§4.9c: which flagged nodes are warnings. A warning sits at the highest level it holds:
    a child flagged for the same top holder as its flagged parent only inherits it; a child
    held by someone else is a pocket of knowledge of its own. Sub-packages are shown, never
    counted; a node under 5 % of its parent's touches is too small; in a repo of one or two
    contributors only the repo itself is a warning. Returns the nodes, parents first."""
    by_id = {(m.project_id, m.module): m for m in nodes}
    ordered = sorted(nodes, key=lambda m: m.level)
    small_team: set[int] = {
        m.project_id for m in ordered if m.level == 0 and m.contributors <= SMALL_TEAM
    }
    # node -> its nearest flagged ancestor (parents come first, so theirs is known).
    above: dict[tuple[int, str], Module | None] = {}
    for m in ordered:
        parent = by_id.get((m.project_id, m.parent)) if m.parent else None
        if parent is not None and parent.weight > 0:
            m.share_of_parent = 100.0 * m.weight / parent.weight
        nearest = None
        if parent is not None:
            nearest = parent if flagged(parent) else above.get((parent.project_id, parent.module))
        above[(m.project_id, m.module)] = nearest
        if not flagged(m):
            continue
        top = m.owners[0][0] if m.owners else None
        same = nearest is not None and bool(nearest.owners) and nearest.owners[0][0] == top
        if m.tier == "sub":
            m.why = "package con: chỉ để tham khảo"
        elif same:
            m.inherited = True
            m.why = "cùng người nắm với cấp trên"
        elif m.level > 0 and m.project_id in small_team:
            m.why = f"repo chỉ có ≤ {SMALL_TEAM} người đóng góp: chỉ cảnh báo ở cấp repo"
        elif m.share_of_parent is not None and m.share_of_parent < 100 * MIN_SHARE_OF_PARENT:
            m.why = "quá nhỏ so với cấp trên"
        else:
            m.counted = True
            if m.level == 0:
                m.why = "cả repo phụ thuộc một người"
            elif nearest is not None:
                m.why = "người nắm khác cấp trên"
            elif m.tier == "module":
                m.why = "cả module phụ thuộc một người"
    return ordered
