"""deep review: project profile, per-repo switch, review mode and verification report

docs/specs/18_DEEP_REVIEW.md (requested by the user, 2026-10-25).

Revision ID: b8d4f1a2c3e5
Revises: a7c3e9b1d2f4
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8d4f1a2c3e5"
down_revision: str | None = "a7c3e9b1d2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("deep_review", sa.Boolean(), server_default="true", nullable=False)
    )
    op.add_column(
        "projects", sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("projects", sa.Column("profile_sha", sa.String(), nullable=True))
    op.add_column("projects", sa.Column("profile_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reviews", sa.Column("mode", sa.String(), server_default="fast", nullable=False))
    op.add_column(
        "reviews", sa.Column("deep", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("reviews", "deep")
    op.drop_column("reviews", "mode")
    op.drop_column("projects", "profile_at")
    op.drop_column("projects", "profile_sha")
    op.drop_column("projects", "profile")
    op.drop_column("projects", "deep_review")
