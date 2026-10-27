"""review knowledge: compiled review premises per repo / dự án, and manual edits

docs/specs/19 §3.4: `review_knowledge` holds one row per build (the previous active row of the
scope becomes superseded, nothing is deleted; a build that failed after paying keeps a `failed`
row with its cost); `review_knowledge_edits` keeps a lead's manual changes across builds.
Versions are unique per scope and partial unique indexes allow one active row per scope.
`projects.knowledge_auto_refresh` lets a repo rebuild on pushes that touch `.claude/` (default
off).

Revision ID: b1c2d3e4f5a6
Revises: a6d2e8c4f1b9
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a6d2e8c4f1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_knowledge",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("business_project_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(), server_default="active", nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("built_by", sa.String(), server_default="manual", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('project','business_project')", name=op.f("ck_review_knowledge_scope")
        ),
        sa.CheckConstraint(
            "status IN ('active','superseded','failed')", name=op.f("ck_review_knowledge_status")
        ),
        sa.UniqueConstraint(
            "scope", "project_id", "version", name=op.f("uq_review_knowledge_project_version")
        ),
        sa.UniqueConstraint(
            "scope",
            "business_project_id",
            "version",
            name=op.f("uq_review_knowledge_business_project_version"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["users.id"],
            name=op.f("fk_review_knowledge_approved_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_review_knowledge_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_review_knowledge_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_knowledge")),
    )
    op.create_index(
        "ix_review_knowledge_scope_project_id_status",
        "review_knowledge",
        ["scope", "project_id", "status"],
    )
    op.create_index(
        "ix_review_knowledge_scope_business_project_id_status",
        "review_knowledge",
        ["scope", "business_project_id", "status"],
    )
    op.create_index(
        "uq_review_knowledge_active_project",
        "review_knowledge",
        ["scope", "project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_review_knowledge_active_business_project",
        "review_knowledge",
        ["scope", "business_project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "review_knowledge_edits",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("business_project_id", sa.Integer(), nullable=True),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('project','business_project')",
            name=op.f("ck_review_knowledge_edits_scope"),
        ),
        sa.CheckConstraint(
            "action IN ('disable','set_severity','reword','add_invariant')",
            name=op.f("ck_review_knowledge_edits_action"),
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_review_knowledge_edits_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_review_knowledge_edits_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_review_knowledge_edits_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_knowledge_edits")),
    )
    op.create_index(
        op.f("ix_review_knowledge_edits_project_id"), "review_knowledge_edits", ["project_id"]
    )
    op.create_index(
        op.f("ix_review_knowledge_edits_business_project_id"),
        "review_knowledge_edits",
        ["business_project_id"],
    )

    op.add_column(
        "projects",
        sa.Column("knowledge_auto_refresh", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("projects", "knowledge_auto_refresh")
    op.drop_index(
        op.f("ix_review_knowledge_edits_business_project_id"), table_name="review_knowledge_edits"
    )
    op.drop_index(op.f("ix_review_knowledge_edits_project_id"), table_name="review_knowledge_edits")
    op.drop_table("review_knowledge_edits")
    op.drop_index("uq_review_knowledge_active_business_project", table_name="review_knowledge")
    op.drop_index("uq_review_knowledge_active_project", table_name="review_knowledge")
    op.drop_index(
        "ix_review_knowledge_scope_business_project_id_status", table_name="review_knowledge"
    )
    op.drop_index("ix_review_knowledge_scope_project_id_status", table_name="review_knowledge")
    op.drop_table("review_knowledge")
