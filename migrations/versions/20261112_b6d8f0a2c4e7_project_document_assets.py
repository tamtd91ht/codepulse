"""project_document_assets: images pasted into the document editor (docs/specs/29 §13,
user 2026-11-12), referenced from the Markdown as asset:<id>.

Revision ID: b6d8f0a2c4e7
Revises: a5c7e9f1b3d6
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6d8f0a2c4e7"
down_revision: str | None = "a5c7e9f1b3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "project_document_assets"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mime", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
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
            ["uploaded_by"],
            ["users.id"],
            name=op.f(f"fk_{TABLE}_uploaded_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint("business_project_id", "sha256", name="uq_project_document_asset"),
    )
    op.create_index(op.f(f"ix_{TABLE}_business_project_id"), TABLE, ["business_project_id"])


def downgrade() -> None:
    op.drop_index(op.f(f"ix_{TABLE}_business_project_id"), table_name=TABLE)
    op.drop_table(TABLE)
