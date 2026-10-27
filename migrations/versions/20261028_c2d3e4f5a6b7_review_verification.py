"""review findings: independent verification (docs/specs/19 §4.3)

`verified`: confirmed | downgraded | rejected | unverified | skipped (low/info, or reviews before
rubric v4). `verification`: the verifier's verdict, reason, evidence and cost. A rejected finding
keeps its row with status = rejected.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "review_findings",
        sa.Column("verified", sa.String(), server_default="skipped", nullable=False),
    )
    op.add_column(
        "review_findings",
        sa.Column("verification", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_findings", "verification")
    op.drop_column("review_findings", "verified")
