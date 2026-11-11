"""project_documents, project_document_versions, project_document_chunks + docs.view /
docs.edit: Tài liệu dự án (docs/specs/29, user 2026-11-11).

Every upload or edit is a version (the original file kept with an upload); the current version's
sections are indexed for search. `docs.view` goes into the built-in sets where they hold
`overview.view` (everyone), `docs.edit` into "Quản lý dự án" (project) and "Quản lý tổng" (all).
Only the new items are inserted: whatever an admin changed in the built-in sets since stays.
BUILTIN is a literal copy of app/permissions/catalog.BUILTIN_SETS at this revision (tests compare
the two).

Revision ID: d2f4a6b8c0e2
Revises: c1e3a5b7d9f0
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert

revision: str = "d2f4a6b8c0e2"
down_revision: str | None = "c1e3a5b7d9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUILTIN = {
    "member": {
        "overview.view": "flag",
        "evaluation.view": "own",
        "report.member": "own",
        "report.mcp": "own",
        "report.export": "flag",
        "metrics.person": "own",
        "review.view": "own",
        "review.request": "own",
        "metrics.team": "own",
        "finding.close_request": "own",
        "dispute.raise": "own",
        "evidence.view": "own",
        "evidence.edit_self": "own",
        "dpi.view": "own",
        "dpi.self": "own",
        "dpi.dispute": "own",
        "knowledge.view": "flag",
        "telegram.self": "flag",
        "docs.view": "flag",
    },
    "lead": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "team",
        "report.member": "team",
        "report.mcp": "team",
        "report.export": "flag",
        "metrics.person": "team",
        "metrics.team": "team",
        "review.view": "team",
        "finding.close_decide": "team",
        "dispute.decide": "team",
        "dispute.ai_check": "team",
        "evidence.view": "team",
        "evidence.lead": "team",
        "dpi.view": "team",
        "dpi.assess": "team",
        "dpi.defects": "team",
    },
    "manager": {
        "insights.view": "flag",
        "members.view": "flag",
        "metrics.team": "team",
        "finding.close_decide": "team",
        "finding.lower_far": "team",
        "assistant.use": "flag",
        "dpi.distribution": "team",
    },
    "project_manager": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "project",
        "report.member": "project",
        "report.mcp": "project",
        "report.export": "flag",
        "review.view": "project",
        "review.request": "project",
        "review.rerun": "project",
        "review.author": "project",
        "knowledge.edit": "project",
        "criteria.assign": "project",
        "dpi.view": "project",
        "dpi.defects": "project",
        "docs.view": "flag",
        "docs.edit": "project",
    },
    "manage_all": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "all",
        "report.member": "all",
        "report.mcp": "all",
        "report.export": "flag",
        "review.view": "all",
        "review.request": "all",
        "review.rerun": "all",
        "review.author": "all",
        "knowledge.edit": "all",
        "criteria.assign": "all",
        "dpi.view": "all",
        "dpi.distribution": "all",
        "dpi.calibrate": "all",
        "dpi.defects": "all",
        "docs.view": "flag",
        "docs.edit": "all",
    },
}

NEW = ("docs.view", "docs.edit")
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
SOURCES = ("upload", "editor", "template", "restore")

sets = sa.table("permission_sets", sa.column("id", sa.Integer), sa.column("auto_role", sa.String))
items = sa.table(
    "permission_set_items",
    sa.column("set_id", sa.Integer),
    sa.column("permission", sa.String),
    sa.column("scope", sa.String),
)


def _stamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "project_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("category", sa.String(), server_default="other", nullable=False),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pinned", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("version_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        *_stamps(),
        sa.CheckConstraint(f"category IN {CATEGORIES}", name=op.f("ck_project_documents_category")),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_project_documents_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_project_documents_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_project_documents_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_documents")),
    )
    op.create_index(
        op.f("ix_project_documents_business_project_id"),
        "project_documents",
        ["business_project_id"],
    )
    op.create_table(
        "project_document_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("file_name", sa.String(), nullable=True),
        sa.Column("file_kind", sa.String(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("original", sa.LargeBinary(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            f"source IN {SOURCES}", name=op.f("ck_project_document_versions_source")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["project_documents.id"],
            name=op.f("fk_project_document_versions_document_id_project_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_project_document_versions_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_document_versions")),
        sa.UniqueConstraint("document_id", "number", name="uq_project_document_version"),
    )
    op.create_index(
        op.f("ix_project_document_versions_document_id"),
        "project_document_versions",
        ["document_id"],
    )
    op.create_foreign_key(
        "fk_project_documents_current_version_id",
        "project_documents",
        "project_document_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "project_document_chunks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(), nullable=True),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("search", postgresql.TSVECTOR(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["project_documents.id"],
            name=op.f("fk_project_document_chunks_document_id_project_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_document_chunks")),
    )
    op.create_index(
        op.f("ix_project_document_chunks_document_id"),
        "project_document_chunks",
        ["document_id"],
    )
    op.create_index(
        "ix_project_document_chunks_search",
        "project_document_chunks",
        ["search"],
        postgresql_using="gin",
    )
    bind = op.get_bind()
    for role, held in BUILTIN.items():
        set_id = bind.execute(sa.select(sets.c.id).where(sets.c.auto_role == role)).scalar()
        rows = [
            {"set_id": set_id, "permission": k, "scope": v} for k, v in held.items() if k in NEW
        ]
        if set_id is None or not rows:
            continue
        bind.execute(insert(items).values(rows).on_conflict_do_nothing())


def downgrade() -> None:
    op.get_bind().execute(sa.delete(items).where(items.c.permission.in_(NEW)))
    op.drop_index("ix_project_document_chunks_search", table_name="project_document_chunks")
    op.drop_index(
        op.f("ix_project_document_chunks_document_id"), table_name="project_document_chunks"
    )
    op.drop_table("project_document_chunks")
    op.drop_constraint(
        "fk_project_documents_current_version_id", "project_documents", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_project_document_versions_document_id"), table_name="project_document_versions"
    )
    op.drop_table("project_document_versions")
    op.drop_index(op.f("ix_project_documents_business_project_id"), table_name="project_documents")
    op.drop_table("project_documents")
