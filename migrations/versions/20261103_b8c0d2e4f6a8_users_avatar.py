"""users.avatar_file: the one avatar a person has (Admin > Người & Team)

User request 2026-11-03: admins view and edit a member's profile (name, email, avatar). The
image lives in AVATAR_DIR as "<user id>.<ext>"; this column names it, NULL = no avatar.

Revision ID: b8c0d2e4f6a8
Revises: a7b9c1d3e5f7
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c0d2e4f6a8"
down_revision: str | None = "a7b9c1d3e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_file", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_file")
