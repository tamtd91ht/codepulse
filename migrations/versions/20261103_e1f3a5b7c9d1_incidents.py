"""incidents: GitLab incidents for DORA (docs/specs/20 §5.3, decision D3)

Issues of type incident, or carrying an INCIDENT_LABELS label; their fix = the MRs GitLab says
close them. Team level only.

Revision ID: e1f3a5b7c9d1
Revises: d0e2f4a6b8c0
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f3a5b7c9d1"
down_revision: str | None = "d0e2f4a6b8c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("iid", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("web_url", sa.String(), nullable=True),
        sa.Column("created_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fix_mr_iids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_incidents_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
        sa.UniqueConstraint("project_id", "iid", name=op.f("uq_incidents_project_id_iid")),
    )
    op.create_index(
        op.f("ix_incidents_created_at_src"), "incidents", ["created_at_src"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_incidents_created_at_src"), table_name="incidents")
    op.drop_table("incidents")
