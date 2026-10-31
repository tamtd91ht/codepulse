"""finding_closures: closing critical/high findings a later change fixed

Outside the spec, requested by the user 2026-10-31 (app/review/resolve.py). One row per attempt:
closed automatically by a later review's change; asked for by the author with the fixing commits,
then refused or checked (by a worker) at the request of a manager of their team or an admin;
closed or reopened by an admin with a note. Closing only moves the finding to `fixed`;
its severity and its weight in the review's score stay. At most one pending request per finding.

Revision ID: b2c4d6e8f0a1
Revises: e8f1a2b3c4d5
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c4d6e8f0a1"
down_revision: str | None = "e8f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "finding_closures",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("finding_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("shas", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("review_id", sa.BigInteger(), nullable=True),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("evidence", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "kind IN ('auto','request','admin','reopen')", name=op.f("ck_finding_closures_kind")
        ),
        sa.CheckConstraint(
            "status IN ('pending','checking','closed','rejected','failed','cancelled','reopened')",
            name=op.f("ck_finding_closures_status"),
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["review_findings.id"],
            name=op.f("fk_finding_closures_finding_id_review_findings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["reviews.id"],
            name=op.f("fk_finding_closures_review_id_reviews"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_finding_closures_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_finding_closures_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finding_closures")),
    )
    op.create_index("ix_finding_closures_created_at", "finding_closures", ["created_at"])
    op.create_index(
        op.f("ix_finding_closures_finding_id"), "finding_closures", ["finding_id"], unique=False
    )
    op.create_index(
        "uq_finding_closures_pending",
        "finding_closures",
        ["finding_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','checking')"),
    )


def downgrade() -> None:
    op.drop_index("uq_finding_closures_pending", table_name="finding_closures")
    op.drop_index(op.f("ix_finding_closures_finding_id"), table_name="finding_closures")
    op.drop_index("ix_finding_closures_created_at", table_name="finding_closures")
    op.drop_table("finding_closures")
