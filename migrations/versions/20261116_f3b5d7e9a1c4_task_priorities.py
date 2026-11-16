"""Task priorities per dự án, as Jira's priority scheme (docs/specs/31 §18, user 2026-11-16:
"làm cả 3 luôn đi"): `task_priorities` holds each dự án's own names, colours, order and default;
`level` (one of the five old values) gives the arrow and DPI's P1/P2. Every existing task space
gets the five priorities it used so far (same keys, so tasks keep theirs); the fixed CHECK on
`tasks.priority` goes, the column holds the key of one of its dự án's priorities.

Downgrade: a task on a custom priority falls back to that priority's level, the CHECK returns.

Revision ID: f3b5d7e9a1c4
Revises: e1a3c5e7b9d2
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b5d7e9a1c4"
down_revision: str | None = "e1a3c5e7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEVELS = ("highest", "high", "medium", "low", "lowest")
COLORS = ("red", "orange", "amber", "green", "blue", "purple", "gray")
DEFAULTS = (
    ("highest", "Cao nhất", "highest", "red"),
    ("high", "Cao", "high", "orange"),
    ("medium", "Trung bình", "medium", "amber"),
    ("low", "Thấp", "low", "blue"),
    ("lowest", "Thấp nhất", "lowest", "gray"),
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "task_priorities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("color", sa.String(length=12), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("level", LEVELS), name=op.f("ck_task_priorities_level")),
        sa.CheckConstraint(_in("color", COLORS), name=op.f("ck_task_priorities_color")),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["task_spaces.business_project_id"],
            name=op.f("fk_task_priorities_business_project_id_task_spaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_priorities")),
        sa.UniqueConstraint(
            "business_project_id", "key", name=op.f("uq_task_priorities_business_project_id_key")
        ),
    )
    op.create_index(
        op.f("ix_task_priorities_business_project_id"),
        "task_priorities",
        ["business_project_id"],
        unique=False,
    )
    bind = op.get_bind()
    spaces = [r[0] for r in bind.execute(sa.text("SELECT business_project_id FROM task_spaces"))]
    rows = [
        {
            "bp": bp,
            "key": key,
            "name": name,
            "level": level,
            "color": color,
            "position": i,
            "is_default": key == "medium",
        }
        for bp in spaces
        for i, (key, name, level, color) in enumerate(DEFAULTS)
    ]
    if rows:
        bind.execute(
            sa.text(
                "INSERT INTO task_priorities"
                " (business_project_id, key, name, level, color, position, is_default)"
                " VALUES (:bp, :key, :name, :level, :color, :position, :is_default)"
            ),
            rows,
        )
    op.drop_constraint(op.f("ck_tasks_priority"), "tasks", type_="check")
    op.alter_column("tasks", "priority", type_=sa.String(length=30), existing_nullable=False)


def downgrade() -> None:
    op.execute(
        "UPDATE tasks SET priority = p.level FROM task_priorities p"
        " WHERE p.business_project_id = tasks.business_project_id AND p.key = tasks.priority"
    )
    op.execute(
        "UPDATE tasks SET priority = 'medium'"
        " WHERE priority NOT IN ('highest', 'high', 'medium', 'low', 'lowest')"
    )
    op.alter_column("tasks", "priority", type_=sa.String(), existing_nullable=False)
    op.create_check_constraint(op.f("ck_tasks_priority"), "tasks", _in("priority", LEVELS))
    op.drop_index(op.f("ix_task_priorities_business_project_id"), table_name="task_priorities")
    op.drop_table("task_priorities")
