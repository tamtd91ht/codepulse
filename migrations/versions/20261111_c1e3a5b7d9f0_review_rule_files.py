"""review_rule_files: review rules imported as files into a dự án (docs/specs/17 §Tệp quy tắc,
user 2026-11-11). The text converted to Markdown is kept, never the file itself.

Revision ID: c1e3a5b7d9f0
Revises: b0d2f4a6c8e1
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1e3a5b7d9f0"
down_revision: str | None = "b0d2f4a6c8e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_rule_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_review_rule_files_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_review_rule_files_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_review_rule_files_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_rule_files")),
        sa.UniqueConstraint("business_project_id", "name", name="uq_rule_file_name"),
    )
    op.create_index(
        op.f("ix_review_rule_files_business_project_id"),
        "review_rule_files",
        ["business_project_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_review_rule_files_business_project_id"), table_name="review_rule_files")
    op.drop_table("review_rule_files")
