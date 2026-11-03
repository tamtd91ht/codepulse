"""evidence_packs, evidence_items: one person's month for the 1:1 (docs/specs/20 §5.4, §6)

Phase 9.10, decisions D1 (coaching only, no score) and D4 (the person, their lead, admins).

Revision ID: c9d1e3f5a7b9
Revises: b8c0d2e4f6a8
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d1e3f5a7b9"
down_revision: str | None = "b8c0d2e4f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = "('draft', 'submitted', 'reviewed', 'shared')"
KINDS = (
    "('mr', 'commit', 'review_given', 'finding_fixed', 'incident', 'design', 'mentoring', 'other')"
)


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "evidence_packs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("self_notes", sa.String(), nullable=True),
        sa.Column("lead_notes", sa.String(), nullable=True),
        sa.Column("ai_draft", sa.String(), nullable=True),
        sa.Column("goals_next", sa.String(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("insights_version", sa.String(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(f"status IN {STATUSES}", name=op.f("ck_evidence_packs_status")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_evidence_packs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name=op.f("fk_evidence_packs_reviewed_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_packs")),
        sa.UniqueConstraint(
            "user_id", "period_start", name=op.f("uq_evidence_packs_user_id_period_start")
        ),
    )
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("pack_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("added_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(f"kind IN {KINDS}", name=op.f("ck_evidence_items_kind")),
        sa.ForeignKeyConstraint(
            ["pack_id"],
            ["evidence_packs.id"],
            name=op.f("fk_evidence_items_pack_id_evidence_packs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["users.id"],
            name=op.f("fk_evidence_items_added_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_items")),
    )
    op.create_index(op.f("ix_evidence_items_pack_id"), "evidence_items", ["pack_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_evidence_items_pack_id"), table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_table("evidence_packs")
