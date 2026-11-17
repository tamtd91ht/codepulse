"""Task round 3, stage 3.2 - anonymous usage telemetry (docs/specs/31 §21.2).

- usage_events: (role, event, surface, at) only; never a person or task content. Kept 180 days.

The notification settings of round 3 (organisation defaults, quiet days) live in app_settings
and need no schema change.

Revision ID: a2b3c4d5e6f7
Revises: b3c4d5e6f7a8
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("surface", sa.String(length=40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
    )
    op.create_index(op.f("ix_usage_events_at"), "usage_events", ["at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_events_at"), table_name="usage_events")
    op.drop_table("usage_events")
