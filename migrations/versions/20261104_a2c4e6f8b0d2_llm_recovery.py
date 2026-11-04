"""llm_health + reviews.llm_retry: review again once the LLM works again (user 2026-11-04)

Revision ID: a2c4e6f8b0d2
Revises: f0b2d4e6a8c1
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2c4e6f8b0d2"
down_revision: str | None = "f0b2d4e6a8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("llm_retry", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index(
        "ix_reviews_llm_retry",
        "reviews",
        ["id"],
        postgresql_where=sa.text("llm_retry"),
    )
    op.create_table(
        "llm_health",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), server_default="ok", nullable=False),
        sa.Column("down_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("probes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requeued_last", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_health")),
    )
    op.execute("INSERT INTO llm_health (id, status) VALUES (1, 'ok')")


def downgrade() -> None:
    op.drop_table("llm_health")
    op.drop_index("ix_reviews_llm_retry", table_name="reviews")
    op.drop_column("reviews", "llm_retry")
