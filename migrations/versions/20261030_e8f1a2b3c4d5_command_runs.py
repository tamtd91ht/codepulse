"""command_runs: running a repo's .claude commands from the dashboard

Outside the spec, requested by the user 2026-10-30 (app/commands). One row per run: who ran
which command where, the shas used, how it ended and what it cost. Output files are not stored
in the database; `files` lists their names and sizes until they are downloaded or expire. At
most one queued or running run per repo, and per dự án for a run without a repo (partial
unique indexes).

Revision ID: e8f1a2b3c4d5
Revises: a7c3e9d1f2b4
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8f1a2b3c4d5"
down_revision: str | None = "a7c3e9d1f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE = "status IN ('queued','running') AND project_id IS NOT NULL"
ACTIVE_SHARED = "status IN ('queued','running') AND project_id IS NULL"


def upgrade() -> None:
    op.create_table(
        "command_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("business_project_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("command", sa.String(length=200), nullable=False),
        sa.Column("arguments", sa.String(), server_default="", nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=True),
        sa.Column("repo_sha", sa.String(length=64), nullable=True),
        sa.Column("shared_ref", sa.String(length=255), nullable=True),
        sa.Column("shared_sha", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("num_turns", sa.Integer(), nullable=True),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("files_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name=op.f("ck_command_runs_status"),
        ),
        sa.CheckConstraint("source IN ('repo','shared')", name=op.f("ck_command_runs_source")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_command_runs_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_command_runs_business_project_id_business_projects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["started_by_user_id"],
            ["users.id"],
            name=op.f("fk_command_runs_started_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_command_runs")),
    )
    op.create_index("ix_command_runs_created_at", "command_runs", ["created_at"])
    op.create_index(
        "uq_command_runs_active_project",
        "command_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )
    op.create_index(
        "uq_command_runs_active_business_project",
        "command_runs",
        ["business_project_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_SHARED),
    )


def downgrade() -> None:
    op.drop_index("uq_command_runs_active_business_project", table_name="command_runs")
    op.drop_index("uq_command_runs_active_project", table_name="command_runs")
    op.drop_index("ix_command_runs_created_at", table_name="command_runs")
    op.drop_table("command_runs")
