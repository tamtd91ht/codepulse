"""project grants: who may see and manage each dự án

Outside the spec, requested by the user 2026-10-28 (docs/specs/09 "Phân quyền"). A person sees
only the dự án they are granted ("view"), or also requests and re-runs reviews and edits the
review premises there ("manage"). ADMIN_EMAILS admins need no row.

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_grants",
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("granted_by_user_id", sa.Integer(), nullable=True),
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
        sa.CheckConstraint("role IN ('view','manage')", name=op.f("ck_project_grants_role")),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_project_grants_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_project_grants_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["users.id"],
            name=op.f("fk_project_grants_granted_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("business_project_id", "user_id", name=op.f("pk_project_grants")),
    )
    op.create_index(op.f("ix_project_grants_user_id"), "project_grants", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_project_grants_user_id"), table_name="project_grants")
    op.drop_table("project_grants")
