"""Teams kept in sync with a GitLab group (docs/specs/31 §20.7, R2).

- teams.gitlab_group_path: the GitLab group (full path) whose direct members the team follows;
  teams.gitlab_sync_at / gitlab_sync_error: the last sync and why it failed (NULL: it worked).
- team_members.source: "manual" (Admin, CSV; every existing row) or "gitlab" (added by the
  sync). The sync only ends rows it added itself.

Revision ID: e5a7c9d1f3b6
Revises: e2b4d6f8a0c3
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7c9d1f3b6"
down_revision: str | None = "e2b4d6f8a0c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("gitlab_group_path", sa.String(length=255), nullable=True))
    op.add_column("teams", sa.Column("gitlab_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("teams", sa.Column("gitlab_sync_error", sa.String(), nullable=True))
    op.add_column(
        "team_members",
        sa.Column("source", sa.String(length=10), server_default="manual", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_team_members_source"), "team_members", "source IN ('manual', 'gitlab')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_team_members_source"), "team_members", type_="check")
    op.drop_column("team_members", "source")
    op.drop_column("teams", "gitlab_sync_error")
    op.drop_column("teams", "gitlab_sync_at")
    op.drop_column("teams", "gitlab_group_path")
