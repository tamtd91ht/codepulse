"""One person's month, read from the database for the DPI metrics (docs/specs/25 §2, §4).

Only counts, ids, times and labels are read; the one text read is a review comment's body, to
label it when the monthly classification has not labelled it yet (§4.5).
"""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.dpi.labels import blocked
from app.dpi.metrics import (
    CommentFact,
    DefectFact,
    Facts,
    IssueFact,
    ItemFact,
    MrFact,
)
from app.evidence.snapshot import month_bounds
from app.insights.knowledge import module_of
from app.models import (
    Account,
    Commit,
    CommitFile,
    Connection,
    CriteriaSet,
    EvidenceItem,
    EvidencePack,
    Issue,
    IssueMrLink,
    MergeRequest,
    MrCommit,
    MrNote,
    Pipeline,
    ProdDefect,
    Project,
    ProjectCriteriaSet,
    Review,
    ReviewComment,
    ReviewCriteriaScore,
    ReviewFinding,
)
from app.scoring.formula import (
    MIN_CONFIDENCE_UNVERIFIED,
    ScoredFinding,
    counts_for_score,
    rule_for,
)

REWORK_DAYS = 21
BASELINE_DAYS = 90
FEEDBACK_CAP_H = 72.0  # one review comment's handling counts at most this long
PUSH = re.compile(r"^added \d+ (new )?commits?\b")
DEFAULT_BOT_PATTERN = r"(^|[-_.])bot([-_.@]|$)|renovate|dependabot|codepulse|gitlab-ci"
RULE_WORDS = re.compile(
    r"\b(bug|lỗi|null|nil|race|deadlock|leak|test|security|bảo mật|inject|sql|xss|validate|"
    r"exception|panic|timeout|retry|transaction|nên|should|must|thiếu|missing|wrong|sai)\b",
    re.IGNORECASE,
)


def bot_accounts(session: Session, pattern: str | None) -> set[int]:
    """Accounts that look like bots (§4.3): never counted."""
    regex = re.compile(pattern or DEFAULT_BOT_PATTERN, re.IGNORECASE)
    return {
        int(aid)
        for aid, key, name in session.execute(
            select(Account.id, Account.external_key, Account.display_name)
        )
        if regex.search(key or "") or regex.search(name or "")
    }


def classify_rule(body: str | None) -> str:
    """§4.5 fallback label of a review comment, when no model labelled it."""
    text = (body or "").strip()
    if len(text) >= 40 and RULE_WORDS.search(text):
        return "actionable"
    return "other"


def web_root(session: Session, project: Project | None) -> str | None:
    """The repo's GitLab web root (connection base URL + path)."""
    if project is None or project.connection_id is None:
        return None
    base = session.scalar(select(Connection.base_url).where(Connection.id == project.connection_id))
    return f"{base.rstrip('/')}/{project.path_with_namespace}" if base else None


def author_hours(
    opened: datetime | None,
    first_commit: datetime | None,
    notes: list[tuple[datetime, bool, bool]],
) -> float | None:
    """§2 C1: the author's own time on an MR: writing (first commit -> opened) plus handling
    each review comment (comment -> the author's next push), each at most FEEDBACK_CAP_H.
    `notes`: (time, is_push, is_review_comment) in time order."""
    if opened is None:
        return None
    total = 0.0
    if first_commit is not None:
        total += max(0.0, (opened - first_commit).total_seconds() / 3600)
    pending: list[datetime] = []
    for at, is_push, is_comment in notes:
        if is_comment:
            pending.append(at)
        elif is_push and pending:
            total += min(FEEDBACK_CAP_H, (at - pending[0]).total_seconds() / 3600)
            pending = []
    return round(total, 2)


class Loader:
    """Facts of many people for one month; what all of them share (repo baselines, bots,
    primary criteria sets) is read once."""

    def __init__(
        self,
        session: Session,
        month: date,
        tz: ZoneInfo,
        bot_pattern: str | None = None,
        module_depth: int = 2,
    ) -> None:
        self.s = session
        self.month = month
        self.lo, self.hi = month_bounds(month, tz)
        self.next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        self.bots = bot_accounts(session, bot_pattern)
        self.depth = module_depth
        self._projects: dict[int, Project] = {}
        self._roots: dict[int, str | None] = {}
        self._baselines: dict[int, float] = {}
        self._primary: dict[int, int] | None = None

    # -- shared ----------------------------------------------------------------------------

    def project(self, pid: int | None) -> Project | None:
        if pid is None:
            return None
        if pid not in self._projects:
            project = self.s.get(Project, pid)
            if project is None:
                return None
            self._projects[pid] = project
        return self._projects[pid]

    def url(self, pid: int | None, kind: str, ref: str | int) -> str | None:
        """A GitLab link: kind is merge_requests, issues or commit."""
        if pid is None:
            return None
        if pid not in self._roots:
            self._roots[pid] = web_root(self.s, self.project(pid))
        root = self._roots[pid]
        return f"{root}/-/{kind}/{ref}" if root else None

    def primary_sets(self) -> dict[int, int]:
        """dự án -> its primary criteria set (spec 22)."""
        if self._primary is None:
            self._primary = {
                int(bp): int(sid)
                for bp, sid in self.s.execute(
                    select(ProjectCriteriaSet.business_project_id, ProjectCriteriaSet.set_id)
                    .join(CriteriaSet, CriteriaSet.id == ProjectCriteriaSet.set_id)
                    .where(ProjectCriteriaSet.is_primary.is_(True))
                )
            }
        return self._primary

    def _mr_hours(self, mr_ids: list[int]) -> dict[int, float]:
        """mr id -> author hours (§2 C1)."""
        if not mr_ids:
            return {}
        mrs = {
            m.id: m for m in self.s.scalars(select(MergeRequest).where(MergeRequest.id.in_(mr_ids)))
        }
        firsts = dict(
            self.s.execute(
                select(MrCommit.mr_id, func.min(Commit.authored_at))
                .join(Commit, Commit.sha == MrCommit.sha)
                .where(MrCommit.mr_id.in_(mr_ids), Commit.is_merge.is_not(True))
                .group_by(MrCommit.mr_id)
            ).all()
        )
        notes: dict[int, list[tuple[datetime, bool, bool]]] = defaultdict(list)
        for mr_id, author, is_system, body, at in self.s.execute(
            select(
                MrNote.mr_id,
                MrNote.author_account_id,
                MrNote.is_system,
                MrNote.body,
                MrNote.created_at_src,
            ).where(MrNote.mr_id.in_(mr_ids), MrNote.created_at_src.is_not(None))
        ):
            mr = mrs.get(int(mr_id or 0))
            if mr is None:
                continue
            push = bool(is_system) and bool(PUSH.match(body or ""))
            comment = not is_system and author != mr.author_account_id
            if (push or comment) and at is not None:
                notes[int(mr_id or 0)].append((at, push, comment))
        out: dict[int, float] = {}
        for mr_id, mr in mrs.items():
            ordered = sorted(notes.get(mr_id, []), key=lambda n: n[0])
            hours = author_hours(mr.created_at_src, firsts.get(mr_id), ordered)
            if hours is not None:
                out[mr_id] = hours
        return out

    def baseline(self, project_id: int) -> float | None:
        """The repo's median author hours over BASELINE_DAYS up to the month's end."""
        if project_id not in self._baselines:
            ids = list(
                self.s.scalars(
                    select(MergeRequest.id).where(
                        MergeRequest.project_id == project_id,
                        MergeRequest.state == "merged",
                        MergeRequest.merged_at >= self.hi - timedelta(days=BASELINE_DAYS),
                        MergeRequest.merged_at < self.hi,
                    )
                )
            )
            hours = list(self._mr_hours(ids).values())
            self._baselines[project_id] = median(hours) if len(hours) >= 3 else 0.0
        value = self._baselines[project_id]
        return value or None

    # -- one person ------------------------------------------------------------------------

    def accounts(self, user_id: int) -> list[int]:
        return [
            int(a)
            for a in self.s.scalars(select(Account.id).where(Account.user_id == user_id))
            if int(a) not in self.bots
        ]

    def facts(self, user_id: int, a1_target: float, leave_days: float = 0.0) -> Facts:
        f = Facts(user_id=user_id, month=self.month, a1_target=a1_target, leave_days=leave_days)
        accounts = self.accounts(user_id)
        if not accounts:
            return f
        self._issues(f, accounts)
        self._reviews(f, user_id)
        mr_rows = self._mrs(f, accounts)
        self._pipelines(f, accounts, mr_rows)
        self._direct(f, accounts)
        self._rework(f, accounts)
        self._comments(f, accounts)
        self._items(f, user_id)
        self._defects(f, user_id)
        return f

    def _issue_fact(self, i: Issue) -> IssueFact:
        return IssueFact(
            id=i.id,
            iid=i.iid,
            project_id=i.project_id,
            title=i.title,
            web_url=i.web_url,
            type=i.type,
            points=i.complexity_points,
            assignees=len(i.assignee_account_ids or []) or 1,
            due_date=i.due_date,
            state=i.state,
            closed_at=i.closed_at_src,
            blocked=blocked(i.labels),
        )

    def _assigned(self, accounts: list[int]) -> Any:
        return or_(*[Issue.assignee_account_ids.contains([a]) for a in accounts])

    def _issues(self, f: Facts, accounts: list[int]) -> None:
        closed = list(
            self.s.scalars(
                select(Issue).where(
                    self._assigned(accounts),
                    Issue.state == "closed",
                    Issue.closed_at_src >= self.lo,
                    Issue.closed_at_src < self.hi,
                    Issue.prod_severity.is_(None),  # a production bug is not output
                )
            )
        )
        f.issues_closed = [self._issue_fact(i) for i in closed]
        f.issues_closed_all = len(closed)
        due = self.s.scalars(
            select(Issue).where(
                self._assigned(accounts),
                Issue.due_date >= self.month,
                Issue.due_date < self.next_month,
            )
        )
        f.issues_due = [self._issue_fact(i) for i in due]

    def _reviews(self, f: Facts, user_id: int) -> None:
        repo_of_mr = select(MergeRequest.id, MergeRequest.project_id).subquery()
        repo_of_commit = select(Commit.sha, Commit.project_id).subquery()
        repo_id = func.coalesce(repo_of_mr.c.project_id, repo_of_commit.c.project_id)
        rows = self.s.execute(
            select(Review.id, Review.score, Review.loc_reviewed, Review.rubric_version, repo_id)
            .outerjoin(repo_of_mr, repo_of_mr.c.id == Review.mr_id)
            .outerjoin(repo_of_commit, repo_of_commit.c.sha == Review.commit_sha)
            .where(
                Review.status == "done",
                Review.author_user_id == user_id,
                Review.counted_at >= self.lo,
                Review.counted_at < self.hi,
            )
        ).all()
        if not rows:
            return
        ids = [int(r[0]) for r in rows]
        primary = self.primary_sets()
        criteria = {
            (int(rid), int(sid)): float(score)
            for rid, sid, score in self.s.execute(
                select(
                    ReviewCriteriaScore.review_id,
                    ReviewCriteriaScore.set_id,
                    ReviewCriteriaScore.score,
                ).where(ReviewCriteriaScore.review_id.in_(ids))
            )
        }
        findings: dict[int, list[Any]] = defaultdict(list)
        for row in self.s.execute(
            select(
                ReviewFinding.review_id,
                ReviewFinding.severity,
                ReviewFinding.category,
                ReviewFinding.confidence,
                ReviewFinding.status,
                ReviewFinding.verified,
            ).where(ReviewFinding.review_id.in_(ids))
        ):
            findings[int(row[0] or 0)].append(row)
        weighted = weight = 0.0
        for rid, score, loc, rubric_version, pid in rows:
            project = self.project(pid)
            set_id = (
                primary.get(project.business_project_id)
                if project and project.business_project_id
                else None
            )
            value = criteria.get((int(rid), set_id)) if set_id is not None else None
            if value is None and score is not None:
                value = float(score)
            w = max(int(loc or 0), 1)
            f.review_loc += int(loc or 0)
            if value is not None:
                weighted += value * w
                weight += w
            rule = rule_for(rubric_version)
            severe = False
            for _rid, severity, category, confidence, status, verified in findings.get(
                int(rid), []
            ):
                scored = ScoredFinding(
                    str(severity), float(confidence or 0), status or "open", verified or "skipped"
                )
                if not counts_for_score(scored, rule, MIN_CONFIDENCE_UNVERIFIED):
                    continue
                severe = severe or severity in ("critical", "high")
                if category == "testing":
                    f.testing_findings += 1
            f.severe_reviews += severe
        f.review_count = len(rows)
        f.review_avg = weighted / weight if weight else None

    def _head_pipelines(self, shas: list[str]) -> dict[str, Pipeline]:
        """sha -> its latest pipeline (a retry replaces a failure)."""
        out: dict[str, Pipeline] = {}
        if not shas:
            return out
        for p in self.s.scalars(
            select(Pipeline).where(Pipeline.sha.in_(shas)).order_by(Pipeline.id)
        ):
            if p.sha:
                out[p.sha] = p
        return out

    def _mrs(self, f: Facts, accounts: list[int]) -> list[MergeRequest]:
        mrs = list(
            self.s.scalars(
                select(MergeRequest).where(
                    MergeRequest.author_account_id.in_(accounts),
                    MergeRequest.state == "merged",
                    MergeRequest.merged_at >= self.lo,
                    MergeRequest.merged_at < self.hi,
                )
            )
        )
        if not mrs:
            return []
        ids = [m.id for m in mrs]
        links: dict[int, list[int]] = defaultdict(list)
        for issue_id, mr_id in self.s.execute(
            select(IssueMrLink.issue_id, IssueMrLink.mr_id).where(IssueMrLink.mr_id.in_(ids))
        ):
            links[int(mr_id)].append(int(issue_id))
        # Reviewed before the merge: a comment or an approval by someone else (spec 20 §4.2).
        reviewed = set(
            self.s.scalars(
                select(MrNote.mr_id)
                .join(MergeRequest, MergeRequest.id == MrNote.mr_id)
                .where(
                    MrNote.mr_id.in_(ids),
                    MrNote.author_account_id != MergeRequest.author_account_id,
                    MrNote.author_account_id.not_in(self.bots or {0}),
                    or_(
                        MrNote.is_system.is_not(True),
                        func.lower(MrNote.body).contains("approved this merge request"),
                    ),
                    MrNote.created_at_src <= MergeRequest.merged_at,
                )
            )
        )
        heads = self._head_pipelines([m.head_sha for m in mrs if m.head_sha])
        hours = self._mr_hours(ids)
        for m in mrs:
            pipeline = heads.get(m.head_sha or "")
            if m.project_id is not None and m.id in hours:
                base = self.baseline(m.project_id)
                if base:
                    f.repo_author_median[m.project_id] = base
            f.mrs.append(
                MrFact(
                    id=m.id,
                    iid=m.iid,
                    project_id=int(m.project_id or 0),
                    title=m.title,
                    web_url=self.url(m.project_id, "merge_requests", m.iid),
                    merged_at=m.merged_at,
                    author_hours=hours.get(m.id),
                    issue_ids=tuple(sorted(links.get(m.id, []))),
                    tasks_total=m.tasks_total or 0,
                    tasks_done=m.tasks_done or 0,
                    pipeline=pipeline.status if pipeline else None,
                    coverage=float(pipeline.coverage)
                    if pipeline is not None and pipeline.coverage is not None
                    else None,
                    reviewed=m.id in reviewed,
                    refactor=any(
                        (label or "").lower() == "type::refactor" for label in m.labels or []
                    ),
                )
            )
        return mrs

    def _pipelines(self, f: Facts, accounts: list[int], mrs: list[MergeRequest]) -> None:
        shas = set(
            self.s.scalars(
                select(Commit.sha).where(
                    Commit.author_account_id.in_(accounts),
                    Commit.is_merge.is_not(True),
                    Commit.authored_at >= self.lo,
                    Commit.authored_at < self.hi,
                )
            )
        )
        shas |= {m.head_sha for m in mrs if m.head_sha}
        for p in self._head_pipelines(sorted(shas)).values():
            if p.status == "success":
                f.pipelines_success += 1
            elif p.status == "failed":
                f.pipelines_failed += 1

    def _direct(self, f: Facts, accounts: list[int]) -> None:
        """§2 C3: their non-merge commits no MR carries (pushed straight to a branch)."""
        in_mr = select(MrCommit.sha).where(MrCommit.sha == Commit.sha)
        made_by_mr = select(MergeRequest.id).where(
            or_(
                MergeRequest.merge_commit_sha == Commit.sha,
                MergeRequest.squash_commit_sha == Commit.sha,
            )
        )
        rows = self.s.execute(
            select(Commit.sha, Commit.project_id, Commit.message)
            .where(
                Commit.author_account_id.in_(accounts),
                Commit.is_merge.is_not(True),
                Commit.authored_at >= self.lo,
                Commit.authored_at < self.hi,
                ~exists(in_mr),
                ~exists(made_by_mr),
            )
            .order_by(Commit.authored_at.desc())
        ).all()
        for sha, pid, message in rows:
            f.direct_commits.append(
                {
                    "ref": sha[:8],
                    "label": (message or "").strip().splitlines()[0][:200] if message else sha,
                    "url": self.url(pid, "commit", sha),
                }
            )

    def _rework(self, f: Facts, accounts: list[int]) -> None:
        """§4.2: lines added in [lo-21d, hi-21d) (fully observable at the month's end), and how
        much of them the same person deleted again in the same file within 21 days; refactor
        commits do not count as rework."""
        start = self.lo - timedelta(days=REWORK_DAYS)
        end = self.hi
        rows = self.s.execute(
            select(
                Commit.sha,
                Commit.authored_at,
                Commit.message,
                CommitFile.path,
                CommitFile.additions,
                CommitFile.deletions,
            )
            .join(CommitFile, CommitFile.sha == Commit.sha)
            .where(
                Commit.author_account_id.in_(accounts),
                Commit.is_merge.is_not(True),
                Commit.authored_at >= start,
                Commit.authored_at < end,
            )
            .order_by(Commit.authored_at)
        ).all()
        by_path: dict[str, list[tuple[datetime, int, int, bool]]] = defaultdict(list)
        for _sha, at, message, path, adds, dels in rows:
            refactor = (message or "").lower().lstrip().startswith("refactor")
            if at is not None:
                by_path[path].append((at, int(adds or 0), int(dels or 0), refactor))
        window_hi = self.hi - timedelta(days=REWORK_DAYS)
        files: list[dict[str, Any]] = []
        for path, events in by_path.items():
            added = reworked = 0
            for i, (at, adds, _dels, _refactor) in enumerate(events):
                if not (start <= at < window_hi) or adds <= 0:
                    continue
                later = sum(
                    d
                    for t, _a, d, refactor in events[i + 1 :]
                    if t <= at + timedelta(days=REWORK_DAYS) and not refactor
                )
                added += adds
                reworked += min(adds, later)
            if added:
                f.added_lines += added
                f.reworked_lines += reworked
                if reworked:
                    files.append({"label": path, "added": added, "reworked": reworked})
        f.rework_files = sorted(files, key=lambda r: -r["reworked"])

    def _comments(self, f: Facts, accounts: list[int]) -> None:
        notes = self.s.execute(
            select(
                MrNote.id,
                MrNote.mr_id,
                MrNote.body,
                MrNote.created_at_src,
                MergeRequest.iid,
                MergeRequest.project_id,
                ReviewComment.label,
                ReviewComment.acted_on,
                ReviewComment.confirmed_by,
            )
            .join(MergeRequest, MergeRequest.id == MrNote.mr_id)
            .outerjoin(ReviewComment, ReviewComment.note_id == MrNote.id)
            .where(
                MrNote.author_account_id.in_(accounts),
                MrNote.is_system.is_not(True),
                MergeRequest.author_account_id.not_in(accounts),
                MrNote.created_at_src >= self.lo,
                MrNote.created_at_src < self.hi,
            )
        ).all()
        if not notes:
            return
        mr_ids = sorted({int(n.mr_id) for n in notes})
        pushes: dict[int, list[datetime]] = defaultdict(list)
        for mr_id, body, at in self.s.execute(
            select(MrNote.mr_id, MrNote.body, MrNote.created_at_src).where(
                MrNote.mr_id.in_(mr_ids), MrNote.is_system.is_(True)
            )
        ):
            if at is not None and PUSH.match(body or ""):
                pushes[int(mr_id or 0)].append(at)
        for n in notes:
            label = n.label or classify_rule(n.body)
            acted = bool(n.acted_on) or any(
                at > n.created_at_src for at in pushes.get(int(n.mr_id), []) if n.created_at_src
            )
            f.comments.append(
                CommentFact(
                    note_id=int(n.id),
                    mr_id=int(n.mr_id),
                    mr_iid=int(n.iid),
                    project_id=int(n.project_id or 0),
                    label=label,
                    acted_on=acted,
                    confirmed=n.confirmed_by is not None,
                    web_url=self.url(n.project_id, "merge_requests", int(n.iid)),
                )
            )
        f.reviewed_mrs = len(mr_ids)
        self._outside(f, accounts, mr_ids)

    def _outside(self, f: Facts, accounts: list[int], mr_ids: list[int]) -> None:
        """§2 D2: MRs reviewed in modules they did not commit to in the last 90 days."""
        mine = {
            module_of(path, self.depth)
            for (path,) in self.s.execute(
                select(CommitFile.path)
                .join(Commit, Commit.sha == CommitFile.sha)
                .where(
                    Commit.author_account_id.in_(accounts),
                    Commit.authored_at >= self.hi - timedelta(days=BASELINE_DAYS),
                    Commit.authored_at < self.hi,
                )
                .distinct()
            )
        }
        touched: dict[int, set[str]] = defaultdict(set)
        for mr_id, path in self.s.execute(
            select(MrCommit.mr_id, CommitFile.path)
            .join(CommitFile, CommitFile.sha == MrCommit.sha)
            .where(MrCommit.mr_id.in_(mr_ids))
        ):
            touched[int(mr_id)].add(module_of(path, self.depth))
        mrs = {
            m.id: m for m in self.s.scalars(select(MergeRequest).where(MergeRequest.id.in_(mr_ids)))
        }
        for mr_id, modules in touched.items():
            if modules and not modules & mine and mr_id in mrs:
                m = mrs[mr_id]
                f.outside_reviews.append(
                    {
                        "id": m.id,
                        "ref": f"!{m.iid}",
                        "label": m.title,
                        "url": self.url(m.project_id, "merge_requests", m.iid),
                        "modules": sorted(modules)[:3],
                    }
                )

    def _items(self, f: Facts, user_id: int) -> None:
        pack = self.s.scalars(
            select(EvidencePack).where(
                EvidencePack.user_id == user_id, EvidencePack.period_start == self.month
            )
        ).first()
        if pack is None:
            return
        confirmed = pack.status in ("reviewed", "shared")
        f.items = [
            ItemFact(id=int(i.id), kind=i.kind, title=i.title, confirmed=confirmed)
            for i in self.s.scalars(select(EvidenceItem).where(EvidenceItem.pack_id == pack.id))
            if i.kind not in ("mr", "commit")  # Git work is counted from Git already
        ]

    def _defects(self, f: Facts, user_id: int) -> None:
        rows = self.s.execute(
            select(ProdDefect, Issue)
            .join(Issue, Issue.id == ProdDefect.issue_id)
            .where(
                ProdDefect.rca_status == "confirmed",
                Issue.created_at_src >= self.lo,
                Issue.created_at_src < self.hi,
            )
        ).all()
        for defect, issue in rows:
            share = sum(
                float(r.get("share") or 0)
                for r in defect.responsible or []
                if int(r.get("user_id") or 0) == user_id
            )
            if share > 0:
                f.defects.append(
                    DefectFact(
                        id=int(defect.id),
                        issue_id=int(issue.id),
                        severity=defect.severity,
                        share=min(1.0, share),
                        title=issue.title,
                        web_url=issue.web_url,
                    )
                )


def active_people(session: Session, month: date, tz: ZoneInfo) -> list[int]:
    """People with Git activity in the month (a commit, a merged MR, a review comment, a closed
    issue assigned to them): the ones the cycle creates a scorecard for (§5)."""
    lo, hi = month_bounds(month, tz)
    commits = (
        select(Account.user_id)
        .join(Commit, Commit.author_account_id == Account.id)
        .where(Commit.authored_at >= lo, Commit.authored_at < hi)
    )
    notes = (
        select(Account.user_id)
        .join(MrNote, MrNote.author_account_id == Account.id)
        .where(
            MrNote.created_at_src >= lo, MrNote.created_at_src < hi, MrNote.is_system.is_not(True)
        )
    )
    mrs = (
        select(Account.user_id)
        .join(MergeRequest, MergeRequest.author_account_id == Account.id)
        .where(and_(MergeRequest.merged_at >= lo, MergeRequest.merged_at < hi))
    )
    ids: set[int] = set()
    for query in (commits, notes, mrs):
        ids |= {int(u) for u in session.scalars(query.distinct()) if u is not None}
    return sorted(ids)
