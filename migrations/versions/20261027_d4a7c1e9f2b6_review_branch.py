"""per-repo review branch: where "recent commits" are listed and pushes are reviewed

Teams often work on develop or feature branches; listing only the GitLab default branch showed
"no commits in the last 7 days" for busy repos. NULL keeps the default branch, "*" means every
branch (docs/specs/17 §Nhánh review).

Revision ID: d4a7c1e9f2b6
Revises: c9e5a2b7d4f1
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7c1e9f2b6"
down_revision: str | None = "c9e5a2b7d4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("review_branch", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "review_branch")
