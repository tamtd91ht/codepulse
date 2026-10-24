"""review basis: what each review was checked against

The rubric version and lens, the team rules (dự án, repo, files in the repo) with a short hash
of each so a change between rounds is visible, and the repository instructions, reviewer agents
and skills the deep pass read (docs/specs/17 §Căn cứ review). Names, paths and hashes only:
never the text of the rules or of any file.

Revision ID: e2b8f4c1a7d3
Revises: d4a7c1e9f2b6
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2b8f4c1a7d3"
down_revision: str | None = "d4a7c1e9f2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("basis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reviews", "basis")
