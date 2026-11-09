"""assistant_conversations + assistant_turns: Lịch sử Stetho, kept 30 days, admins only
(docs/specs/28 §9, user 2026-11-09)

Revision ID: a9c1e3f5b7d9
Revises: f8b0c2d4e6a8
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9c1e3f5b7d9"
down_revision: str | None = "f8b0c2d4e6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAGS = (
    "codepulse_howto",
    "jira_commit",
    "quality_analysis",
    "findings_fix",
    "people_team",
    "dpi",
    "admin_config",
    "tech_general",
    "other",
)


def _stamps() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "assistant_conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("turns", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 4), server_default="0", nullable=False),
        sa.Column("tag", sa.String(), nullable=True),
        sa.Column("tag_source", sa.String(), nullable=True),
        sa.Column("tagged_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            f"tag IS NULL OR tag IN {TAGS}", name=op.f("ck_assistant_conversations_tag")
        ),
        sa.CheckConstraint(
            "tag_source IS NULL OR tag_source IN ('ai', 'rule', 'manual')",
            name=op.f("ck_assistant_conversations_tag_source"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_assistant_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_conversations")),
    )
    op.create_index("ix_assistant_conversations_last_at", "assistant_conversations", ["last_at"])
    op.create_index(
        "ix_assistant_conversations_user_id_last_at",
        "assistant_conversations",
        ["user_id", "last_at"],
    )
    op.create_table(
        "assistant_turns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("question", sa.String(), nullable=False),
        sa.Column("answer_points", sa.String(), nullable=True),
        sa.Column("answer_chars", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("screen", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("docs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("web", sa.Boolean(), server_default="false", nullable=False),
        *_stamps(),
        sa.CheckConstraint(
            "status IN ('ok', 'error', 'cancelled')", name=op.f("ck_assistant_turns_status")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["assistant_conversations.id"],
            name=op.f("fk_assistant_turns_conversation_id_assistant_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_turns")),
    )
    op.create_index(
        op.f("ix_assistant_turns_conversation_id"), "assistant_turns", ["conversation_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_assistant_turns_conversation_id"), table_name="assistant_turns")
    op.drop_table("assistant_turns")
    op.drop_index(
        "ix_assistant_conversations_user_id_last_at", table_name="assistant_conversations"
    )
    op.drop_index("ix_assistant_conversations_last_at", table_name="assistant_conversations")
    op.drop_table("assistant_conversations")
