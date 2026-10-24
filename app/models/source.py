from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Connection(TimestampMixin, Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    kind: Mapped[str] = mapped_column(server_default="gitlab")
    base_url: Mapped[str]
    token_encrypted: Mapped[str]
    webhook_secret_encrypted: Mapped[str | None]


class BusinessProject(TimestampMixin, Base):
    """A "dự án" defined by admins; it groups repos, possibly from several GitLab groups."""

    __tablename__ = "business_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    description: Mapped[str | None]
    # Team rules (markdown) put into every review prompt of this dự án (docs/specs/17).
    review_rules: Mapped[str | None]


class Project(TimestampMixin, Base):
    """A GitLab project, i.e. one git repo ("repo" in the dashboard)."""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id"))
    external_id: Mapped[int] = mapped_column(BigInteger)
    path_with_namespace: Mapped[str]
    default_branch: Mapped[str | None]
    review_enabled: Mapped[bool | None] = mapped_column(server_default="true")
    post_mr_comment: Mapped[bool | None] = mapped_column(server_default="false")
    allow_external_llm: Mapped[bool | None] = mapped_column(server_default="true")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    business_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_projects.id", ondelete="SET NULL")
    )
    # Untracked repos keep their history but are no longer synced, reviewed or fed by webhooks.
    tracked: Mapped[bool] = mapped_column(server_default="true")
    untracked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Repo-specific rules, and files inside the repo (comma list, read on the default branch).
    review_rules: Mapped[str | None]
    review_rules_paths: Mapped[str | None]
    # docs/specs/18: second pass with the repo open, and the cached project profile.
    deep_review: Mapped[bool] = mapped_column(server_default="true")
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    profile_sha: Mapped[str | None]
    profile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RawGitlab(TimestampMixin, Base):
    __tablename__ = "raw_gitlab"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    endpoint: Mapped[str]
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    payload: Mapped[Any] = mapped_column(JSONB)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
