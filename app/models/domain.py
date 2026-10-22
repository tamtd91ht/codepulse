from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Commit(TimestampMixin, Base):
    __tablename__ = "commits"
    __table_args__ = (Index("ix_commits_project_id_authored_at", "project_id", "authored_at"),)

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
    sha: Mapped[str] = mapped_column(ForeignKey("commits.sha"), primary_key=True)


class MrNote(TimestampMixin, Base):
    __tablename__ = "mr_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    mr_id: Mapped[int | None] = mapped_column(ForeignKey("merge_requests.id"))
    author_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    body: Mapped[str | None]
    is_system: Mapped[bool | None]
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Pipeline(TimestampMixin, Base):
    __tablename__ = "pipelines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    sha: Mapped[str | None]
    ref: Mapped[str | None]
    status: Mapped[str | None]
    duration_sec: Mapped[int | None]
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
