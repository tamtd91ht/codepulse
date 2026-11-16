"""Task roadmap P5 - project templates and Kanban mode (docs/specs/31 §19.6).

- task_spaces.board_mode: "scrum" (the Board shows the active sprint, default) or "kanban" (the
  Board shows every open task and recently done ones; no Backlog tab).

Revision ID: e2b4d6f8a0c3
Revises: c9e1a3b5d7f0
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b4d6f8a0c3"
down_revision: str | None = "c9e1a3b5d7f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_spaces",
        sa.Column("board_mode", sa.String(length=10), server_default="scrum", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_task_spaces_board_mode"), "task_spaces", "board_mode IN ('scrum', 'kanban')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_task_spaces_board_mode"), "task_spaces", type_="check")
    op.drop_column("task_spaces", "board_mode")
