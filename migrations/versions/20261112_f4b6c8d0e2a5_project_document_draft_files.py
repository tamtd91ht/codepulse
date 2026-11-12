"""project_document_drafts.files: uploaded files as sources of Tổng hợp bằng AI
(docs/specs/29 §12, user 2026-11-12). Only their text is kept, never the file.

Revision ID: f4b6c8d0e2a5
Revises: e3a5b7c9d1f4
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4b6c8d0e2a5"
down_revision: str | None = "e3a5b7c9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_document_drafts",
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_document_drafts", "files")
