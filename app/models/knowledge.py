"""Review knowledge (docs/specs/19 §3.4): the compiled premises a review of a repo starts from.

One row per build; the previous active row of the same scope becomes `superseded`, never
deleted. `data` is the schema of app/review/knowledge.py (`Knowledge`), `sources` the shas it
was built from (the repo's `.claude` tree, the shared .claude repo, the default branch head),
so a review can tell when the premises are older than the configuration.

A build that fails after the model was paid leaves a `failed` row whose `data.meta` holds
`cost_usd` and `error`, so the month's spend stays complete and a repo whose build just failed
is not rebuilt on every review. Versions are unique per scope and at most one row per scope is
active (partial unique indexes), so two builds of the same repo cannot both win.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SCOPES = ("project", "business_project")
STATUSES = ("active", "superseded", "failed")
BUILT_BY = ("manual", "auto", "first_review")
EDIT_ACTIONS = ("disable", "set_severity", "reword", "add_invariant")


class ReviewKnowledge(Base):
    __tablename__ = "review_knowledge"
    __table_args__ = (
        CheckConstraint("scope IN ('project','business_project')", name="scope"),
        CheckConstraint("status IN ('active','superseded','failed')", name="status"),
        UniqueConstraint(
            "scope", "project_id", "version", name="uq_review_knowledge_project_version"
        ),
        UniqueConstraint(
            "scope",
            "business_project_id",
            "version",
            name="uq_review_knowledge_business_project_version",
        ),
        Index("ix_review_knowledge_scope_project_id_status", "scope", "project_id", "status"),
        Index(
            "ix_review_knowledge_scope_business_project_id_status",
            "scope",
            "business_project_id",
            "status",
        ),
        Index(
            "uq_review_knowledge_active_project",
            "scope",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),  # unit tests run the store on SQLite
        ),
        Index(
            "uq_review_knowledge_active_business_project",
            "scope",
            "business_project_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope: Mapped[str]  # project | business_project
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    business_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE")
    )
    version: Mapped[int]  # increasing within the scope
    schema_version: Mapped[int] = mapped_column(server_default="1")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sources: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(server_default="active")  # active|superseded|failed
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    built_by: Mapped[str] = mapped_column(server_default="manual")  # manual|auto|first_review
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewKnowledgeEdit(Base):
    """A lead's manual change to one item, kept across builds while the item id exists."""

    __tablename__ = "review_knowledge_edits"
    __table_args__ = (
        CheckConstraint("scope IN ('project','business_project')", name="scope"),
        CheckConstraint(
            "action IN ('disable','set_severity','reword','add_invariant')", name="action"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope: Mapped[str]
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    business_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str]
    action: Mapped[str]  # disable | set_severity | reword | add_invariant
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
