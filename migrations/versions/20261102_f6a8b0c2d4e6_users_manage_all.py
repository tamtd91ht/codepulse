"""users.manage_all: a person who manages every dự án without a grant

Below an admin, above everyone else (user request 2026-11-02): sees and does what a manager of
a dự án does (project_grants "manage"), on every dự án, without being granted any. Set by an
admin in Admin > Người & Team. Admin configuration stays with ADMIN_EMAILS.

Revision ID: f6a8b0c2d4e6
Revises: e5f7a9b1c3d4
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a8b0c2d4e6"
down_revision: str | None = "e5f7a9b1c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("manage_all", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "manage_all")
