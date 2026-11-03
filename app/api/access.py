"""Dự án grants applied to queries and lookups (docs/specs/09 "Phân quyền theo dự án").

The rules themselves live on `Principal` (app/api/security.py); this module turns them into SQL
for lists and into 404/403 for single repos, so every router hides the same things.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, Select, and_, exists, false, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.security import Principal
from app.models import Account, Commit, MergeRequest, Project, ProjectGrant, Review, User
from app.permissions.catalog import FLAG


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


# -- docs/specs/21 phase B1: reviews, findings, closings and disputes on permissions ---------------


def covers_work(
    principal: Principal, key: str, author_user_id: int | None, bp_id: int | None
) -> bool:
    """`Principal.covers` on a review, its findings or a commit in a repo of dự án `bp_id`:
    one's own work wherever it is; anyone else's only in a dự án one sees (repos outside every
    dự án are the admins', docs/specs/09)."""
    if principal.is_admin:
        return True
    others = author_user_id is None or author_user_id != principal.user_id
    if others and not principal.can_view_project(bp_id):
        return False
    return principal.covers(key, author_user_id, bp_id)


def sees_work(principal: Principal, author_user_id: int | None, bp_id: int | None) -> bool:
    """`review.view` on a review, its findings or a commit (was `Principal.can_see_work`)."""
    return covers_work(principal, "review.view", author_user_id, bp_id)


def may_request_review(principal: Principal, author_user_id: int | None, bp_id: int | None) -> bool:
    """`review.request` on a commit / MR of a dự án one sees (was `Principal.may_review_work`):
    one's own work; anyone else's, and work whose author is not mapped to a person, by the
    dự án-wide scopes only (the permission has no "team" scope: leading a team gives no review
    right)."""
    if not principal.can_view_project(bp_id):
        return False
    if author_user_id is not None and author_user_id == principal.user_id:
        return principal.can("review.request")
    return principal.covers("review.request", None, bp_id)


def may_rerun(principal: Principal, author_user_id: int | None, bp_id: int | None) -> bool:
    """`review.rerun` (and "review again" from Yêu cầu review): it spends LLM budget, so its
    scopes are "project" (a dự án one manages, the built-in Quản lý dự án) and "all"."""
    return principal.covers("review.rerun", author_user_id, bp_id)


def team_reach(principal: Principal, key: str) -> set[int]:
    """Other people whose work `key` covers through its "team" scope (or a wider one): those
    one leads or manages. Admins: use `covers_work`, they reach everyone."""
    if principal.scope(key) in (None, "own"):
        return set()
    return principal.team_people(key) - {principal.user_id}


def whole_projects(principal: Principal, key: str) -> set[int] | None:
    """Dự án in which `key` covers everyone's work: those one manages ("project"), every one
    one sees ("all"). None: every dự án (admin)."""
    if principal.is_admin:
        return None
    held = principal.scope(key)
    if held == "project":
        return principal.managed_projects() or set()
    if held in ("all", FLAG):
        return principal.visible_projects() or set()
    return set()


def work_scope(principal: Principal, key: str = "review.view") -> ColumnElement[bool] | None:
    """`covers_work(key, …)` as a filter on `reviews`; None when nothing is hidden."""
    if principal.is_admin:
        return None
    if principal.scope(key) is None:
        return false()
    allowed: list[ColumnElement[bool]] = [Review.author_user_id == principal.user_id]
    whole = whole_projects(principal, key) or set()
    if whole:
        allowed.append(reviews_in_projects(whole))
    viewed = (principal.visible_projects() or set()) - whole
    people = team_reach(principal, key)
    if viewed and people:
        allowed.append(and_(Review.author_user_id.in_(people), reviews_in_projects(viewed)))
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


def _people_of(bps: list[int]) -> list[Select[Any]]:
    """Who is part of dự án `bps`, three ways: granted on it, or the mapped author of a commit or
    an MR in one of its tracked repos (a review's person comes from that same account). Each
    is driven by an index (project_grants, commits / merge_requests by author account)."""
    in_repos = and_(Project.business_project_id.in_(bps), Project.tracked.is_(True))
    return [
        select(ProjectGrant.user_id).where(ProjectGrant.business_project_id.in_(bps)),
        select(Account.user_id)
        .join(Commit, Commit.author_account_id == Account.id)
        .join(Project, Project.id == Commit.project_id)
        .where(Account.user_id.is_not(None), in_repos),
        select(Account.user_id)
        .join(MergeRequest, MergeRequest.author_account_id == Account.id)
        .join(Project, Project.id == MergeRequest.project_id)
        .where(Account.user_id.is_not(None), in_repos),
    ]


def project_people(session: Session, bp_ids: Iterable[int]) -> set[int]:
    """Everyone who is part of dự án `bp_ids` (`_people_of`), for lists."""
    bps = list(bp_ids)
    if not bps:
        return set()
    granted, committed, opened = _people_of(bps)
    return {
        int(user_id)
        for user_id in session.scalars(granted.union(committed, opened))
        if user_id is not None
    }


def seen_people(session: Session, principal: Principal) -> set[int] | None:
    """Whose individual pages and evaluations `principal` may open: themselves, the people they
    lead, and everyone in a dự án they manage (2026-11-02: a manager of dự án A sees all of
    A). None: everyone (admin). Their numbers then only count work the viewer may see
    (`review_scope`)."""
    visible = principal.visible_users()
    if visible is None or principal.manages_all:
        return None  # admin, or "Quản lý tổng": everyone (numbers still by `review_scope`)
    managed = principal.managed_projects() or set()
    return visible | project_people(session, managed) if managed else visible


def may_see_person(session: Session, principal: Principal, user_id: int) -> bool:
    if principal.can_see_user(user_id) or principal.manages_all:
        return True
    managed = list(principal.managed_projects() or set())
    if not managed:
        return False
    granted, committed, opened = _people_of(managed)
    # One person: three EXISTS, each stops at the first row.
    return bool(
        session.scalar(
            select(
                or_(
                    exists(granted.where(ProjectGrant.user_id == user_id)),
                    exists(committed.where(Account.user_id == user_id)),
                    exists(opened.where(Account.user_id == user_id)),
                )
            )
        )
    )


def reviewable_people(
    session: Session, principal: Principal, bp_ids: Iterable[int] | None, since: datetime
) -> list[User]:
    """People whose work `principal` may ask a review for (`may_request_review`) in
    dự án `bp_ids` (None: every dự án), yourself first, then the most recently active.

    Not a manager there: only yourself. A manager: also everyone granted those dự án and every
    mapped author of a commit in their repos since `since` (who actually works there). Admin
    with no dự án given: every active person."""
    me = session.get(User, principal.user_id)
    if not principal.can("review.request"):
        return []
    # Where `review.request` covers everyone's work (`may_request_review`); None = every dự án.
    managed = whole_projects(principal, "review.request")
    scope: set[int] | None
    if bp_ids is None:
        scope = managed
    else:
        wanted = set(bp_ids)
        scope = wanted if managed is None else wanted & managed
    if scope is not None and not scope:
        return [me] if me is not None and me.active is not False else []
    authored = (
        select(Account.user_id.label("user_id"), func.max(Commit.authored_at).label("last"))
        .join(Commit, Commit.author_account_id == Account.id)
        .join(Project, Project.id == Commit.project_id)
        .where(Account.user_id.is_not(None), Commit.authored_at >= since)
    )
    if scope is not None:
        authored = authored.where(Project.business_project_id.in_(scope))
    active = authored.group_by(Account.user_id).subquery()
    query = (
        select(User)
        .outerjoin(active, active.c.user_id == User.id)
        .where(User.active.is_not(False))
        .order_by(
            (User.id == principal.user_id).desc(),
            active.c.last.desc().nulls_last(),
            User.full_name,
            User.id,
        )
    )
    if scope is not None:
        granted = select(ProjectGrant.user_id).where(ProjectGrant.business_project_id.in_(scope))
        query = query.where(
            or_(User.id == principal.user_id, User.id.in_(granted), active.c.user_id.is_not(None))
        )
    return list(session.scalars(query))
