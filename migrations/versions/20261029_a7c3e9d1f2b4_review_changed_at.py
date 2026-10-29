"""reviews.changed_at: when the reviewed change was made, the order of "Kết quả review"

Outside the spec, requested by the user 2026-10-29: the list of review results shows the
commit's time (an MR review: when the MR was opened) and is sorted by it, newest first. Kept on
the row with an index so the list does not sort every visible review per page. A change with no
known time gets 1970-01-01 and sorts last.

Revision ID: a7c3e9d1f2b4
Revises: d4e5f6a7b8c9
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e9d1f2b4"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNKNOWN = "'1970-01-01 00:00:00+00'"


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text(UNKNOWN),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE reviews r SET changed_at = c.authored_at
        FROM commits c
        WHERE r.commit_sha = c.sha AND c.authored_at IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE reviews r SET changed_at = m.created_at_src
        FROM merge_requests m
        WHERE r.mr_id = m.id AND m.created_at_src IS NOT NULL
        """
    )
    op.create_index(
        "ix_reviews_changed_at_id",
        "reviews",
        ["changed_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reviews_changed_at_id", table_name="reviews")
    op.drop_column("reviews", "changed_at")
