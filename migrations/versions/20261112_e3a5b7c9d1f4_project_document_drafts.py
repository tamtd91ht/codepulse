"""project_document_drafts + version source "link": Tổng hợp từ link (docs/specs/29 §12).

The LLM writes a draft from web pages / GitLab files; a person applies it as a new version
whose source is "link".

Revision ID: e3a5b7c9d1f4
Revises: d2f4a6b8c0e2
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3a5b7c9d1f4"
down_revision: str | None = "d2f4a6b8c0e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES = (
    "overview",
    "architecture",
    "setup",
    "api",
    "deploy",
    "runbook",
    "conventions",
    "decision",
    "other",
)
OLD_SOURCES = ("upload", "editor", "template", "restore")
SOURCES = (*OLD_SOURCES, "link")
MODES = ("new", "merge", "upgrade")
STATUSES = ("queued", "fetching", "writing", "ready", "failed", "applied", "discarded")


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_project_document_versions_source"), "project_document_versions", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_project_document_versions_source"),
        "project_document_versions",
        f"source IN {SOURCES}",
    )
    op.create_table(
        "project_document_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("urls", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("instruction", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="queued", nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("body", sa.String(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("applied_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"mode IN {MODES}", name=op.f("ck_project_document_drafts_mode")),
        sa.CheckConstraint(f"status IN {STATUSES}", name=op.f("ck_project_document_drafts_status")),
        sa.CheckConstraint(
            f"category IN {CATEGORIES}", name=op.f("ck_project_document_drafts_category")
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_project_document_drafts_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["project_documents.id"],
            name=op.f("fk_project_document_drafts_document_id_project_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_project_document_drafts_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_project_document_drafts_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_document_drafts")),
    )
    op.create_index(
        op.f("ix_project_document_drafts_business_project_id"),
        "project_document_drafts",
        ["business_project_id"],
    )
    op.create_index(
        op.f("ix_project_document_drafts_document_id"),
        "project_document_drafts",
        ["document_id"],
    )
    # A person's drafts being written (the limit of ACTIVE_PER_USER).
    op.create_index(
        "ix_project_document_drafts_created_by_status",
        "project_document_drafts",
        ["created_by", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_document_drafts_created_by_status", table_name="project_document_drafts"
    )
    op.drop_index(
        op.f("ix_project_document_drafts_document_id"), table_name="project_document_drafts"
    )
    op.drop_index(
        op.f("ix_project_document_drafts_business_project_id"),
        table_name="project_document_drafts",
    )
    op.drop_table("project_document_drafts")
    # Versions written from links stay (history is kept), counted as edits on the web.
    op.execute("UPDATE project_document_versions SET source = 'editor' WHERE source = 'link'")
    op.drop_constraint(
        op.f("ck_project_document_versions_source"), "project_document_versions", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_project_document_versions_source"),
        "project_document_versions",
        f"source IN {OLD_SOURCES}",
    )
