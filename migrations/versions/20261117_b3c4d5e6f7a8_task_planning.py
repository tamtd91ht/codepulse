"""Task round 3 stage 3.3 - sprint planning capacity (docs/specs/31 §21.3).

- sprints.capacity_points: the capacity typed in by whoever plans the sprint.
- sprints.capacity_source: "auto" (mean of the last 3 closed sprints, default) or "manual".

Revision ID: b3c4d5e6f7a8
Revises: d4f6b8c0e2a5
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "d4f6b8c0e2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sprints", sa.Column("capacity_points", sa.Numeric(8, 1), nullable=True))
    op.add_column(
        "sprints",
        sa.Column("capacity_source", sa.String(length=10), server_default="auto", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_sprints_capacity_source"), "sprints", "capacity_source IN ('auto', 'manual')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_sprints_capacity_source"), "sprints", type_="check")
    op.drop_column("sprints", "capacity_source")
    op.drop_column("sprints", "capacity_points")
