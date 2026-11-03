"""Reading the domain layer into the plain facts the insights functions take (docs/specs/20).

The only module of app/insights that touches the database; everything else stays pure. Shared by
the Insights API and the evidence packs (app/evidence).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from app.insights import knowledge as kn
from app.insights.components import AutoMap, Resolver, auto_map
from app.insights.flow import BOT_MARKER, MrFacts, Note
from app.models import (
    Account,
    Commit,
    CommitFile,
    MergeRequest,
    MrCommit,
    MrNote,
    Project,
    RepoComponentMap,
    User,
)

CHUNK = 1000  # ids per IN list


def chunks(ids: Sequence[int]) -> Iterable[Sequence[int]]:
    for i in range(0, len(ids), CHUNK):
        yield ids[i : i + CHUNK]


def load_mrs(
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
    notes = load_notes(session, ids)
    first_at = first_commits(session, ids)
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
            first_commit_at=first_at.get(mr.id),
            notes=tuple(notes.get(mr.id, ())),
        )
        for mr, user_id in rows
    ]


def first_commits(session: Session, mr_ids: list[int]) -> dict[int, datetime]:
    """mr id -> its earliest non-merge commit's author date, for these MRs only."""
    out: dict[int, datetime] = {}
    for chunk in chunks(mr_ids):
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


def load_notes(session: Session, mr_ids: list[int]) -> dict[int, list[Note]]:
    """Notes that can be a response or a ready mark (§4.2.1). Other system notes (most of the
    rows) and CodePulse's own review comments are dropped in SQL; a comment's text is never
    read, only system notes need theirs."""
    out: dict[int, list[Note]] = defaultdict(list)
    body = func.lower(MrNote.body)
    system = MrNote.is_system.is_(True)
    for chunk in chunks(mr_ids):
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


# -- knowledge risk (docs/specs/20 §4.9) ------------------------------------------------------


def module_sql(depth: int) -> Any:
    """SQL twin of `knowledge.module_of`: the first `depth` directories, or ROOT_MODULE."""
    first_dirs = func.substring(CommitFile.path, f"^((?:[^/]+/){{1,{depth}}})")
    return func.coalesce(func.rtrim(first_dirs, "/"), kn.ROOT_MODULE)


def _in_window(repo_ids: list[int], lo: datetime, hi: datetime) -> Any:
    return and_(
        Commit.project_id.in_(repo_ids),
        Commit.is_merge.is_not(True),
        Commit.authored_at >= lo,
        Commit.authored_at < hi,
    )


def module_rows(
    session: Session, repo_ids: list[int], lo: datetime, hi: datetime, depth: int
) -> tuple[list[kn.ModuleTotalRow], list[kn.ModuleAuthorRow]]:
    """commit_files of non-merge commits authored in [lo, hi), summed per (repo, module,
    author) in one SQL pass; the per-module totals add those up (a commit has one author)."""
    if not repo_ids:
        return [], []
    module = module_sql(depth).label("module")
    # One person writing from two e-mails is one author: their rows are folded here.
    folded: dict[tuple[int, str, kn.Author], list[Any]] = {}
    for pid, mod, uid, aid, total, n, last in session.execute(
        select(
            Commit.project_id,
            module,
            Account.user_id,
            Commit.author_account_id,
            func.sum(CommitFile.additions + CommitFile.deletions),
            func.count(func.distinct(Commit.sha)),
            func.max(Commit.authored_at),
        )
        .select_from(CommitFile)
        .join(Commit, Commit.sha == CommitFile.sha)
        .outerjoin(Account, Account.id == Commit.author_account_id)
        .where(_in_window(repo_ids, lo, hi), Commit.author_account_id.is_not(None))
        .group_by(Commit.project_id, module, Account.user_id, Commit.author_account_id)
    ):
        author = kn.Author(uid, None) if uid is not None else kn.Author(None, aid)
        slot = folded.setdefault((int(pid), str(mod), author), [0, 0, None])
        slot[0] += int(total or 0)
        slot[1] += int(n)
        if last is not None and (slot[2] is None or last > slot[2]):
            slot[2] = last
    authors = [kn.ModuleAuthorRow(p, m, a, c, n) for (p, m, a), (c, n, _l) in folded.items()]
    sums: dict[tuple[int, str], list[Any]] = {}
    for (p, m, _a), (c, n, last) in folded.items():
        slot = sums.setdefault((p, m), [0, 0, None])
        slot[0] += c
        slot[1] += n
        if last is not None and (slot[2] is None or last > slot[2]):
            slot[2] = last
    totals = [kn.ModuleTotalRow(p, m, c, n, last) for (p, m), (c, n, last) in sums.items()]
    return totals, authors


def file_coverage(
    session: Session, repo_ids: list[int], lo: datetime, hi: datetime
) -> dict[int, tuple[int, int]]:
    """repo -> (non-merge commits in the window, how many of them have file rows)."""
    if not repo_ids:
        return {}
    has_files = exists().where(CommitFile.sha == Commit.sha)
    return {
        int(pid or 0): (int(n), int(with_files or 0))
        for pid, n, with_files in session.execute(
            select(
                Commit.project_id,
                func.count(),
                func.count().filter(has_files),
            )
            .where(_in_window(repo_ids, lo, hi))
            .group_by(Commit.project_id)
        )
    }


def last_commits(
    session: Session, authors: set[kn.Author], hi: datetime
) -> dict[kn.Author, datetime]:
    """Each author's latest commit in any repo before `hi` (§4.9 `orphaned`)."""
    out: dict[kn.Author, datetime] = {}
    users = sorted({a.user_id for a in authors if a.user_id is not None})
    accounts = sorted({a.account_id for a in authors if a.account_id is not None})
    for chunk in chunks(users):
        for uid, last in session.execute(
            select(Account.user_id, func.max(Commit.authored_at))
            .join(Commit, Commit.author_account_id == Account.id)
            .where(Account.user_id.in_(chunk), Commit.authored_at < hi)
            .group_by(Account.user_id)
        ):
            if uid is not None and last is not None:
                out[kn.Author(int(uid), None)] = last
    for chunk in chunks(accounts):
        for aid, last in session.execute(
            select(Commit.author_account_id, func.max(Commit.authored_at))
            .where(Commit.author_account_id.in_(chunk), Commit.authored_at < hi)
            .group_by(Commit.author_account_id)
        ):
            if aid is not None and last is not None:
                out[kn.Author(None, int(aid))] = last
    return out


# Paths read per repo to build its component map (the touched files, newest first).
MAX_MAP_PATHS = 100_000


def repo_paths(session: Session, repo_id: int) -> list[str]:
    """Every path the repo's commits touched (commit_files): what its component map is built
    from (§4.9b). Paths only, never source."""
    return [
        str(path)
        for path in session.scalars(
            select(CommitFile.path)
            .join(Commit, Commit.sha == CommitFile.sha)
            .where(Commit.project_id == repo_id)
            .group_by(CommitFile.path)
            .order_by(func.max(Commit.authored_at).desc())
            .limit(MAX_MAP_PATHS)
        )
    ]


# repo -> ((commits, latest commit), its automatic map): rebuilt only when the repo got new
# commits. Per process; a handful of maps is a few MB.
_AUTO_CACHE: dict[int, tuple[tuple[int, Any], AutoMap]] = {}
_AUTO_CACHE_SIZE = 64


def _auto_maps(session: Session, names: dict[int, str]) -> dict[int, AutoMap]:
    keys = {
        int(pid or 0): (int(n), last)
        for pid, n, last in session.execute(
            select(Commit.project_id, func.count(), func.max(Commit.authored_at))
            .where(Commit.project_id.in_(list(names)))
            .group_by(Commit.project_id)
        )
    }
    out: dict[int, AutoMap] = {}
    for pid, name in names.items():
        key = keys.get(pid, (0, None))
        cached = _AUTO_CACHE.get(pid)
        if cached is not None and cached[0] == key:
            out[pid] = cached[1]
            continue
        built = auto_map(repo_paths(session, pid), name)
        if len(_AUTO_CACHE) >= _AUTO_CACHE_SIZE:
            _AUTO_CACHE.pop(next(iter(_AUTO_CACHE)))
        _AUTO_CACHE[pid] = (key, built)
        out[pid] = built
    return out


def component_resolvers(session: Session, repo_ids: Sequence[int]) -> dict[int, Resolver]:
    """repo -> where each of its paths belongs (§4.9b): the automatic map (cached until the
    repo gets new commits), regrouped by the stored AI grouping when there is one."""
    if not repo_ids:
        return {}
    names = {
        int(pid): str(path).rsplit("/", 1)[-1]
        for pid, path in session.execute(
            select(Project.id, Project.path_with_namespace).where(Project.id.in_(repo_ids))
        )
    }
    grouped = {
        m.project_id: m.groups
        for m in session.scalars(
            select(RepoComponentMap).where(RepoComponentMap.project_id.in_(repo_ids))
        )
        if m.groups
    }
    autos = _auto_maps(session, {pid: names.get(pid, "repo") for pid in repo_ids})
    return {pid: Resolver(autos[pid], grouped.get(pid) or []) for pid in repo_ids}


def component_rows(
    session: Session,
    repo_ids: list[int],
    lo: datetime,
    hi: datetime,
    *,
    resolvers: dict[int, Resolver],
    half_life_days: int,
    bulk_files: int,
) -> tuple[list[kn.ModuleTotalRow], list[kn.ModuleAuthorRow]]:
    """§4.9b: each (commit, file) of non-merge commits in [lo, hi) weighs 0.5^(age/half-life),
    and a commit touching n > `bulk_files` files is worth bulk_files^2/n touches in all;
    summed per (repo, component, author). Lines are kept for display only, without bulk
    commits."""
    if not repo_ids:
        return [], []
    files_in_commit = func.count().over(partition_by=CommitFile.sha)
    total: dict[tuple[int, str], list[Any]] = {}
    per_author: dict[tuple[int, str, kn.Author], list[Any]] = {}
    placed: dict[tuple[int, str], Any] = {}
    seconds = half_life_days * 86400.0
    for pid, sha, uid, aid, path, lines, at, n_files in session.execute(
        select(
            Commit.project_id,
            Commit.sha,
            Account.user_id,
            Commit.author_account_id,
            CommitFile.path,
            CommitFile.additions + CommitFile.deletions,
            Commit.authored_at,
            files_in_commit,
        )
        .select_from(CommitFile)
        .join(Commit, Commit.sha == CommitFile.sha)
        .outerjoin(Account, Account.id == Commit.author_account_id)
        .where(_in_window(repo_ids, lo, hi), Commit.author_account_id.is_not(None))
    ):
        resolver = resolvers.get(int(pid or 0))
        if resolver is None:
            continue
        place = resolver.of(str(path))
        bulk = int(n_files) > bulk_files
        age = max(0.0, (hi - at).total_seconds()) if at is not None else 0.0
        # Past `bulk_files` files (import, rename, formatting) each file gets (bulk/n)^2: the
        # commit is worth bulk^2/n touches, continuous at the threshold and lighter the larger
        # it is (1000 files ~ 10 touches with the default 100).
        weight = 0.5 ** (age / seconds) * min(1.0, bulk_files / int(n_files)) ** 2
        churn = 0 if bulk else int(lines or 0)
        author = kn.Author(int(uid), None) if uid is not None else kn.Author(None, int(aid or 0))
        key = (int(pid or 0), place.id)
        placed[key] = place
        t = total.setdefault(key, [0.0, 0, set(), None])
        t[0] += weight
        t[1] += churn
        t[2].add(sha)
        if at is not None and (t[3] is None or at > t[3]):
            t[3] = at
        a = per_author.setdefault((key[0], key[1], author), [0.0, 0, set()])
        a[0] += weight
        a[1] += churn
        a[2].add(sha)
    totals = [
        kn.ModuleTotalRow(
            pid,
            cid,
            churn,
            len(shas),
            last,
            weight=w,
            name=placed[(pid, cid)].name,
            description=placed[(pid, cid)].description,
            kind=placed[(pid, cid)].kind,
            source=placed[(pid, cid)].source,
        )
        for (pid, cid), (w, churn, shas, last) in total.items()
    ]
    authors = [
        kn.ModuleAuthorRow(pid, cid, author, churn, len(shas), weight=w)
        for (pid, cid, author), (w, churn, shas) in per_author.items()
    ]
    return totals, authors


def knowledge_modules(
    session: Session,
    repo_ids: list[int],
    lo: datetime,
    hi: datetime,
    *,
    threshold: float,
    min_commits: int,
    half_life_days: int = 90,
    bulk_files: int = 100,
    resolvers: dict[int, Resolver] | None = None,
) -> list[kn.Module]:
    """§4.9/§4.9b for `repo_ids` over [lo, hi): every component with work, its bus factor and
    flags."""
    if resolvers is None:
        resolvers = component_resolvers(session, repo_ids)
    totals, author_rows = component_rows(
        session,
        repo_ids,
        lo,
        hi,
        resolvers=resolvers,
        half_life_days=half_life_days,
        bulk_files=bulk_files,
    )
    authors = {r.author for r in author_rows}
    inactive = {
        int(uid)
        for uid in session.scalars(
            select(User.id).where(
                User.id.in_({a.user_id for a in authors if a.user_id is not None}),
                User.active.is_(False),
            )
        )
    }
    return kn.modules(
        totals,
        author_rows,
        threshold=threshold,
        min_commits=min_commits,
        end=hi,
        last_commit=last_commits(session, authors, hi),
        inactive_users=inactive,
    )
