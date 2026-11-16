"""Task roadmap P2 - daily work (docs/specs/31 §19.3).

- merge_requests.reviewer_account_ids: GitLab reviewers of an MR (filled by the next sync), for
  "Việc của tôi" (MRs waiting for my review) and review waiting times.
- task_statuses.wip_limit / age_warning_hours: Board hints (soft WIP cap, card age flag).

Revision ID: c9e1a3b5d7f0
Revises: b8d0f2a4c6e9
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e1a3b5d7f0"
down_revision: str | None = "b8d0f2a4c6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "merge_requests",
        sa.Column("reviewer_account_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_merge_requests_reviewer_account_ids",
        "merge_requests",
        ["reviewer_account_ids"],
        postgresql_using="gin",
    )
    op.add_column("task_statuses", sa.Column("wip_limit", sa.Integer(), nullable=True))
    op.add_column("task_statuses", sa.Column("age_warning_hours", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_statuses", "age_warning_hours")
    op.drop_column("task_statuses", "wip_limit")
    op.drop_index("ix_merge_requests_reviewer_account_ids", table_name="merge_requests")
    op.drop_column("merge_requests", "reviewer_account_ids")
