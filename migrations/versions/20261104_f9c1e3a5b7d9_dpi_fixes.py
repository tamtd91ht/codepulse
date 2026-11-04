"""DPI fixes (docs/specs/25): reviews pin their rubric version; a GIN index for the issues
assigned to an account.

Revision ID: f9c1e3a5b7d9
Revises: e7b9d1f3a5c7
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9c1e3a5b7d9"
down_revision: str | None = "e7b9d1f3a5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dpi_reviews", sa.Column("rubric_version", sa.String(), nullable=True))
    op.create_index(
        "ix_issues_assignee_account_ids",
        "issues",
        ["assignee_account_ids"],
        postgresql_using="gin",
        postgresql_ops={"assignee_account_ids": "jsonb_path_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_issues_assignee_account_ids", table_name="issues")
    op.drop_column("dpi_reviews", "rubric_version")
