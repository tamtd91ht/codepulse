"""assistant_docs + assistant_doc_chunks: Stetho's knowledge base (docs/specs/28, user 2026-11-09)

The `assistant.train` permission (who may teach Stetho) needs no row: no built-in set holds it,
admins have every permission, and an admin grants it through a permission set.

Revision ID: f8b0c2d4e6a8
Revises: e7a9b1c3d5f7
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f8b0c2d4e6a8"
down_revision: str | None = "e7a9b1c3d5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        "assistant_docs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_name", sa.String(), nullable=True),
        sa.Column("paths", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column(
            "keywords", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("audience", sa.String(), server_default="all", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "source IN ('builtin', 'manual', 'file', 'url', 'qa')",
            name=op.f("ck_assistant_docs_source"),
        ),
        sa.CheckConstraint(
            "audience IN ('all', 'trainers')", name=op.f("ck_assistant_docs_audience")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_assistant_docs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_assistant_docs_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_docs")),
        sa.UniqueConstraint("key", name=op.f("uq_assistant_docs_key")),
    )
    op.create_table(
        "assistant_doc_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("doc_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(), nullable=True),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("search", postgresql.TSVECTOR(), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(
            ["doc_id"],
            ["assistant_docs.id"],
            name=op.f("fk_assistant_doc_chunks_doc_id_assistant_docs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_doc_chunks")),
    )
    op.create_index(
        op.f("ix_assistant_doc_chunks_doc_id"), "assistant_doc_chunks", ["doc_id"], unique=False
    )
    op.create_index(
        "ix_assistant_doc_chunks_search",
        "assistant_doc_chunks",
        ["search"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_doc_chunks_search", table_name="assistant_doc_chunks")
    op.drop_index(op.f("ix_assistant_doc_chunks_doc_id"), table_name="assistant_doc_chunks")
    op.drop_table("assistant_doc_chunks")
    op.drop_table("assistant_docs")
    op.execute("DELETE FROM permission_set_items WHERE permission = 'assistant.train'")
