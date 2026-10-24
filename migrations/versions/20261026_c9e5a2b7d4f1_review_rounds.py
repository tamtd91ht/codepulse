"""review rounds: a commit or MR head may be reviewed again on request

Every review stays a row of its own (history per commit / per MR head). A deliberate
"review lại" adds round N+1 for the same target and marks the previous round superseded, so
metrics count one review per target while nothing is deleted (docs/specs/17 §Lịch sử).

Revision ID: c9e5a2b7d4f1
Revises: b8d4f1a2c3e5
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e5a2b7d4f1"
down_revision: str | None = "b8d4f1a2c3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("round", sa.Integer(), server_default="1", nullable=False))
    op.add_column("reviews", sa.Column("superseded_by", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_reviews_superseded_by_reviews"),
        "reviews",
        "reviews",
        ["superseded_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("uq_reviews_target", "reviews", type_="unique")
    op.create_unique_constraint(
        "uq_reviews_target",
        "reviews",
        ["target_type", "mr_id", "commit_sha", "reviewed_sha", "rubric_version", "round"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_constraint("uq_reviews_target", "reviews", type_="unique")
    op.create_unique_constraint(
        "uq_reviews_target",
        "reviews",
        ["target_type", "mr_id", "commit_sha", "reviewed_sha", "rubric_version"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint(op.f("fk_reviews_superseded_by_reviews"), "reviews", type_="foreignkey")
    op.drop_column("reviews", "superseded_by")
    op.drop_column("reviews", "round")
