"""Which screens Stetho must not read (app/assistant/screen_read.py), from the database.

`Project.allow_external_llm = false` is how CodePulse keeps a repo's code away from external LLMs
(the "Cấm" rule of docs/specs/AGENTS.md, whose `REVIEW_EXCLUDE_PROJECTS` became this per-repo
switch). Review and commit screens show code, so a screen belonging to such a repo is withheld
whole. Two independent checks, either one withholds:
  1. by the page's path: a review → its repo; a dự án → its repos (any closed one withholds);
  2. by the text read: a closed repo's path named anywhere on the screen.
"""

import re
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.screen_read import withheld_repos
from app.models import Commit, MergeRequest, Project, Review

_REVIEW = re.compile(r"^/reviews/(\d+)(?:/|$)")
_BUSINESS_PROJECT = re.compile(r"^/projects/(\d+)(?:/|$)")


def closed_repo_paths(session: Session) -> list[str]:
    """Repos whose code must not go to an external LLM."""
    rows = session.scalars(
        select(Project.path_with_namespace).where(Project.allow_external_llm.is_(False))
    )
    return [p for p in rows if p]


def _review_repo(session: Session, review_id: int) -> int | None:
    review = session.get(Review, review_id)
    if review is None:
        return None
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        return mr.project_id if mr else None
    if review.commit_sha is not None:
        commit = session.get(Commit, review.commit_sha)
        return commit.project_id if commit else None
    return None


def closed_by_path(session: Session, path: str) -> list[str]:
    """The closed repos the page at `path` belongs to (empty: none, or not a repo's page)."""
    repo_ids: list[int] = []
    if m := _REVIEW.match(path):
        if (repo := _review_repo(session, int(m.group(1)))) is not None:
            repo_ids = [repo]
    elif m := _BUSINESS_PROJECT.match(path):
        repo_ids = list(
            session.scalars(
                select(Project.id).where(Project.business_project_id == int(m.group(1)))
            )
        )
    if not repo_ids:
        return []
    rows = session.scalars(
        select(Project.path_with_namespace).where(
            Project.id.in_(repo_ids), Project.allow_external_llm.is_(False)
        )
    )
    return sorted(p or "?" for p in rows)


def withheld(session: Session, path: str | None, texts: Iterable[str]) -> list[str]:
    """Closed repos this screen shows, by its path or by its text; empty = may be read."""
    found = set(closed_by_path(session, path)) if path else set()
    found.update(withheld_repos(texts, closed_repo_paths(session)))
    return sorted(found)
