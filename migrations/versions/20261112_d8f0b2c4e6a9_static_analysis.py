"""review_static_findings + projects.static_analysis: Gitleaks / Semgrep on the changed lines
as hints for the review (docs/specs/07, user 2026-11-12).

Revision ID: d8f0b2c4e6a9
Revises: c7e9a1b3d5f8
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f0b2c4e6a9"
down_revision: str | None = "c7e9a1b3d5f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "review_static_findings"


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("static_analysis", sa.Boolean(), server_default="true", nullable=False),
    )
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("review_id", sa.BigInteger(), nullable=False),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("code_excerpt", sa.String(), nullable=True),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("confirmed_by_ai", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["reviews.id"],
            name=op.f(f"fk_{TABLE}_review_id_reviews"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    op.create_index(op.f(f"ix_{TABLE}_review_id"), TABLE, ["review_id"])


def downgrade() -> None:
    op.drop_index(op.f(f"ix_{TABLE}_review_id"), table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_column("projects", "static_analysis")
