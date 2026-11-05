"""accounts.email, accounts.login: a jira_user's emailAddress and name, kept so that an account
not yet linked to a person is linked once that person exists (docs/specs/27 §7)

Revision ID: f1b3d5e7a9c2
Revises: e8a0c2d4f6b9
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1b3d5e7a9c2"
down_revision: str | None = "e8a0c2d4f6b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("email", sa.String(), nullable=True))
    op.add_column("accounts", sa.Column("login", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "login")
    op.drop_column("accounts", "email")
