"""Task round 2 stage 2.4 - saved space templates (docs/specs/31 §20.4).

- task_space_templates: a dự án's Task setup (types, workflow, priorities, rules, custom fields,
  board mode) saved as JSON to apply to other dự án.
- task_spaces.template_key: the template last applied (built-in or saved key).

Revision ID: c3e5a7b9d1f4
Revises: e2b4d6f8a0c3
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3e5a7b9d1f4"
down_revision: str | None = "e2b4d6f8a0c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_space_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("builtin", sa.Boolean(), server_default="false", nullable=False),
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
            name=op.f("fk_task_space_templates_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_space_templates")),
        sa.UniqueConstraint("key", name=op.f("uq_task_space_templates_key")),
    )
    op.add_column("task_spaces", sa.Column("template_key", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("task_spaces", "template_key")
    op.drop_table("task_space_templates")
