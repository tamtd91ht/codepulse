"""Task roadmap P1 - leaving Jira (docs/specs/31 §19.2).

- test_project_settings.bug_target: "codepulse" (a bug from a failed test becomes a CodePulse task)
  or "jira". A dự án that already sends bugs to a Jira project keeps "jira" until switched.
- task_test_links: task ↔ testcase (`bug` raised from a result, `covers` for acceptance, P3).
- task_imports: issues imported from Jira; their old keys keep resolving to the new tasks.

Revision ID: b8d0f2a4c6e9
Revises: a7c9e1b3d5f8
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d0f2a4c6e9"
down_revision: str | None = "a7c9e1b3d5f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _stamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.add_column(
        "test_project_settings",
        sa.Column("bug_target", sa.String(length=10), server_default="codepulse", nullable=False),
    )
    op.execute(
        "UPDATE test_project_settings SET bug_target = 'jira' WHERE jira_project_key IS NOT NULL"
    )
    op.create_table(
        "task_test_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("result_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        *_stamps(),
        sa.CheckConstraint("kind IN ('bug', 'covers')", name=op.f("ck_task_test_links_kind")),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["test_cases.id"],
            name=op.f("fk_task_test_links_case_id_test_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_task_test_links_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["result_id"],
            ["test_results.id"],
            name=op.f("fk_task_test_links_result_id_test_results"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_test_links_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_test_links")),
        sa.UniqueConstraint(
            "task_id", "case_id", "kind", name=op.f("uq_task_test_links_task_id_case_id_kind")
        ),
    )
    op.create_index(op.f("ix_task_test_links_case_id"), "task_test_links", ["case_id"])
    op.create_index(op.f("ix_task_test_links_task_id"), "task_test_links", ["task_id"])
    op.create_table(
        "task_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("external_key", sa.String(length=30), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("imported_by", sa.Integer(), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(
            ["imported_by"],
            ["users.id"],
            name=op.f("fk_task_imports_imported_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_imports_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_imports")),
        sa.UniqueConstraint(
            "source", "external_key", name=op.f("uq_task_imports_source_external_key")
        ),
    )
    op.create_index(op.f("ix_task_imports_task_id"), "task_imports", ["task_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_task_imports_task_id"), table_name="task_imports")
    op.drop_table("task_imports")
    op.drop_index(op.f("ix_task_test_links_task_id"), table_name="task_test_links")
    op.drop_index(op.f("ix_task_test_links_case_id"), table_name="task_test_links")
    op.drop_table("task_test_links")
    op.drop_column("test_project_settings", "bug_target")
