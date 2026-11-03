"""repo_component_maps: AI grouping of a repo's components (docs/specs/20 §4.9b)

Revision ID: c5d7e9f1a3b5
Revises: b4c6d8e0f2a4
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5d7e9f1a3b5"
down_revision: str | None = "b4c6d8e0f2a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repo_component_maps",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("groups", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("paths_seen", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_repo_component_maps_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_repo_component_maps_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_repo_component_maps")),
    )


def downgrade() -> None:
    op.drop_table("repo_component_maps")
