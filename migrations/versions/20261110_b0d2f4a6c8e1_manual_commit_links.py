"""issue_commit_links.manual / linked_by: a commit linked to a Jira issue by hand on Task Jira
(docs/specs/27 §20, user 2026-11-10); such links survive re-reading the commit message.

Revision ID: b0d2f4a6c8e1
Revises: a9c1e3f5b7d9
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b0d2f4a6c8e1"
down_revision: str | None = "a9c1e3f5b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "issue_commit_links",
        sa.Column("manual", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("issue_commit_links", sa.Column("linked_by", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_issue_commit_links_linked_by_users"),
        "issue_commit_links",
        "users",
        ["linked_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_issue_commit_links_linked_by_users"), "issue_commit_links", type_="foreignkey"
    )
    op.drop_column("issue_commit_links", "linked_by")
    op.drop_column("issue_commit_links", "manual")
