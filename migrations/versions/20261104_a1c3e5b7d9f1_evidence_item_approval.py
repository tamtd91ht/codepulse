"""evidence_items.approved / approved_by: the lead approves each self-reported item (spec 25 §5)

Revision ID: a1c3e5b7d9f1
Revises: f9c1e3a5b7d9
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c3e5b7d9f1"
down_revision: str | None = "f9c1e3a5b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence_items", sa.Column("approved", sa.Boolean(), nullable=True))
    op.add_column("evidence_items", sa.Column("approved_by", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_evidence_items_approved_by_users"),
        "evidence_items",
        "users",
        ["approved_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_evidence_items_approved_by_users"), "evidence_items", type_="foreignkey"
    )
    op.drop_column("evidence_items", "approved_by")
    op.drop_column("evidence_items", "approved")
