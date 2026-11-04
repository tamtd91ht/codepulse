"""dpi_reviews.ai_suggestion*: the AI's suggestion of the lead's 30 points (docs/specs/25 §13)

Revision ID: f0b2d4e6a8c1
Revises: e8a0c2d4f6b8
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f0b2d4e6a8c1"
down_revision: str | None = "e8a0c2d4f6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dpi_reviews", sa.Column("ai_suggestion", JSONB(), nullable=True))
    op.add_column("dpi_reviews", sa.Column("ai_suggestion_status", sa.String(), nullable=True))
    op.add_column(
        "dpi_reviews", sa.Column("ai_suggested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("dpi_reviews", sa.Column("ai_suggestion_error", sa.String(), nullable=True))


def downgrade() -> None:
    for column in (
        "ai_suggestion_error",
        "ai_suggested_at",
        "ai_suggestion_status",
        "ai_suggestion",
    ):
        op.drop_column("dpi_reviews", column)
