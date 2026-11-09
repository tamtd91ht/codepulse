"""commits.jira_keys + issue_commit_links: a commit linked to the Jira issue it fixes or names
(docs/specs/27 §19, user 2026-11-09)

The keys of commits stored before this stay NULL here; app.jira.commit_links.backfill_once_job
(queued by the scheduler) reads them from the stored messages and links them.

Revision ID: e7a9b1c3d5f7
Revises: d6f8a0c2e4b6
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7a9b1c3d5f7"
down_revision: str | None = "d6f8a0c2e4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "commits",
        sa.Column("jira_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_commits_jira_keys",
        "commits",
        ["jira_keys"],
        postgresql_using="gin",
        postgresql_ops={"jira_keys": "jsonb_path_ops"},
    )
    op.create_table(
        "issue_commit_links",
        sa.Column("issue_id", sa.BigInteger(), nullable=False),
        sa.Column("sha", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('fixes', 'refs')", name=op.f("ck_issue_commit_links_kind")),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["issues.id"],
            name=op.f("fk_issue_commit_links_issue_id_issues"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sha"],
            ["commits.sha"],
            name=op.f("fk_issue_commit_links_sha_commits"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("issue_id", "sha", name=op.f("pk_issue_commit_links")),
    )
    op.create_index(op.f("ix_issue_commit_links_sha"), "issue_commit_links", ["sha"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_issue_commit_links_sha"), table_name="issue_commit_links")
    op.drop_table("issue_commit_links")
    op.drop_index("ix_commits_jira_keys", table_name="commits")
    op.drop_column("commits", "jira_keys")
