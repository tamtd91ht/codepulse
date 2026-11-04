"""P0 data readiness (docs/specs/25 §0.2, §10): is each tracked repo giving the DPI what it
needs? Changes through MRs, pipelines with coverage, issues with complexity, a criteria set on
its dự án, a protected default branch, the standard labels."""

from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabError
from app.collector.sync import client_factory
from app.crypto import secret_box
from app.dpi.labels import STANDARD
from app.models import (
    BusinessProject,
    Commit,
    Connection,
    Issue,
    MergeRequest,
    MrCommit,
    Pipeline,
    Project,
    ProjectCriteriaSet,
)


def _share(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def repo_readiness(session: Session, since: datetime) -> dict[str, Any]:
    repos = session.scalars(
        select(Project).where(Project.tracked.is_(True)).order_by(Project.path_with_namespace)
    ).all()
    bps = {b.id: b.name for b in session.scalars(select(BusinessProject))}
    with_sets = set(session.scalars(select(ProjectCriteriaSet.business_project_id).distinct()))
    in_mr = select(MrCommit.sha).where(MrCommit.sha == Commit.sha)
    made_by_mr = select(MergeRequest.id).where(
        or_(
            MergeRequest.merge_commit_sha == Commit.sha,
            MergeRequest.squash_commit_sha == Commit.sha,
        )
    )
    rows: list[dict[str, Any]] = []
    for repo in repos:
        commits = (
            session.scalar(
                select(func.count()).where(
                    Commit.project_id == repo.id,
                    Commit.is_merge.is_not(True),
                    Commit.authored_at >= since,
                )
            )
            or 0
        )
        through_mr = (
            session.scalar(
                select(func.count()).where(
                    Commit.project_id == repo.id,
                    Commit.is_merge.is_not(True),
                    Commit.authored_at >= since,
                    exists(in_mr) | exists(made_by_mr),
                )
            )
            or 0
        )
        merged = (
            session.scalar(
                select(func.count()).where(
                    MergeRequest.project_id == repo.id,
                    MergeRequest.state == "merged",
                    MergeRequest.merged_at >= since,
                )
            )
            or 0
        )
        with_checklist = (
            session.scalar(
                select(func.count()).where(
                    MergeRequest.project_id == repo.id,
                    MergeRequest.state == "merged",
                    MergeRequest.merged_at >= since,
                    MergeRequest.tasks_total > 0,
                )
            )
            or 0
        )
        pipelines = (
            session.scalar(
                select(func.count()).where(
                    Pipeline.project_id == repo.id, Pipeline.created_at_src >= since
                )
            )
            or 0
        )
        passed = (
            session.scalar(
                select(func.count()).where(
                    Pipeline.project_id == repo.id,
                    Pipeline.created_at_src >= since,
                    Pipeline.status == "success",
                )
            )
            or 0
        )
        covered = (
            session.scalar(
                select(func.count()).where(
                    Pipeline.project_id == repo.id,
                    Pipeline.created_at_src >= since,
                    Pipeline.coverage.is_not(None),
                )
            )
            or 0
        )
        avg_coverage = session.scalar(
            select(func.avg(Pipeline.coverage)).where(
                Pipeline.project_id == repo.id,
                Pipeline.created_at_src >= since,
                Pipeline.coverage.is_not(None),
            )
        )
        closed = (
            session.scalar(
                select(func.count()).where(
                    Issue.project_id == repo.id, Issue.closed_at_src >= since
                )
            )
            or 0
        )
        pointed = (
            session.scalar(
                select(func.count()).where(
                    Issue.project_id == repo.id,
                    Issue.closed_at_src >= since,
                    Issue.complexity_points.is_not(None),
                )
            )
            or 0
        )
        rows.append(
            {
                "project": {"id": repo.id, "path": repo.path_with_namespace},
                "business_project": bps.get(repo.business_project_id or 0),
                "criteria_assigned": repo.business_project_id in with_sets,
                "commits": commits,
                "through_mr_pct": _share(through_mr, commits),
                "merged_mrs": merged,
                "checklist_pct": _share(with_checklist, merged),
                "pipelines": pipelines,
                "pipeline_pass_pct": _share(passed, pipelines),
                "coverage_pct": _share(covered, pipelines),
                "avg_coverage": round(float(avg_coverage), 1) if avg_coverage is not None else None,
                "issues_closed": closed,
                "complexity_pct": _share(pointed, closed),
            }
        )
    total = len(rows) or 1
    commits_all = sum(r["commits"] for r in rows)
    through_all = sum(
        round((r["through_mr_pct"] or 0) * r["commits"] / 100) for r in rows if r["commits"]
    )
    closed_all = sum(r["issues_closed"] for r in rows)
    pointed_all = sum(
        round((r["complexity_pct"] or 0) * r["issues_closed"] / 100)
        for r in rows
        if r["issues_closed"]
    )
    return {
        "repos": rows,
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
