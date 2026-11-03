"""deployments, projects.production_environment (DORA, docs/specs/20 §4.10, §5.2)

Phase 9.8, decision D2: production = GitLab's `production` tier, else an environment named
"production"/"prod"; `projects.production_environment` overrides it per repo. The MRs a
deployment shipped are kept as iids on the row and resolved when read.

Revision ID: d0e2f4a6b8c0
Revises: c9d1e3f5a7b9
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d0e2f4a6b8c0"
down_revision: str | None = "c9d1e3f5a7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("production_environment", sa.String(255), nullable=True))
    op.create_table(
        "deployments",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("environment_tier", sa.String(), nullable=True),
        sa.Column("sha", sa.String(), nullable=True),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mr_iids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            name=op.f("fk_deployments_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployments")),
    )
    op.create_index(
        "ix_deployments_project_id_finished_at_src",
        "deployments",
        ["project_id", "finished_at_src"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_deployments_project_id_finished_at_src", table_name="deployments")
    op.drop_table("deployments")
    op.drop_column("projects", "production_environment")
