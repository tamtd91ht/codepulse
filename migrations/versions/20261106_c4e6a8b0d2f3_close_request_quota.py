"""users.close_requests_per_day: a person's own limit of close requests (user 2026-11-06)

NULL: the default of Admin > Cấu hình (`close_requests_per_day`, CLOSE_REQUESTS_PER_DAY, 20).
0: this person may not send any. Counted over the last 24 hours, web and MCP together.

Revision ID: c4e6a8b0d2f3
Revises: b2d4f6a8c0e1
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e6a8b0d2f3"
down_revision: str | None = "b2d4f6a8c0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("close_requests_per_day", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_users_close_requests_per_day"),
        "users",
        "close_requests_per_day IS NULL OR close_requests_per_day BETWEEN 0 AND 1000",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_close_requests_per_day"), "users", type_="check")
    op.drop_column("users", "close_requests_per_day")
