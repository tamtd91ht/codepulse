"""llm_accounts: several Claude accounts, one default and backups (user 2026-11-09)

The single token set from the Admin screen (stored_secrets 'claude_code_oauth_token') becomes
the default account, encrypted value copied as is (same key). reviews.llm_account records which
account answered a review.

Revision ID: d6f8a0c2e4b6
Revises: c4e6a8b0d2f3
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6f8a0c2e4b6"
down_revision: str | None = "c4e6a8b0d2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TOKEN = "claude_code_oauth_token"  # noqa: S105 - a row name, not a secret


def upgrade() -> None:
    op.create_table(
        "llm_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("token_encrypted", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("state", sa.String(), server_default="ok", nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String()),
        sa.Column("blocked_model", sa.String()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_ok_at", sa.DateTime(timezone=True)),
        sa.Column("last_failed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('ok', 'quota', 'auth', 'model')", name=op.f("ck_llm_accounts_state")
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], name=op.f("fk_llm_accounts_set_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_accounts")),
    )
    op.create_index(
        "uq_llm_accounts_default",
        "llm_accounts",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.execute(
        sa.text(
            "INSERT INTO llm_accounts"
            " (label, token_encrypted, source, set_by, is_default, position) "
            "SELECT 'Tài khoản mặc định', value_encrypted, 'migrated', set_by, true, 0 "
            "FROM stored_secrets WHERE name = :name"
        ).bindparams(name=OLD_TOKEN)
    )
    op.execute(sa.text("DELETE FROM stored_secrets WHERE name = :name").bindparams(name=OLD_TOKEN))
    op.add_column("reviews", sa.Column("llm_account", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("reviews", "llm_account")
    op.execute(
        sa.text(
            "INSERT INTO stored_secrets (name, value_encrypted, source, set_by) "
            "SELECT :name, token_encrypted, source, set_by FROM llm_accounts WHERE is_default "
            "ON CONFLICT (name) DO NOTHING"
        ).bindparams(name=OLD_TOKEN)
    )
    op.drop_index("uq_llm_accounts_default", table_name="llm_accounts")
    op.drop_table("llm_accounts")
