"""telegram bots and requests

Outside docs/specs/03, requested by the user: one Telegram bot per dự án (optionally pinned to
a repo) takes review commands; requests remember the chat so results can be posted back.

Revision ID: e3a1b2c4d5f6
Revises: c2d8f4a1b6e3
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3a1b2c4d5f6"
down_revision: str | None = "c2d8f4a1b6e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_bots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("bot_username", sa.String(), nullable=True),
        sa.Column("token_encrypted", sa.String(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("allowed_chat_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_update_id", sa.BigInteger(), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_telegram_bots_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_telegram_bots_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_bots")),
        sa.UniqueConstraint(
            "business_project_id",
            "project_id",
            name="uq_telegram_bots_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "telegram_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("command", sa.String(), nullable=False),
        sa.Column("targets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(), server_default="queued", nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bot_id"],
            ["telegram_bots.id"],
            name=op.f("fk_telegram_requests_bot_id_telegram_bots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_telegram_requests_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_requests")),
    )
    op.create_index(
        op.f("ix_telegram_requests_bot_id"), "telegram_requests", ["bot_id"], unique=False
    )
    op.create_index(
        op.f("ix_telegram_requests_user_id"), "telegram_requests", ["user_id"], unique=False
    )
    op.create_index(
        "ix_telegram_requests_status_created_at",
        "telegram_requests",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_requests_status_created_at", table_name="telegram_requests")
    op.drop_index(op.f("ix_telegram_requests_user_id"), table_name="telegram_requests")
    op.drop_index(op.f("ix_telegram_requests_bot_id"), table_name="telegram_requests")
    op.drop_table("telegram_requests")
    op.drop_table("telegram_bots")
