"""indexes on foreign keys used to find a repo's rows

Removing a repo deletes child rows by these columns, and every deleted parent row makes
PostgreSQL look up its referencing rows; without indexes both are sequential scans.

Revision ID: c2d8f4a1b6e3
Revises: b7e4c1d9a2f0
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c2d8f4a1b6e3"
down_revision: str | None = "b7e4c1d9a2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = [
    ("mr_commits", "sha"),
    ("mr_notes", "mr_id"),
    ("pipelines", "project_id"),
    ("raw_gitlab", "project_id"),
    ("reviews", "mr_id"),
    ("reviews", "commit_sha"),
    ("finding_disputes", "finding_id"),
]


def upgrade() -> None:
    for table, column in INDEXES:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column], unique=False)


def downgrade() -> None:
    for table, column in reversed(INDEXES):
        op.drop_index(op.f(f"ix_{table}_{column}"), table_name=table)
