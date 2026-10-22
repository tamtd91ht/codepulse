"""metrics fix_rate

docs/specs/08 defines fix_rate and uses it in the quality index; docs/specs/03 had no column.

Revision ID: 7c1f3a9d2e40
Revises: 0fde0516a366
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1f3a9d2e40"
down_revision: str | None = "0fde0516a366"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("metrics_user_period", "metrics_team_period")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("fix_rate", sa.Numeric(4, 3), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "fix_rate")
