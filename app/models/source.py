from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Project.review_branch value meaning "every branch" (docs/specs/17 §Nhánh review).
ALL_BRANCHES = "*"


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
    # Optional shared .claude repo (skills, agents, rules, CLAUDE.md for every repo of the dự án;
    # outside the spec, requested by the user, see PROGRESS.md). Read by the deep review at
    # `claude_repo_ref` (NULL = its default branch). `claude_repo_include`: extra globs,
    # relative to the .claude folder, of files to load besides the review-named ones.
    claude_repo_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL")
    )
    claude_repo_external_id: Mapped[int | None] = mapped_column(BigInteger)
    claude_repo_path: Mapped[str | None]
    claude_repo_ref: Mapped[str | None] = mapped_column(String(255))
    claude_repo_include: Mapped[str | None]


class ProjectGrant(TimestampMixin, Base):
    """A person's access to a dự án (outside the spec, requested by the user 2026-10-28; see
    docs/specs/09 "Phân quyền"): "view" shows it, "manage" also lets them request and re-run
    reviews and edit its review premises. ADMIN_EMAILS admins need no grant."""

    __tablename__ = "project_grants"
    __table_args__ = (CheckConstraint("role IN ('view','manage')", name="role"),)

    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str]
    granted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class Project(TimestampMixin, Base):
    """A GitLab project, i.e. one git repo ("repo" in the dashboard)."""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id"))
    external_id: Mapped[int] = mapped_column(BigInteger)
    path_with_namespace: Mapped[str]
    default_branch: Mapped[str | None]
    # docs/specs/17 §Nhánh review: branch for "recent commits" and push reviews; NULL = the
    # default branch, "*" = every branch.
    review_branch: Mapped[str | None] = mapped_column(String(255))
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
    # docs/specs/19 §3.4: a push to the default branch touching `.claude/` queues a rebuild of
    # the review knowledge (default off: the premises only get flagged as stale).
    knowledge_auto_refresh: Mapped[bool] = mapped_column(server_default="false")


class RawGitlab(TimestampMixin, Base):
    __tablename__ = "raw_gitlab"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    endpoint: Mapped[str]
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    payload: Mapped[Any] = mapped_column(JSONB)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
