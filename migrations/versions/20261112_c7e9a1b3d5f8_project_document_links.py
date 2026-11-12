"""project_document_links: one document shared by several dự án (docs/specs/29 §17,
user 2026-11-12). The document's business_project_id stays its home.

Revision ID: c7e9a1b3d5f8
Revises: b6d8f0a2c4e7
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e9a1b3d5f8"
down_revision: str | None = "b6d8f0a2c4e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "project_document_links"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("linked_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["project_documents.id"],
            name=op.f(f"fk_{TABLE}_document_id_project_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f(f"fk_{TABLE}_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_linked_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("document_id", "business_project_id", name=op.f(f"pk_{TABLE}")),
    )
    op.create_index(op.f(f"ix_{TABLE}_business_project_id"), TABLE, ["business_project_id"])


def downgrade() -> None:
    op.drop_index(op.f(f"ix_{TABLE}_business_project_id"), table_name=TABLE)
    op.drop_table(TABLE)
