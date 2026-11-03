"""commit_files: the paths each commit changed, with line counts

docs/specs/20 §5.1 (Engineering Insights, user request 2026-11-03): the input of knowledge risk
(bus factor per module). Paths after IGNORE_PATHS and line counts only, never content. Filled by
the converter from the commit diff stats it already collects; older commits are filled from
raw_gitlab by `codepulse insights backfill-files`.

Revision ID: a7b9c1d3e5f7
Revises: f6a8b0c2d4e6
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b9c1d3e5f7"
down_revision: str | None = "f6a8b0c2d4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commit_files",
        sa.Column("sha", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("additions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deletions", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sha"],
            ["commits.sha"],
            name=op.f("fk_commit_files_sha_commits"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sha", "path", name=op.f("pk_commit_files")),
    )
    op.create_index(op.f("ix_commit_files_path"), "commit_files", ["path"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_commit_files_path"), table_name="commit_files")
    op.drop_table("commit_files")
