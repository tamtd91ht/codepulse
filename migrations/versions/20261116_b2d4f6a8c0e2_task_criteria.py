"""Task roadmap round 2, stage 2.3 QA - acceptance criteria with stable ids (docs/specs/31 §20.3).

- task_criteria: a task's acceptance criteria (position, text, done).
- task_test_links.criterion_id: the criterion a `covers` testcase tests (SET NULL when the
  criterion goes; the case keeps covering the task).

Revision ID: b2d4f6a8c0e2
Revises: e2b4d6f8a0c3
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2d4f6a8c0e2"
down_revision: str | None = "e2b4d6f8a0c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_criteria",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("done", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_task_criteria_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_criteria_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_criteria")),
    )
    op.create_index(op.f("ix_task_criteria_task_id"), "task_criteria", ["task_id"], unique=False)
    op.add_column("task_test_links", sa.Column("criterion_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_task_test_links_criterion_id_task_criteria"),
        "task_test_links",
        "task_criteria",
        ["criterion_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_task_test_links_criterion_id"), "task_test_links", ["criterion_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_task_test_links_criterion_id"), table_name="task_test_links")
    op.drop_constraint(
        op.f("fk_task_test_links_criterion_id_task_criteria"), "task_test_links", type_="foreignkey"
    )
    op.drop_column("task_test_links", "criterion_id")
    op.drop_index(op.f("ix_task_criteria_task_id"), table_name="task_criteria")
    op.drop_table("task_criteria")
