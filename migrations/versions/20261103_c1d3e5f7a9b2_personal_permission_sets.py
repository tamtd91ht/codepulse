"""permission_sets.personal_user_id: a person's own set (docs/specs/21 §9)

Revision ID: c1d3e5f7a9b2
Revises: b0c2d4e6f8a1
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d3e5f7a9b2"
down_revision: str | None = "b0c2d4e6f8a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("permission_sets", sa.Column("personal_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_permission_sets_personal_user_id_users"),
        "permission_sets",
        "users",
        ["personal_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        op.f("uq_permission_sets_personal_user_id"), "permission_sets", ["personal_user_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_permission_sets_personal_user_id"), "permission_sets", type_="unique"
    )
    op.drop_constraint(
        op.f("fk_permission_sets_personal_user_id_users"), "permission_sets", type_="foreignkey"
    )
    op.drop_column("permission_sets", "personal_user_id")
