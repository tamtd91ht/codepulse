"""P0 data readiness (docs/specs/25 §0.2, §10): is each tracked repo giving the DPI what it
needs? Changes through MRs, pipelines with coverage, issues with complexity, a criteria set on
its dự án, a protected default branch, the standard labels."""

from datetime import datetime
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabError
from app.collector.sync import client_factory
from app.crypto import secret_box
from app.dpi.facts import counted_task
from app.dpi.labels import STANDARD
from app.models import (
    Account,
    BusinessProject,
    Commit,
    Connection,
    Issue,
    JiraProject,
    MergeRequest,
    MrCommit,
    Pipeline,
    Project,
    ProjectCriteriaSet,
)


def _share(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def _per_repo(session: Session, query: Any) -> dict[int, Any]:
    """{project id: value} of a `select(project_id, aggregate) … group_by(project_id)`."""
    return {int(pid): value for pid, value in session.execute(query) if pid is not None}


OVERSIZED_POINTS = 8  # Story Points above this count as 8: "nên tách issue" (docs/specs/27 §6)


def jira_readiness(
    session: Session, since: datetime, visible: set[int] | None = None
) -> dict[str, Any]:
    """docs/specs/27 §12: per registered Jira project (those of the dự án one sees; one
    without a dự án: admins only, `visible` None), the issues closed since `since` (sub-tasks
    without points left out, as in the DPI), how many have Story Points, how many are over
    8 points; and how many of their assignees are linked to a person."""
    query = select(JiraProject).order_by(JiraProject.key)
    if visible is not None:
        query = query.where(JiraProject.business_project_id.in_(visible or {0}))
    projects = session.scalars(query).all()
    bps = {b.id: b.name for b in session.scalars(select(BusinessProject))}
    ids = [p.id for p in projects] or [0]
    closed = and_(
        Issue.source == "jira",
        Issue.jira_project_id.in_(ids),
        Issue.closed_at_src >= since,
        counted_task(),
    )
    stats = {
        int(pid): (int(n), int(c), int(big))
        for pid, n, c, big in session.execute(
            select(
                Issue.jira_project_id,
                func.count(),
                func.count().filter(Issue.complexity_points.is_not(None)),
                func.count().filter(Issue.story_points > OVERSIZED_POINTS),
            )
            .where(closed)
            .group_by(Issue.jira_project_id)
        )
        if pid is not None
    }
    assignees: set[int] = set()
    for (accounts,) in session.execute(select(Issue.assignee_account_ids).where(closed)):
        assignees |= {int(a) for a in accounts or []}
    linked = (
        int(
            session.scalar(
                select(func.count())
                .select_from(Account)
                .where(Account.id.in_(assignees), Account.user_id.is_not(None))
            )
            or 0
        )
        if assignees
        else 0
    )
    rows = []
    for p in projects:
        n, pointed, big = stats.get(p.id, (0, 0, 0))
        bp = p.business_project_id
        rows.append(
            {
                "key": p.key,
                "name": p.name,
                "enabled": p.enabled,
                "business_project": {"id": bp, "name": bps.get(bp)} if bp in bps else None,
                "issues_closed": n,
                "pointed": pointed,
                "story_points_pct": _share(pointed, n),
                "oversized": big,
                "last_synced_at": p.last_synced_at,
                "last_error": p.last_error,
            }
        )
    return {"projects": rows, "assignees": {"total": len(assignees), "linked": linked}}


def repo_readiness(
    session: Session,
    since: datetime,
    source: str = "gitlab",
    visible: set[int] | None = None,
) -> dict[str, Any]:
    """`source`: the DPI task source (docs/specs/27 §9). Jira: the per-repo issue columns are
    null and a `jira` block (`jira_readiness`, by `visible`) holds the issue readiness; the
    company-wide complexity criterion is computed over it."""
    repos = session.scalars(
        select(Project).where(Project.tracked.is_(True)).order_by(Project.path_with_namespace)
    ).all()
    ids = [r.id for r in repos] or [0]
    bps = {b.id: b.name for b in session.scalars(select(BusinessProject))}
    with_sets = set(session.scalars(select(ProjectCriteriaSet.business_project_id).distinct()))
    in_mr = select(MrCommit.sha).where(MrCommit.sha == Commit.sha)
    made_by_mr = select(MergeRequest.id).where(
        or_(
            MergeRequest.merge_commit_sha == Commit.sha,
            MergeRequest.squash_commit_sha == Commit.sha,
        )
    )
    recent = and_(
        Commit.project_id.in_(ids), Commit.is_merge.is_not(True), Commit.authored_at >= since
    )
    commits = _per_repo(
        session,
        select(
            Commit.project_id,
            func.count(),
        )
        .where(recent)
        .group_by(Commit.project_id),
    )
    through = _per_repo(
        session,
        select(Commit.project_id, func.count())
        .where(recent, exists(in_mr) | exists(made_by_mr))
        .group_by(Commit.project_id),
    )
    merged_rows = session.execute(
        select(
            MergeRequest.project_id,
            func.count(),
            func.count().filter(MergeRequest.tasks_total > 0),
        )
        .where(
            MergeRequest.project_id.in_(ids),
            MergeRequest.state == "merged",
            MergeRequest.merged_at >= since,
        )
        .group_by(MergeRequest.project_id)
    ).all()
    merged = {int(p): (int(n), int(t)) for p, n, t in merged_rows if p is not None}
    pipe_rows = session.execute(
        select(
            Pipeline.project_id,
            func.count(),
            func.count().filter(Pipeline.status == "success"),
            func.count().filter(Pipeline.coverage.is_not(None)),
            func.avg(Pipeline.coverage),
        )
        .where(Pipeline.project_id.in_(ids), Pipeline.created_at_src >= since)
        .group_by(Pipeline.project_id)
    ).all()
    pipes = {int(p): (int(n), int(ok), int(cov), avg) for p, n, ok, cov, avg in pipe_rows if p}
    issue_rows = session.execute(
        select(
            Issue.project_id,
            func.count(),
            func.count().filter(Issue.complexity_points.is_not(None)),
        )
        .where(Issue.source == "gitlab", Issue.project_id.in_(ids), Issue.closed_at_src >= since)
        .group_by(Issue.project_id)
    ).all()
    issues = {int(p): (int(n), int(c)) for p, n, c in issue_rows if p is not None}
    rows: list[dict[str, Any]] = []
    for repo in repos:
        n_commits = int(commits.get(repo.id, 0))
        n_merged, n_checklist = merged.get(repo.id, (0, 0))
        n_pipes, n_ok, n_cov, avg_coverage = pipes.get(repo.id, (0, 0, 0, None))
        n_closed, n_pointed = issues.get(repo.id, (0, 0))
        rows.append(
            {
                "project": {"id": repo.id, "path": repo.path_with_namespace},
                "business_project": bps.get(repo.business_project_id or 0),
                "criteria_assigned": repo.business_project_id in with_sets,
                "commits": n_commits,
                "through_mr_pct": _share(int(through.get(repo.id, 0)), n_commits),
                "merged_mrs": n_merged,
                "checklist_pct": _share(n_checklist, n_merged),
                "pipelines": n_pipes,
                "pipeline_pass_pct": _share(n_ok, n_pipes),
                "coverage_pct": _share(n_cov, n_pipes),
                "avg_coverage": round(float(avg_coverage), 1) if avg_coverage is not None else None,
                "issues_closed": n_closed if source == "gitlab" else None,
                "complexity_pct": _share(n_pointed, n_closed) if source == "gitlab" else None,
            }
        )
    total = len(rows) or 1
    commits_all = sum(r["commits"] for r in rows)
    through_all = sum(
        round((r["through_mr_pct"] or 0) * r["commits"] / 100) for r in rows if r["commits"]
    )
    jira = jira_readiness(session, since, visible) if source == "jira" else None
    if jira is not None:
        closed_all = sum(p["issues_closed"] for p in jira["projects"])
        pointed_all = sum(p["pointed"] for p in jira["projects"])
    else:
        closed_all = sum(n for n, _c in issues.values())
        pointed_all = sum(c for _n, c in issues.values())
    return {
        "task_source": source,
        "repos": rows,
        "jira": jira,
        # §10 acceptance 1-3.
        "acceptance": {
            "through_mr_pct": _share(through_all, commits_all),
            "repos_with_pipeline_pct": round(
                100.0 * sum(1 for r in rows if r["pipelines"] and r["coverage_pct"]) / total, 1
            ),
            "issues_with_complexity_pct": _share(pointed_all, closed_all),
            "targets": {
                "through_mr_pct": 80,
                "repos_with_pipeline_pct": 90,
                "issues_with_complexity_pct": 70,
            },
        },
    }


def _client(session: Session, project: Project) -> Any:
    connection = session.get(Connection, project.connection_id) if project.connection_id else None
    if connection is None:
        return None
    return client_factory(secret_box())(connection)


def gitlab_checks(session: Session, project_id: int) -> dict[str, Any]:
    project = session.get(Project, project_id)
    if project is None:
        return {"error": "repo không tồn tại"}
    client = _client(session, project)
    if client is None:
        return {"error": "repo không có kết nối GitLab"}
    out: dict[str, Any] = {"default_branch": project.default_branch}
    try:
        with client:
            protected = [b.get("name") for b in client.protected_branches(project.external_id)]
            names = {str(label.get("name")) for label in client.labels(project.external_id)}
    except GitLabError as exc:
        return {"error": f"GitLab trả lỗi {exc.status_code}"}
    branch = project.default_branch or "main"
    out["protected_branches"] = protected
    out["default_protected"] = any(p == branch or p in ("*", f"{branch}*") for p in protected)
    out["missing_labels"] = sorted(set(STANDARD) - names)
    return out


def ensure_labels(session: Session, project_id: int) -> dict[str, Any]:
    project = session.get(Project, project_id)
    if project is None:
        return {"error": "repo không tồn tại"}
    client = _client(session, project)
    if client is None:
        return {"error": "repo không có kết nối GitLab"}
    created: list[str] = []
    try:
        with client:
            names = {str(label.get("name")) for label in client.labels(project.external_id)}
            for name, (color, description) in STANDARD.items():
                if name in names:
                    continue
                client.create_label(project.external_id, name, color, description)
                created.append(name)
    except GitLabError as exc:
        hint = " (token cần quyền api)" if exc.status_code in (401, 403) else ""
        return {"created": created, "error": f"GitLab trả lỗi {exc.status_code}{hint}"}
    return {"created": created}


MR_TEMPLATE_PATH = ".gitlab/merge_request_templates/Default.md"
MR_TEMPLATE = """Closes #

## Checklist
- [ ] Đã liên kết issue
- [ ] Có test cho thay đổi
- [ ] Migration (nếu có) chạy được hai chiều
- [ ] Có cách rollback
"""


def protect_default_branch(session: Session, project_id: int) -> dict[str, Any]:
    """P0.1: the default branch protected: nobody pushes, maintainers merge (MRs only)."""
    project = session.get(Project, project_id)
    if project is None:
        return {"error": "repo không tồn tại"}
    client = _client(session, project)
    if client is None:
        return {"error": "repo không có kết nối GitLab"}
    branch = project.default_branch or "main"
    try:
        with client:
            existing = [b.get("name") for b in client.protected_branches(project.external_id)]
            if branch in existing:
                client.unprotect_branch(project.external_id, branch)
            client.protect_branch(
                project.external_id, branch, push_access_level=0, merge_access_level=40
            )
    except GitLabError as exc:
        hint = (
            " (token cần quyền api và vai trò Maintainer)" if exc.status_code in (401, 403) else ""
        )
        return {"error": f"GitLab trả lỗi {exc.status_code}{hint}"}
    return {"protected": branch}


def add_mr_template(session: Session, project_id: int) -> dict[str, Any]:
    """P0.5: the MR template with the issue link and the checklist, committed to the default
    branch (left alone when the repo already has one)."""
    project = session.get(Project, project_id)
    if project is None:
        return {"error": "repo không tồn tại"}
    client = _client(session, project)
    if client is None:
        return {"error": "repo không có kết nối GitLab"}
    branch = project.default_branch or "main"
    try:
        with client:
            if client.file_exists(project.external_id, MR_TEMPLATE_PATH, branch):
                return {"exists": True}
            client.create_file(
                project.external_id,
                MR_TEMPLATE_PATH,
                branch,
                MR_TEMPLATE,
                "chore: mẫu merge request có checklist (CodePulse DPI)",
            )
    except GitLabError as exc:
        hint = (
            " (nhánh có bảo vệ: thêm qua MR, hoặc token cần quyền api)"
            if exc.status_code in (400, 401, 403)
            else ""
        )
        return {"error": f"GitLab trả lỗi {exc.status_code}{hint}"}
    return {"created": MR_TEMPLATE_PATH}
