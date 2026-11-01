"""commits / merge_requests: index author_account_id

Linking an account to a person gives the account's earlier reviews that person
(app/identity/attribution.py): the commits and merge requests of one account are looked up on
every link, which without these indexes scans both tables. Built CONCURRENTLY so a large
`commits` table is not locked while the index builds.

Revision ID: e5f7a9b1c3d4
Revises: d4e6f8a0b2c3
Create Date: 2026-11-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5f7a9b1c3d4"
down_revision: str | None = "d4e6f8a0b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = (
    ("ix_commits_author_account_id", "commits"),
    ("ix_merge_requests_author_account_id", "merge_requests"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table in INDEXES:
            op.create_index(
                name,
                table,
                ["author_account_id"],
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table in INDEXES:
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
