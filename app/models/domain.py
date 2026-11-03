from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Commit(TimestampMixin, Base):
    __tablename__ = "commits"
    __table_args__ = (
        Index("ix_commits_project_id_authored_at", "project_id", "authored_at"),
        Index("ix_commits_author_account_id", "author_account_id"),
    )

    sha: Mapped[str] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    author_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None]
    additions: Mapped[int | None]
    deletions: Mapped[int | None]
    files_changed: Mapped[int | None]
    is_merge: Mapped[bool | None]


class MergeRequest(TimestampMixin, Base):
    __tablename__ = "merge_requests"
    __table_args__ = (
        UniqueConstraint("project_id", "iid"),
        Index("ix_merge_requests_project_id_merged_at", "project_id", "merged_at"),
        Index("ix_merge_requests_author_account_id", "author_account_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    iid: Mapped[int]
    title: Mapped[str | None]
    author_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    state: Mapped[str | None]
    source_branch: Mapped[str | None]
    target_branch: Mapped[str | None]
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    additions: Mapped[int | None]
    deletions: Mapped[int | None]
    review_rounds: Mapped[int | None]
    head_sha: Mapped[str | None]


class MrCommit(TimestampMixin, Base):
    __tablename__ = "mr_commits"

    mr_id: Mapped[int] = mapped_column(ForeignKey("merge_requests.id"), primary_key=True)
    sha: Mapped[str] = mapped_column(ForeignKey("commits.sha"), primary_key=True, index=True)


class MrNote(TimestampMixin, Base):
    __tablename__ = "mr_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    mr_id: Mapped[int | None] = mapped_column(ForeignKey("merge_requests.id"), index=True)
    author_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    body: Mapped[str | None]
    is_system: Mapped[bool | None]
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Pipeline(TimestampMixin, Base):
    __tablename__ = "pipelines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    sha: Mapped[str | None]
    ref: Mapped[str | None]
    status: Mapped[str | None]
    duration_sec: Mapped[int | None]
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommitFile(TimestampMixin, Base):
    """Paths a commit changed, with line counts, after IGNORE_PATHS (docs/specs/20 §5.1): the
    input of knowledge risk. Never the content (docs/specs/AGENTS.md)."""

    __tablename__ = "commit_files"

    sha: Mapped[str] = mapped_column(
        ForeignKey("commits.sha", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(primary_key=True, index=True)
    additions: Mapped[int] = mapped_column(server_default="0")
    deletions: Mapped[int] = mapped_column(server_default="0")


class Deployment(TimestampMixin, Base):
    """A GitLab deployment (docs/specs/20 §5.2): DORA reads the production ones (D2)."""

    __tablename__ = "deployments"
    __table_args__ = (
        Index("ix_deployments_project_id_finished_at_src", "project_id", "finished_at_src"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment: Mapped[str]
    environment_tier: Mapped[str | None]  # GitLab's tier: production|staging|testing|...
    sha: Mapped[str | None]
    ref: Mapped[str | None]
    status: Mapped[str]  # created|running|success|failed|canceled|blocked
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The MRs (iids in this repo) GitLab says the deployment shipped; resolved to MRs when
    # read, so an MR collected later still links. NULL = not asked (not a success, or not
    # production when collected).
    mr_iids: Mapped[list[int] | None] = mapped_column(JSONB)


class Incident(TimestampMixin, Base):
    """A GitLab issue of type incident, or carrying an INCIDENT_LABELS label (docs/specs/20
    §5.3, D3). DORA reads them at team level only."""

    __tablename__ = "incidents"
    __table_args__ = (UniqueConstraint("project_id", "iid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    iid: Mapped[int]
    title: Mapped[str | None]
    state: Mapped[str | None]  # opened|closed
    labels: Mapped[list[str] | None] = mapped_column(JSONB)
    web_url: Mapped[str | None]
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    closed_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # MRs (iids) GitLab says close the incident: its fix. NULL = not asked (still open).
    fix_mr_iids: Mapped[list[int] | None] = mapped_column(JSONB)
