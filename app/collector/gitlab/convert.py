"""Convert step: GitLab records -> source-independent domain tables. Upserts only, so re-runs
never duplicate rows."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.collector.gitlab.diffstat import is_ignored
from app.collector.gitlab.extract import (
    Extracted,
    GlCommit,
    GlFileStat,
    GlUser,
    first_review_at,
    review_rounds,
)
from app.models import (
    Account,
    Commit,
    MergeRequest,
    MrCommit,
    MrNote,
    Pipeline,
    Project,
    RawGitlab,
)


@dataclass
class ConvertResult:
    counts: dict[str, int] = field(default_factory=dict)


def _loc(files: list[GlFileStat], ignore: list[str]) -> tuple[int, int, int]:
    kept = [f for f in files if not is_ignored(f.path, ignore)]
    return sum(f.additions for f in kept), sum(f.deletions for f in kept), len(kept)


class Converter:
    def __init__(self, session: Session, project: Project, ignore_paths: list[str]) -> None:
        self._session = session
        self._project = project
        self._ignore = ignore_paths
        self._accounts: dict[tuple[str, str], int] = {}

    # -- accounts ----------------------------------------------------------------------------

    def account_id(self, kind: str, external_key: str, display_name: str | None) -> int:
        key = (kind, external_key)
        if key not in self._accounts:
            stmt = (
                insert(Account)
                .values(kind=kind, external_key=external_key, display_name=display_name)
                .on_conflict_do_update(
                    index_elements=["kind", "external_key"],
                    set_={
                        "display_name": func.coalesce(
                            insert(Account).excluded.display_name, Account.display_name
                        ),
                        "updated_at": func.now(),
                    },
                )
                .returning(Account.id)
            )
            self._accounts[key] = self._session.execute(stmt).scalar_one()
        return self._accounts[key]

    def gitlab_account(self, user: GlUser) -> int:
        return self.account_id("gitlab_user", str(user.id), user.username)

    def email_account(self, email: str, name: str | None) -> int | None:
        return self.account_id("git_email", email, name) if email else None

    # -- domain rows -------------------------------------------------------------------------

    def _upsert(self, model: Any, values: dict[str, Any], keys: list[str]) -> Any:
        stmt = insert(model).values(**values)
        updates: dict[str, Any] = {k: stmt.excluded[k] for k in values if k not in keys}
        updates["updated_at"] = func.now()
        return stmt.on_conflict_do_update(index_elements=keys, set_=updates)

    def commit(self, commit: GlCommit, *, only_if_missing: bool = False) -> None:
        additions, deletions, files_changed = commit.additions, commit.deletions, None
        if commit.files is not None:
            additions, deletions, files_changed = _loc(commit.files, self._ignore)
        values = {
            "sha": commit.sha,
            "project_id": self._project.id,
            "author_account_id": self.email_account(commit.author_email, commit.author_name),
            "authored_at": commit.authored_at,
            "message": commit.message,
            "additions": additions,
            "deletions": deletions,
            "files_changed": files_changed,
            "is_merge": commit.is_merge,
        }
        if only_if_missing:
            # MR commit lists are thinner than the commits API; never overwrite a full row.
            stmt = insert(Commit).values(**values).on_conflict_do_nothing(index_elements=["sha"])
        else:
            stmt = self._upsert(Commit, values, ["sha"])
        self._session.execute(stmt)

    def run(self, data: Extracted) -> ConvertResult:
        result = ConvertResult()
        counts = result.counts
        if data.default_branch:
            self._project.default_branch = data.default_branch

        for commit in data.commits.values():
            self.commit(commit)
        counts["commits"] = len(data.commits)

        for mr in data.merge_requests.values():
            additions = deletions = None
            if mr.files is not None:
                additions, deletions, _ = _loc(mr.files, self._ignore)
            values = {
                "project_id": self._project.id,
                "iid": mr.iid,
                "title": mr.title,
                "author_account_id": self.gitlab_account(mr.author),
                "state": mr.state,
                "source_branch": mr.source_branch,
                "target_branch": mr.target_branch,
                "created_at_src": mr.created_at,
                "first_review_at": first_review_at(mr),
                "merged_at": mr.merged_at,
                "closed_at": mr.closed_at,
                "additions": additions,
                "deletions": deletions,
                "review_rounds": review_rounds(mr),
                "head_sha": mr.head_sha,
            }
            stmt = self._upsert(MergeRequest, values, ["project_id", "iid"])
            mr_id = self._session.execute(stmt.returning(MergeRequest.id)).scalar_one()

            for commit in mr.commits:
                self.commit(data.commits.get(commit.sha, commit), only_if_missing=True)
                self._session.execute(
                    insert(MrCommit)
                    .values(mr_id=mr_id, sha=commit.sha)
                    .on_conflict_do_nothing(index_elements=["mr_id", "sha"])
                )
            for note in mr.notes:
                note_values = {
                    "id": note.id,
                    "mr_id": mr_id,
                    "author_account_id": self.gitlab_account(note.author),
                    "body": note.body,
                    "is_system": note.system,
                    "created_at_src": note.created_at,
                }
                self._session.execute(self._upsert(MrNote, note_values, ["id"]))
        counts["merge_requests"] = len(data.merge_requests)

        for pipeline in data.pipelines.values():
            values = {
                "id": pipeline.id,
                "project_id": self._project.id,
                "sha": pipeline.sha,
                "ref": pipeline.ref,
                "status": pipeline.status,
                "duration_sec": pipeline.duration_sec,
                "created_at_src": pipeline.created_at,
                "finished_at_src": pipeline.finished_at,
            }
            self._session.execute(self._upsert(Pipeline, values, ["id"]))
        counts["pipelines"] = len(data.pipelines)
        self._session.flush()
        return result


def raw_rows_query(project: Project, fetched_since: datetime | None = None) -> Select[RawGitlab]:
    """Raw rows of a project in insertion order; `fetched_since` limits them to recent runs."""
    query = select(RawGitlab).where(RawGitlab.project_id == project.id).order_by(RawGitlab.id)
    if fetched_since is not None:
        query = query.where(RawGitlab.fetched_at >= fetched_since)
    return query
