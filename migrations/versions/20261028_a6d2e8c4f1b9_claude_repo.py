"""dự án: optional shared .claude repo

A GitLab repo holding the dự án's shared Claude configuration (skills, agents, rules,
CLAUDE.md). The deep review of every repo in the dự án reads its instruction files next to the
repo's own. Optional: NULL everywhere means "not configured".

Revision ID: a6d2e8c4f1b9
Revises: f3c9a1d5b8e2
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6d2e8c4f1b9"
down_revision: str | None = "f3c9a1d5b8e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "business_projects", sa.Column("claude_repo_connection_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "business_projects", sa.Column("claude_repo_external_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("business_projects", sa.Column("claude_repo_path", sa.String(), nullable=True))
    op.add_column("business_projects", sa.Column("claude_repo_ref", sa.String(255), nullable=True))
    op.add_column("business_projects", sa.Column("claude_repo_include", sa.String(), nullable=True))
    op.create_foreign_key(
        op.f("fk_business_projects_claude_repo_connection_id_connections"),
        "business_projects",
        "connections",
        ["claude_repo_connection_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_business_projects_claude_repo_connection_id_connections"),
        "business_projects",
        type_="foreignkey",
    )
    for column in (
        "claude_repo_include",
        "claude_repo_ref",
        "claude_repo_path",
        "claude_repo_external_id",
        "claude_repo_connection_id",
    ):
        op.drop_column("business_projects", column)
