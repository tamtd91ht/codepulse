"""An index on the reviews still in flight (`queued` / `running`): the orphan sweep
(app/workers/orphan_jobs.py) looks them up every 5 minutes. Partial, so it stays as small as
the backlog whatever the number of finished reviews.

Revision ID: f0e34169d75e
Revises: a1c3e5f7b9d2
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0e34169d75e"
down_revision: str | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_reviews_in_flight",
        "reviews",
        ["status", "updated_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_reviews_in_flight", table_name="reviews")
