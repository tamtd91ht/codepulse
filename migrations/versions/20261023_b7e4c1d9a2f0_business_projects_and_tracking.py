"""business projects and repo tracking

Outside docs/specs/03, requested by the user: an admin-defined "dự án" groups repos (the
`projects` table holds GitLab projects, i.e. git repos), and a repo can stop being tracked
without deleting its history (AGENTS.md: review history is never deleted).

Revision ID: b7e4c1d9a2f0
Revises: a41d2c8e9b13
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4c1d9a2f0"
down_revision: str | None = "a41d2c8e9b13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_projects")),
        sa.UniqueConstraint("name", name=op.f("uq_business_projects_name")),
    )
    op.add_column("projects", sa.Column("business_project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_projects_business_project_id_business_projects"),
        "projects",
        "business_projects",
        ["business_project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "projects",
        sa.Column("tracked", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column("projects", sa.Column("untracked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "untracked_at")
    op.drop_column("projects", "tracked")
    op.drop_constraint(
        op.f("fk_projects_business_project_id_business_projects"), "projects", type_="foreignkey"
    )
    op.drop_column("projects", "business_project_id")
    op.drop_table("business_projects")
