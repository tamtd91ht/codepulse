"""finding_closures: the run token and start time of a close-request check

Each "Chạy review" gets a token; only the worker carrying it may claim and decide the request, so
two presses, a re-run after a lost check or a cancel during the check never pay twice nor close
a finding on a request that moved on. `started_at` is when the worker began: a check is lost
when it began long ago, not when it waited in a busy queue.

Revision ID: c3d5e7f9a1b2
Revises: b2c4d6e8f0a1
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d5e7f9a1b2"
down_revision: str | None = "b2c4d6e8f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("finding_closures", sa.Column("run_token", sa.String(), nullable=True))
    op.add_column(
        "finding_closures", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("finding_closures", "started_at")
    op.drop_column("finding_closures", "run_token")
