"""Task round 2, stage 2.5 - notifications and rules (docs/specs/31 §20.5).

- user_notifications.snoozed_until: "Tạm ẩn 1 ngày"; a snoozed row is not unread until then.
- task_rule_runs: the log of automation rules (what a rule did, to which task, when; dry runs
  marked).

Revision ID: d4f6b8c0e2a5
Revises: c3e5a7b9d1f4
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4f6b8c0e2a5"
down_revision: str | None = "c3e5a7b9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_notifications",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "task_rule_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("rule", sa.String(length=40), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("dry_run", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["task_spaces.business_project_id"],
            name=op.f("fk_task_rule_runs_business_project_id_task_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_rule_runs_task_id_tasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_rule_runs")),
    )
    op.create_index(
        op.f("ix_task_rule_runs_space_id"),
        "task_rule_runs",
        ["business_project_id", "id"],
    )
    op.create_index(op.f("ix_task_rule_runs_rule_task"), "task_rule_runs", ["rule", "task_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_task_rule_runs_rule_task"), table_name="task_rule_runs")
    op.drop_index(op.f("ix_task_rule_runs_space_id"), table_name="task_rule_runs")
    op.drop_table("task_rule_runs")
    op.drop_column("user_notifications", "snoozed_until")
