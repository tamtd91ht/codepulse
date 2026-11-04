"""user_notifications: the in-app bell, toast and history (docs/specs/24)

Revision ID: e5a7c9b1d3f5
Revises: d2e4f6a8b0c3
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7c9b1d3f5"
down_revision: str | None = "d2e4f6a8b0c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=True),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("tone", sa.String(), server_default="info", nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_user_notifications_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_notifications")),
    )
    # The history (newest first) and the unread count of one person.
    op.create_index("ix_user_notifications_user_id_id", "user_notifications", ["user_id", "id"])
    op.create_index(
        "ix_user_notifications_unread",
        "user_notifications",
        ["user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index("ix_user_notifications_actor_id", "user_notifications", ["actor_id"])
    op.create_index("ix_user_notifications_created_at", "user_notifications", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_user_notifications_created_at", table_name="user_notifications")
    op.drop_index("ix_user_notifications_actor_id", table_name="user_notifications")
    op.drop_index("ix_user_notifications_unread", table_name="user_notifications")
    op.drop_index("ix_user_notifications_user_id_id", table_name="user_notifications")
    op.drop_table("user_notifications")
