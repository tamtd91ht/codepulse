"""Task round 3, stage 3.5 - switching over from Jira (docs/specs/31 §21.6).

- task_spaces.jira_retired_at / jira_retired_by: "Ngừng dùng Jira" per dự án.
- task_key_aliases_seen: commits, MRs and branches that still name a task by its old Jira key.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_spaces", sa.Column("jira_retired_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("task_spaces", sa.Column("jira_retired_by", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_task_spaces_jira_retired_by_users"),
        "task_spaces",
        "users",
        ["jira_retired_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "task_key_aliases_seen",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("external_key", sa.String(length=30), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("ref", sa.String(length=300), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("sha", sa.String(length=64), nullable=True),
        sa.Column("mr_iid", sa.Integer(), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("author_user_id", sa.Integer(), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('commit', 'mr', 'branch')", name=op.f("ck_task_key_aliases_seen_kind")
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["task_spaces.business_project_id"],
            name=op.f("fk_task_key_aliases_seen_business_project_id_task_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_key_aliases_seen_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_task_key_aliases_seen_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
            name=op.f("fk_task_key_aliases_seen_author_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_key_aliases_seen")),
        sa.UniqueConstraint(
            "business_project_id",
            "external_key",
            "ref",
            name=op.f("uq_task_key_aliases_seen_business_project_id_external_key_ref"),
        ),
    )
    op.create_index(
        op.f("ix_task_key_aliases_seen_space_at"),
        "task_key_aliases_seen",
        ["business_project_id", "at"],
    )
    op.create_index(op.f("ix_task_key_aliases_seen_task_id"), "task_key_aliases_seen", ["task_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_task_key_aliases_seen_task_id"), table_name="task_key_aliases_seen")
    op.drop_index(op.f("ix_task_key_aliases_seen_space_at"), table_name="task_key_aliases_seen")
    op.drop_table("task_key_aliases_seen")
    op.drop_constraint(
        op.f("fk_task_spaces_jira_retired_by_users"), "task_spaces", type_="foreignkey"
    )
    op.drop_column("task_spaces", "jira_retired_by")
    op.drop_column("task_spaces", "jira_retired_at")
