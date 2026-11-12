"""project_document_draft_images: screenshots as sources of Tổng hợp bằng AI
(docs/specs/29 §12.10, user 2026-11-12). Kept only until the draft is applied or dropped.

Revision ID: a5c7e9f1b3d6
Revises: f4b6c8d0e2a5
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5c7e9f1b3d6"
down_revision: str | None = "f4b6c8d0e2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "project_document_draft_images"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mime", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f(f"fk_{TABLE}_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["project_document_drafts.id"],
            name=op.f(f"fk_{TABLE}_draft_id_project_document_drafts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_uploaded_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
    )
    for column in ("business_project_id", "draft_id", "uploaded_by"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column])


def downgrade() -> None:
    for column in ("business_project_id", "draft_id", "uploaded_by"):
        op.drop_index(op.f(f"ix_{TABLE}_{column}"), table_name=TABLE)
    op.drop_table(TABLE)
