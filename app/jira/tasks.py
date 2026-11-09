"""Task Jira: Jira issues as work to look at, with the MRs and reviews behind them (user
2026-11-09; docs/specs/27 §18).

Read only, like the rest of the integration. Who sees what:

- issues: those counted for a dự án one sees (`jira_issue_in`, §15); admins and `jira.admin`
  see every issue, also of Jira projects not yet routed to a dự án;
- MRs: those in a repo of a dự án one sees (all for admins);
- reviews and their findings: only those `review.view` covers (`work_scope`), so a member who
  sees only their own reviews does not learn others' scores through an issue.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, false, func, or_, select, true
from sqlalchemy.orm import Session, aliased

from app.api.access import may_rerun, work_scope
from app.api.security import Principal
from app.jira.scopes import jira_issue_in, projects_in
from app.models import (
    Account,
    BusinessProject,
    Commit,
    Issue,
    IssueCommitLink,
    IssueMrLink,
    JiraProject,
    JiraProjectScope,
    MergeRequest,
    MrCommit,
    Project,
    Review,
    ReviewFinding,
    User,
)

MAX_MRS = 50  # per issue in its detail
SEVERE = ("critical", "high")


@dataclass
class IssueFilter:
    business_project_id: int | None = None
    jira_project: str | None = None  # its key, ABC
    state: str = "all"  # open | closed | all
    type: str | None = None
    assignee: int | None = None  # a CodePulse person
    q: str | None = None
    linked: str = "any"  # any | with_mr | without_mr | bug_unfixed
    start: date | None = None
    end: date | None = None


# -- who sees what -------------------------------------------------------------------------------


def seen_projects(principal: Principal) -> set[int] | None:
    """Dự án whose Jira issues one sees; None: every issue (admin, jira.admin)."""
    if principal.is_admin or principal.can("jira.admin"):
        return None
    return principal.visible_projects() or set()


def issue_scope(principal: Principal) -> ColumnElement[bool]:
    bps = seen_projects(principal)
    if bps is None:
        return true()
    return jira_issue_in(bps) if bps else false()


def repo_scope(principal: Principal) -> ColumnElement[bool]:
    """MRs one may see: in a repo of a dự án one sees (an alias: callers also join Project)."""
    if principal.is_admin:
        return true()
    bps = principal.visible_projects() or set()
    if not bps:
        return false()
    repo = aliased(Project)
    return exists().where(repo.id == MergeRequest.project_id, repo.business_project_id.in_(bps))


def _bounds(f: IssueFilter) -> tuple[datetime | None, datetime | None]:
    lo = datetime.combine(f.start, time.min, UTC) if f.start else None
    hi = datetime.combine(f.end, time.min, UTC) + timedelta(days=1) if f.end else None
    return lo, hi


def _accounts_of(session: Session, user_id: int) -> list[int]:
    return list(session.scalars(select(Account.id).where(Account.user_id == user_id)))


def _base_where(
    session: Session, principal: Principal, f: IssueFilter
) -> list[ColumnElement[bool]]:
    """Every filter but state and links (the KPIs count across those)."""
    where: list[ColumnElement[bool]] = [Issue.source == "jira", issue_scope(principal)]
    if f.business_project_id is not None:
        where.append(jira_issue_in([f.business_project_id]))
    if f.jira_project:
        where.append(
            exists().where(
                JiraProject.id == Issue.jira_project_id, JiraProject.key == f.jira_project
            )
        )
    if f.type:
        where.append(Issue.type == f.type)
    if f.assignee is not None:
        accounts = _accounts_of(session, f.assignee)
        where.append(
            or_(*[Issue.assignee_account_ids.contains([a]) for a in accounts])
            if accounts
            else false()
        )
    if f.q and f.q.strip():
        text = f.q.strip()
        like = f"%{text.replace('%', r'\%').replace('_', r'\_')}%"
        where.append(or_(Issue.key.ilike(like), Issue.title.ilike(like)))
    lo, hi = _bounds(f)
    # Worked on in the period: created before its end, not closed before its start.
    if hi is not None:
        where.append(or_(Issue.created_at_src.is_(None), Issue.created_at_src < hi))
    if lo is not None:
        where.append(or_(Issue.closed_at_src.is_(None), Issue.closed_at_src >= lo))
    return where


def commit_scope(principal: Principal) -> ColumnElement[bool]:
    """Commits one may see: in a repo of a dự án one sees."""
    if principal.is_admin:
        return true()
    bps = principal.visible_projects() or set()
    if not bps:
        return false()
    repo = aliased(Project)
    return exists().where(repo.id == Commit.project_id, repo.business_project_id.in_(bps))


def _has_mr(principal: Principal) -> ColumnElement[bool]:
    return exists().where(
        IssueMrLink.issue_id == Issue.id,
        MergeRequest.id == IssueMrLink.mr_id,
        repo_scope(principal),
    )


def _has_commit(principal: Principal, kind: str | None = None) -> ColumnElement[bool]:
    """A commit one may see names the issue (`kind`: only links of that kind)."""
    where = [
        IssueCommitLink.issue_id == Issue.id,
        Commit.sha == IssueCommitLink.sha,
        commit_scope(principal),
    ]
    if kind is not None:
        where.append(IssueCommitLink.kind == kind)
    return exists().where(*where)


def _has_code(principal: Principal) -> ColumnElement[bool]:
    """Some code names the issue: an MR, or a commit of its own."""
    return or_(_has_mr(principal), _has_commit(principal))


def _fixed(principal: Principal) -> ColumnElement[bool]:
    """Code says it fixes the issue: a commit with `Fixes: KEY` (or a fix subject), or an MR
    naming it in its title or branch (§8 `closes`)."""
    by_mr = exists().where(
        IssueMrLink.issue_id == Issue.id,
        IssueMrLink.source != "mention",
        MergeRequest.id == IssueMrLink.mr_id,
        repo_scope(principal),
    )
    return or_(_has_commit(principal, "fixes"), by_mr)


BUG = "bug"


def _severe_open(principal: Principal) -> ColumnElement[bool]:
    """A linked MR's current review (one one may see) has an open critical/high finding."""
    seen = work_scope(principal)
    return exists().where(
        IssueMrLink.issue_id == Issue.id,
        Review.mr_id == IssueMrLink.mr_id,
        Review.status == "done",
        ReviewFinding.review_id == Review.id,
        ReviewFinding.status == "open",
        ReviewFinding.severity.in_(SEVERE),
        seen if seen is not None else true(),
    )


# -- the list ------------------------------------------------------------------------------------


def list_issues(
    session: Session, principal: Principal, f: IssueFilter, offset: int, limit: int
) -> dict[str, Any]:
    base = _base_where(session, principal, f)
    where = list(base)
    if f.state in ("open", "closed"):
        where.append(Issue.state == ("opened" if f.state == "open" else "closed"))
    if f.linked == "with_mr":
        where.append(_has_code(principal))
    elif f.linked == "without_mr":
        where.append(~_has_code(principal))
    elif f.linked == "bug_unfixed":
        where.extend([Issue.type == BUG, ~_fixed(principal)])

    total = int(session.scalar(select(func.count()).select_from(Issue).where(*where)) or 0)
    rows = list(
        session.scalars(
            select(Issue)
            .where(*where)
            .order_by(
                (Issue.state == "closed").asc(),
                func.coalesce(Issue.closed_at_src, Issue.created_at_src).desc().nulls_last(),
                Issue.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "items": issue_rows(session, principal, rows),
        "total": total,
        "kpis": kpis(session, principal, base, f),
    }


def kpis(
    session: Session, principal: Principal, base: Sequence[ColumnElement[bool]], f: IssueFilter
) -> dict[str, int]:
    lo, hi = _bounds(f)
    closed_in = [Issue.state == "closed"]
    if lo is not None:
        closed_in.append(Issue.closed_at_src >= lo)
    if hi is not None:
        closed_in.append(Issue.closed_at_src < hi)
    row = session.execute(
        select(
            func.count(),
            func.count().filter(Issue.state == "opened"),
            func.count().filter(and_(*closed_in)),
            func.count().filter(_has_code(principal)),
            func.count().filter(Issue.story_points.is_not(None)),
            func.count().filter(_severe_open(principal)),
            func.count().filter(Issue.type == BUG),
            func.count().filter(and_(Issue.type == BUG, _fixed(principal))),
        ).where(*base)
    ).one()
    return {
        "issues": int(row[0]),
        "open": int(row[1]),
        "closed_in_period": int(row[2]),
        # Some code names it: an MR or a commit of its own.
        "with_mr": int(row[3]),
        "with_points": int(row[4]),
        "severe_open": int(row[5]),
        "bugs": int(row[6]),
        "bugs_fixed": int(row[7]),
    }


def _people(session: Session, account_ids: set[int]) -> dict[int, dict[str, Any]]:
    """account id -> {user_id, name}: the person when the account is linked, else its name."""
    if not account_ids:
        return {}
    rows = session.execute(
        select(
            Account.id, Account.display_name, Account.user_id, User.full_name, User.primary_email
        )
        .outerjoin(User, User.id == Account.user_id)
        .where(Account.id.in_(account_ids))
    ).all()
    return {
        int(r.id): {
            "user_id": r.user_id,
            "name": r.full_name or r.primary_email or r.display_name or f"#{r.id}",
        }
        for r in rows
    }


def _repo_urls(session: Session, projects: list[Project]) -> dict[int, str]:
    from app.mcp.queries import repo_urls

    return repo_urls(session, list(projects))


def mr_summaries(
    session: Session, principal: Principal, issue_ids: list[int], per_issue: int | None = None
) -> dict[int, list[dict[str, Any]]]:
    """issue id -> its MRs one may see, each with its current review (if one may see it)."""
    if not issue_ids:
        return {}
    links = session.execute(
        select(IssueMrLink.issue_id, IssueMrLink.source, MergeRequest, Project)
        .join(MergeRequest, MergeRequest.id == IssueMrLink.mr_id)
        .outerjoin(Project, Project.id == MergeRequest.project_id)
        .where(IssueMrLink.issue_id.in_(issue_ids), repo_scope(principal))
        .order_by(MergeRequest.created_at_src.desc().nulls_last(), MergeRequest.id.desc())
    ).all()
    mrs = {link.MergeRequest.id: link.MergeRequest for link in links}
    urls = _repo_urls(session, [link.Project for link in links if link.Project is not None])
    reviews = latest_reviews(session, principal, list(mrs))
    authors = _people(session, {m.author_account_id for m in mrs.values() if m.author_account_id})
    out: dict[int, list[dict[str, Any]]] = {}
    for link in links:
        mr, repo = link.MergeRequest, link.Project
        rows = out.setdefault(int(link.issue_id), [])
        if per_issue is not None and len(rows) >= per_issue:
            continue
        base = urls.get(repo.id) if repo is not None else None
        rows.append(
            {
                "id": mr.id,
                "iid": mr.iid,
                "title": mr.title,
                "state": mr.state,
                "repo": repo.path_with_namespace if repo is not None else None,
                "url": f"{base}/-/merge_requests/{mr.iid}" if base else None,
                "author": authors.get(mr.author_account_id or 0),
                "created_at": mr.created_at_src,
                "merged_at": mr.merged_at,
                "link": "mention" if link.source == "mention" else "closes",
                "review": reviews.get(mr.id),
            }
        )
    return out


def latest_reviews(
    session: Session, principal: Principal, mr_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """mr id -> its newest finished review one may see, with its open critical/high counts."""
    if not mr_ids:
        return {}
    seen = work_scope(principal)
    newest = (
        select(Review.mr_id, func.max(Review.id).label("id"))
        .where(Review.mr_id.in_(mr_ids), Review.status == "done")
        .where(seen if seen is not None else true())
        .group_by(Review.mr_id)
        .subquery()
    )
    rows = session.execute(
        select(Review.id, Review.mr_id, Review.score, Review.verdict, Review.finished_at).join(
            newest, newest.c.id == Review.id
        )
    ).all()
    ids = [r.id for r in rows]
    counts: dict[int, dict[str, int]] = {}
    if ids:
        for review_id, severity, n in session.execute(
            select(ReviewFinding.review_id, ReviewFinding.severity, func.count())
            .where(
                ReviewFinding.review_id.in_(ids),
                ReviewFinding.status == "open",
                ReviewFinding.severity.in_(SEVERE),
            )
            .group_by(ReviewFinding.review_id, ReviewFinding.severity)
        ):
            counts.setdefault(int(review_id or 0), {})[str(severity)] = int(n)
    return {
        int(r.mr_id): {
            "id": r.id,
            "score": float(r.score) if r.score is not None else None,
            "verdict": r.verdict,
            "finished_at": r.finished_at,
            "critical": counts.get(r.id, {}).get("critical", 0),
            "high": counts.get(r.id, {}).get("high", 0),
        }
        for r in rows
    }


def commit_summaries(
    session: Session, principal: Principal, issue_ids: list[int], per_issue: int | None = None
) -> dict[int, list[dict[str, Any]]]:
    """issue id -> the commits one may see whose messages name it, fixes first then newest,
    each with its review when it was reviewed on its own (and one may see it)."""
    if not issue_ids:
        return {}
    links = session.execute(
        select(
            IssueCommitLink.issue_id,
            IssueCommitLink.kind,
            IssueCommitLink.manual,
            IssueCommitLink.linked_by,
            Commit,
            Project,
        )
        .join(Commit, Commit.sha == IssueCommitLink.sha)
        .outerjoin(Project, Project.id == Commit.project_id)
        .where(IssueCommitLink.issue_id.in_(issue_ids), commit_scope(principal))
        .order_by(
            (IssueCommitLink.kind == "refs").asc(),
            Commit.authored_at.desc().nulls_last(),
            Commit.sha,
        )
    ).all()
    commits = {link.Commit.sha: link.Commit for link in links}
    urls = _repo_urls(session, [link.Project for link in links if link.Project is not None])
    reviews = commit_reviews(session, principal, list(commits))
    authors = _people(
        session, {c.author_account_id for c in commits.values() if c.author_account_id}
    )
    linkers = _user_names(session, {int(link.linked_by) for link in links if link.linked_by})
    out: dict[int, list[dict[str, Any]]] = {}
    for link in links:
        c, repo = link.Commit, link.Project
        bp = repo.business_project_id if repo is not None else None
        author = authors.get(c.author_account_id or 0)
        rows = out.setdefault(int(link.issue_id), [])
        if per_issue is not None and len(rows) >= per_issue:
            continue
        base = urls.get(repo.id) if repo is not None else None
        rows.append(
            {
                "sha": c.sha,
                "title": (c.message or "").split("\n", 1)[0][:300],
                "repo": repo.path_with_namespace if repo is not None else None,
                "url": f"{base}/-/commit/{c.sha}" if base else None,
                "author": author,
                "authored_at": c.authored_at,
                "kind": link.kind,
                "manual": bool(link.manual),
                "linked_by": linkers.get(int(link.linked_by)) if link.linked_by else None,
                "review": reviews.get(c.sha),
                # "Review lại": the review's own rule (review.rerun, a dự án one manages).
                "can_rerun": bool(reviews.get(c.sha)) and may_rerun(principal, None, bp),
            }
        )
    return out


def _user_names(session: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {
        u.id: u.full_name or u.primary_email or f"#{u.id}"
        for u in session.scalars(select(User).where(User.id.in_(ids)))
    }


# -- linking commits by hand and moving issues (docs/specs/27 §20) ---------------------------

MAX_CANDIDATES = 20


def issue_projects(session: Session, issue: Issue) -> set[int]:
    """The dự án an issue counts for (§15): its own routing, else every scope of its Jira
    project."""
    if issue.business_project_ids:
        return set(issue.business_project_ids)
    return set(
        session.scalars(
            select(JiraProjectScope.business_project_id).where(
                JiraProjectScope.jira_project_id == issue.jira_project_id
            )
        )
    )


def may_move(principal: Principal, bps: set[int]) -> bool:
    """Change the issue's status on Jira: admins, jira.admin, managers of one of its dự án."""
    if principal.is_admin or principal.can("jira.admin"):
        return True
    return any(principal.can_manage_project(bp) for bp in bps)


def may_link(principal: Principal, bps: set[int], author_user_id: int | None = None) -> bool:
    """Link a commit to an issue by hand: who may move it, and a commit's own author."""
    if may_move(principal, bps):
        return True
    return author_user_id is not None and author_user_id == principal.user_id


def commit_author(session: Session, sha: str) -> int | None:
    return session.scalar(
        select(Account.user_id)
        .join(Commit, Commit.author_account_id == Account.id)
        .where(Commit.sha == sha)
    )


def find_issue(session: Session, principal: Principal, key: str) -> Issue | None:
    return session.scalar(
        select(Issue).where(Issue.source == "jira", Issue.key == key, issue_scope(principal))
    )


def candidates(
    session: Session, principal: Principal, issue: Issue, q: str | None
) -> list[dict[str, Any]]:
    """Reviewed commits one may see, in the issue's dự án, not linked to it yet: by sha prefix
    or text of the message, newest first."""
    bps = issue_projects(session, issue)
    repo = aliased(Project)
    where: list[ColumnElement[bool]] = [
        commit_scope(principal),
        Commit.is_merge.is_not(True),
        exists().where(Review.commit_sha == Commit.sha, Review.status == "done"),
        ~exists().where(IssueCommitLink.issue_id == issue.id, IssueCommitLink.sha == Commit.sha),
    ]
    if bps:
        where.append(
            exists().where(repo.id == Commit.project_id, repo.business_project_id.in_(bps))
        )
    elif not principal.is_admin:
        return []
    text = (q or "").strip()
    if text:
        if re.fullmatch(r"[0-9a-fA-F]{4,40}", text):
            where.append(Commit.sha.startswith(text.lower()))
        else:
            like = "%" + text.replace("%", r"\%").replace("_", r"\_") + "%"
            where.append(Commit.message.ilike(like))
    rows = session.execute(
        select(Commit, Project)
        .outerjoin(Project, Project.id == Commit.project_id)
        .where(*where)
        .order_by(Commit.authored_at.desc().nulls_last(), Commit.sha)
        .limit(MAX_CANDIDATES)
    ).all()
    shas = [r.Commit.sha for r in rows]
    reviews = commit_reviews(session, principal, shas)
    urls = _repo_urls(session, [r.Project for r in rows if r.Project is not None])
    authors = _people(
        session, {r.Commit.author_account_id for r in rows if r.Commit.author_account_id}
    )
    out = []
    for r in rows:
        c, proj = r.Commit, r.Project
        base = urls.get(proj.id) if proj is not None else None
        out.append(
            {
                "sha": c.sha,
                "title": (c.message or "").split("\n", 1)[0][:300],
                "repo": proj.path_with_namespace if proj is not None else None,
                "url": f"{base}/-/commit/{c.sha}" if base else None,
                "author": authors.get(c.author_account_id or 0),
                "authored_at": c.authored_at,
                "review": reviews.get(c.sha),
            }
        )
    return out


def commit_counts(
    session: Session, principal: Principal, issue_ids: list[int]
) -> dict[int, tuple[int, int]]:
    """issue id -> (commits one may see naming it, those fixing it): one aggregate, for the
    list (the detail loads the commits themselves)."""
    if not issue_ids:
        return {}
    rows = session.execute(
        select(
            IssueCommitLink.issue_id,
            func.count(),
            func.count().filter(IssueCommitLink.kind == "fixes"),
        )
        .join(Commit, Commit.sha == IssueCommitLink.sha)
        .where(IssueCommitLink.issue_id.in_(issue_ids), commit_scope(principal))
        .group_by(IssueCommitLink.issue_id)
    ).all()
    return {int(i): (int(n), int(fixes)) for i, n, fixes in rows}


def commit_reviews(
    session: Session, principal: Principal, shas: list[str]
) -> dict[str, dict[str, Any]]:
    """sha -> its newest finished commit review one may see, with open critical/high counts."""
    if not shas:
        return {}
    seen = work_scope(principal)
    newest = (
        select(Review.commit_sha, func.max(Review.id).label("id"))
        .where(Review.commit_sha.in_(shas), Review.status == "done")
        .where(seen if seen is not None else true())
        .group_by(Review.commit_sha)
        .subquery()
    )
    rows = session.execute(
        select(Review.id, Review.commit_sha, Review.score, Review.verdict, Review.finished_at).join(
            newest, newest.c.id == Review.id
        )
    ).all()
    counts = _severe_counts(session, [r.id for r in rows])
    return {
        str(r.commit_sha): {
            "id": r.id,
            "score": float(r.score) if r.score is not None else None,
            "verdict": r.verdict,
            "finished_at": r.finished_at,
            "critical": counts.get(r.id, {}).get("critical", 0),
            "high": counts.get(r.id, {}).get("high", 0),
        }
        for r in rows
    }


def _severe_counts(session: Session, review_ids: list[int]) -> dict[int, dict[str, int]]:
    counts: dict[int, dict[str, int]] = {}
    if not review_ids:
        return counts
    for review_id, severity, n in session.execute(
        select(ReviewFinding.review_id, ReviewFinding.severity, func.count())
        .where(
            ReviewFinding.review_id.in_(review_ids),
            ReviewFinding.status == "open",
            ReviewFinding.severity.in_(SEVERE),
        )
        .group_by(ReviewFinding.review_id, ReviewFinding.severity)
    ):
        counts.setdefault(int(review_id or 0), {})[str(severity)] = int(n)
    return counts


def _jira_keys(session: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {
        int(i): str(k)
        for i, k in session.execute(
            select(JiraProject.id, JiraProject.key).where(JiraProject.id.in_(ids))
        )
    }


def issue_rows(session: Session, principal: Principal, issues: list[Issue]) -> list[dict[str, Any]]:
    people = _people(session, {a for i in issues for a in i.assignee_account_ids or []})
    mrs = mr_summaries(session, principal, [i.id for i in issues])
    commits = commit_counts(session, principal, [i.id for i in issues])
    projects = _jira_keys(session, {i.jira_project_id for i in issues if i.jira_project_id})
    out = []
    for i in issues:
        linked = mrs.get(i.id, [])
        reviewed = [m["review"] for m in linked if m["review"]]
        scores = [r["score"] for r in reviewed if r["score"] is not None]
        out.append(
            {
                "key": i.key,
                "title": i.title,
                "url": i.web_url,
                "state": i.state,
                "type": i.type,
                "story_points": float(i.story_points) if i.story_points is not None else None,
                "jira_project": projects.get(i.jira_project_id or 0),
                "parent_key": i.parent_key,
                "assignees": [people[a] for a in i.assignee_account_ids or [] if a in people],
                "created_at": i.created_at_src,
                "closed_at": i.closed_at_src,
                "due_date": i.due_date,
                "mrs": len(linked),
                "mrs_merged": sum(1 for m in linked if m["merged_at"] is not None),
                "commit_count": commits.get(i.id, (0, 0))[0],
                "fix_commits": commits.get(i.id, (0, 0))[1],
                "reviews": len(reviewed),
                "score": round(sum(scores) / len(scores), 1) if scores else None,
                "critical": sum(r["critical"] for r in reviewed),
                "high": sum(r["high"] for r in reviewed),
            }
        )
    return out


# -- one issue -------------------------------------------------------------------------------------


def _brief(i: Issue) -> dict[str, Any]:
    return {"key": i.key, "title": i.title, "state": i.state, "type": i.type}


def issue_detail(session: Session, principal: Principal, key: str) -> dict[str, Any] | None:
    issue = session.scalar(
        select(Issue).where(Issue.source == "jira", Issue.key == key, issue_scope(principal))
    )
    if issue is None:
        return None
    row = issue_rows(session, principal, [issue])[0]
    family = list(
        session.scalars(
            select(Issue)
            .where(
                Issue.source == "jira",
                issue_scope(principal),
                or_(
                    Issue.parent_key == issue.key,
                    Issue.key == issue.parent_key if issue.parent_key else false(),
                ),
            )
            .order_by(Issue.iid)
            .limit(100)
        )
    )
    bps = session.execute(
        select(BusinessProject.id, BusinessProject.name).where(
            BusinessProject.id.in_(issue.business_project_ids or [])
        )
    ).all()
    return {
        **row,
        "labels": issue.labels or [],
        "components": issue.components or [],
        "business_projects": [{"id": b.id, "name": b.name} for b in bps],
        "parent": next((_brief(i) for i in family if i.key == issue.parent_key), None),
        "children": [_brief(i) for i in family if i.parent_key == issue.key],
        "merge_requests": mr_summaries(session, principal, [issue.id], MAX_MRS).get(issue.id, []),
        "commits": commit_summaries(session, principal, [issue.id], MAX_MRS).get(issue.id, []),
        # §20: link reviewed commits by hand (who may move it, or a commit's author: then
        # only their own commits), move the issue on Jira.
        "can_link": may_link(principal, issue_projects(session, issue))
        or principal.can("review.view"),
        "can_move": may_move(principal, issue_projects(session, issue)),
    }


# -- MRs that name no issue ----------------------------------------------------------------------


def unlinked_mrs(
    session: Session,
    principal: Principal,
    business_project_id: int | None,
    start: date | None,
    end: date | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """MRs opened in the period, in a repo one sees, linked to no Jira issue: the task cannot
    be told (an MR is expected to name its issue when Jira is the task source)."""
    lo, hi = _bounds(IssueFilter(start=start, end=end))
    where: list[ColumnElement[bool]] = [
        repo_scope(principal),
        ~exists().where(IssueMrLink.mr_id == MergeRequest.id),
    ]
    if business_project_id is not None:
        repo = aliased(Project)
        where.append(
            exists().where(
                repo.id == MergeRequest.project_id,
                repo.business_project_id == business_project_id,
            )
        )
    if lo is not None:
        where.append(MergeRequest.created_at_src >= lo)
    if hi is not None:
        where.append(MergeRequest.created_at_src < hi)
    total = int(session.scalar(select(func.count()).select_from(MergeRequest).where(*where)) or 0)
    rows = session.execute(
        select(MergeRequest, Project)
        .outerjoin(Project, Project.id == MergeRequest.project_id)
        .where(*where)
        .order_by(MergeRequest.created_at_src.desc().nulls_last(), MergeRequest.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    urls = _repo_urls(session, [r.Project for r in rows if r.Project is not None])
    reviews = latest_reviews(session, principal, [r.MergeRequest.id for r in rows])
    authors = _people(
        session,
        {r.MergeRequest.author_account_id for r in rows if r.MergeRequest.author_account_id},
    )
    items = []
    for r in rows:
        mr, repo = r.MergeRequest, r.Project
        base = urls.get(repo.id) if repo is not None else None
        items.append(
            {
                "id": mr.id,
                "iid": mr.iid,
                "title": mr.title,
                "state": mr.state,
                "repo": repo.path_with_namespace if repo is not None else None,
                "url": f"{base}/-/merge_requests/{mr.iid}" if base else None,
                "branch": mr.source_branch,
                "author": authors.get(mr.author_account_id or 0),
                "created_at": mr.created_at_src,
                "merged_at": mr.merged_at,
                # Key-looking tokens it has but no synced issue matches (another Jira project,
                # a typo, not synced yet).
                "keys": list(mr.jira_keys or [])[:5],
                "review": reviews.get(mr.id),
            }
        )
    return {"items": items, "total": total}


def unlinked_commits(
    session: Session,
    principal: Principal,
    business_project_id: int | None,
    start: date | None,
    end: date | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Commits authored in the period, in a repo one sees, whose message names no synced Jira
    issue (merge commits aside), with how many of the period's commits follow the standard."""
    lo, hi = _bounds(IssueFilter(start=start, end=end))
    period: list[ColumnElement[bool]] = [commit_scope(principal), Commit.is_merge.is_not(True)]
    if business_project_id is not None:
        repo = aliased(Project)
        period.append(
            exists().where(
                repo.id == Commit.project_id, repo.business_project_id == business_project_id
            )
        )
    if lo is not None:
        period.append(Commit.authored_at >= lo)
    if hi is not None:
        period.append(Commit.authored_at < hi)
    linked = exists().where(IssueCommitLink.sha == Commit.sha)
    in_period, with_issue = session.execute(
        select(func.count(), func.count().filter(linked)).select_from(Commit).where(*period)
    ).one()
    where = [*period, ~linked]
    rows = session.execute(
        select(Commit, Project)
        .outerjoin(Project, Project.id == Commit.project_id)
        .where(*where)
        .order_by(Commit.authored_at.desc().nulls_last(), Commit.sha)
        .offset(offset)
        .limit(limit)
    ).all()
    shas = [r.Commit.sha for r in rows]
    # In an MR that names an issue: the work is traceable, only the commit lacks the key.
    via_mr = (
        set(
            session.scalars(
                select(MrCommit.sha)
                .join(IssueMrLink, IssueMrLink.mr_id == MrCommit.mr_id)
                .where(MrCommit.sha.in_(shas))
            )
        )
        if shas
        else set()
    )
    urls = _repo_urls(session, [r.Project for r in rows if r.Project is not None])
    reviews = commit_reviews(session, principal, shas)
    registered = set(session.scalars(select(JiraProject.key)))
    authors = _people(
        session, {r.Commit.author_account_id for r in rows if r.Commit.author_account_id}
    )
    items = []
    for r in rows:
        c, repo = r.Commit, r.Project
        base = urls.get(repo.id) if repo is not None else None
        items.append(
            {
                "sha": c.sha,
                "title": (c.message or "").split("\n", 1)[0][:300],
                "repo": repo.path_with_namespace if repo is not None else None,
                "url": f"{base}/-/commit/{c.sha}" if base else None,
                "author": authors.get(c.author_account_id or 0),
                "authored_at": c.authored_at,
                "keys": [k for k in c.jira_keys or [] if k.rsplit("-", 1)[0] in registered][:5],
                "in_linked_mr": c.sha in via_mr,
                "review": reviews.get(c.sha),
            }
        )
    return {
        "items": items,
        "total": int(in_period) - int(with_issue),
        "commits_in_period": int(in_period),
        "with_issue": int(with_issue),
    }


# -- what the filters offer ------------------------------------------------------------------------


def options(session: Session, principal: Principal) -> dict[str, Any]:
    bps = seen_projects(principal)
    jira_where: list[ColumnElement[bool]] = [JiraProject.enabled.is_(True)]
    if bps is not None:
        jira_where.append(projects_in(bps) if bps else false())
    projects = session.execute(
        select(JiraProject.key, JiraProject.name).where(*jira_where).order_by(JiraProject.key)
    ).all()
    from app.models import JiraProjectScope

    bp_where: list[ColumnElement[bool]] = [
        exists().where(JiraProjectScope.business_project_id == BusinessProject.id)
    ]
    if bps is not None:
        bp_where.append(BusinessProject.id.in_(bps) if bps else false())
    business = session.execute(
        select(BusinessProject.id, BusinessProject.name)
        .where(*bp_where)
        .order_by(BusinessProject.name)
    ).all()
    types = session.scalars(
        select(Issue.type)
        .where(Issue.source == "jira", Issue.type.is_not(None), issue_scope(principal))
        .distinct()
        .order_by(Issue.type)
        .limit(50)
    ).all()
    return {
        "jira_projects": [{"key": k, "name": n} for k, n in projects],
        "business_projects": [{"id": i, "name": n} for i, n in business],
        "types": [str(t) for t in types],
    }
