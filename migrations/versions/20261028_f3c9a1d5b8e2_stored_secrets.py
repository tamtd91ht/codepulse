"""admin-set values: the Claude subscription token and review settings

stored_secrets: the token set from the Admin screen, encrypted with APP_ENCRYPTION_KEY; wins over
CLAUDE_CODE_OAUTH_TOKEN / LLM_API_KEY so an admin can log in without editing .env and restarting.
app_settings: review settings edited on the Admin screen (MAX_REVIEW_LOC), over their env default.

Revision ID: f3c9a1d5b8e2
Revises: e2b8f4c1a7d3
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c9a1d5b8e2"
down_revision: str | None = "e2b8f4c1a7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column[sa.DateTime]]:
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
        "stored_secrets",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("value_encrypted", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["set_by"],
            ["users.id"],
            name=op.f("fk_stored_secrets_set_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_stored_secrets")),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["set_by"],
            ["users.id"],
            name=op.f("fk_app_settings_set_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("stored_secrets")
