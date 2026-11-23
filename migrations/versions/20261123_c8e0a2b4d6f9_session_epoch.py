"""`users.session_epoch` (docs/specs/33 §14): the number a session token must carry to count;
an admin's "sign out" adds one, ending every session of that person.

Revision ID: c8e0a2b4d6f9
Revises: b7d9f1a3c5e8
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e0a2b4d6f9"
down_revision: str | None = "b7d9f1a3c5e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("session_epoch", sa.Integer(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("users", "session_epoch")
