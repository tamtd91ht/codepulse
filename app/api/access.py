"""Dự án grants applied to queries and lookups (docs/specs/09 "Phân quyền theo dự án").

The rules themselves live on `Principal` (app/api/security.py); this module turns them into SQL
for lists and into 404/403 for single repos, so every router hides the same things.
"""

from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import ColumnElement, and_, exists, or_
from sqlalchemy.orm import Session, aliased

from app.api.security import Principal
from app.models import Commit, MergeRequest, Project, Review


def reviews_in_projects(bp_ids: Iterable[int]) -> ColumnElement[bool]:
    """Reviews (of an MR or of a commit) in a repo of the dự án `bp_ids`. Correlated EXISTS on
    aliases: PostgreSQL turns each into a semi-join, where an IN under OR would materialise
    every sha of the dự án per request, and the aliases never bind to the outer query's
    tables."""
    bps = list(bp_ids)
    mr, commit, repo = aliased(MergeRequest), aliased(Commit), aliased(Project)
    by_mr = exists().where(
        mr.id == Review.mr_id, repo.id == mr.project_id, repo.business_project_id.in_(bps)
    )
    by_commit = exists().where(
        commit.sha == Review.commit_sha,
        repo.id == commit.project_id,
        repo.business_project_id.in_(bps),
    )
    return or_(by_mr, by_commit)


def review_scope(principal: Principal) -> ColumnElement[bool] | None:
    """`Principal.can_see_work` as a filter on `reviews`; None when nothing is hidden."""
    if principal.is_admin:
        return None
    allowed: list[ColumnElement[bool]] = [Review.author_user_id == principal.user_id]
    managed = principal.managed_projects() or set()
    if managed:
        allowed.append(reviews_in_projects(managed))
    viewed = (principal.visible_projects() or set()) - managed
    led = principal.lead_members - {principal.user_id}
    if viewed and led:
        allowed.append(and_(Review.author_user_id.in_(led), reviews_in_projects(viewed)))
    return or_(*allowed)


def review_repo(session: Session, review: Review) -> Project | None:
    """The repo of a review's MR or commit."""
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        return session.get(Project, mr.project_id) if mr else None
    commit = session.get(Commit, review.commit_sha) if review.commit_sha else None
    return session.get(Project, commit.project_id) if commit and commit.project_id else None


def visible_repo(session: Session, principal: Principal, project_id: int) -> Project:
    """A tracked repo of a dự án the person sees; anything else is "not found"."""
    project = session.get(Project, project_id)
    if (
        project is None
        or not project.tracked
        or not principal.can_view_project(project.business_project_id)
    ):
        raise HTTPException(404, "repo not found")
    return project


def require_manage(principal: Principal, bp_id: int | None) -> None:
    if not principal.can_manage_project(bp_id):
        raise HTTPException(403, "only a manager of this dự án or an admin can do this")
