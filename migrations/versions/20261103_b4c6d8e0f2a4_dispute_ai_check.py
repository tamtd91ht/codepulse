"""finding_disputes.ai_*: an AI second look a lead may ask for before deciding a dispute

User request 2026-11-03 (app/review/dispute_check.py, app/workers/dispute_jobs.py).

Revision ID: b4c6d8e0f2a4
Revises: a3b5c7d9e1f3
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c6d8e0f2a4"
down_revision: str | None = "a3b5c7d9e1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [
    ("ai_status", sa.String()),  # queued|running|done|failed
    ("ai_verdict", sa.String()),  # finding_holds|dispute_holds|unclear
    ("ai_confidence", sa.Numeric(3, 2)),
    ("ai_reason", sa.String()),
    ("ai_evidence", sa.String()),
    ("ai_model", sa.String()),
    ("ai_cost_usd", sa.Numeric(10, 4)),
    ("ai_requested_by", sa.Integer()),
    ("ai_requested_at", sa.DateTime(timezone=True)),
    ("ai_finished_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("finding_disputes", sa.Column(name, kind, nullable=True))
    op.create_foreign_key(
        op.f("fk_finding_disputes_ai_requested_by_users"),
        "finding_disputes",
        "users",
        ["ai_requested_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_finding_disputes_ai_requested_by_users"), "finding_disputes", type_="foreignkey"
    )
    for name, _kind in reversed(COLUMNS):
        op.drop_column("finding_disputes", name)
