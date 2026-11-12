"""Testcase upgrade, phase 2 (docs/specs/30 §15, 30a: G3, G4, G6): a dự án's testing settings
(assigned-only recording, approval rule, Jira bug target), custom result statuses (the CHECKs on
result / test statuses go: the application validates them, "skipped" is built in), result
attachments, the failed step of a result, report schedules.

Downgrade is lossy: tests in "skipped" or a custom status go back to "untested" and results with
one get status NULL (the old CHECKs know neither); test_attachments is dropped, so the
`{id, name, mime, size}` entries in test_results.attachments (JSONB, kept) point at files that
no longer exist.

Revision ID: e7a9c1d3f5b8
Revises: d5f7b9c1e3a6
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7a9c1d3f5b8"
down_revision: str | None = "d5f7b9c1e3a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TEST = "status IN ('untested', 'passed', 'failed', 'blocked', 'retest')"
OLD_RESULT = "status IS NULL OR status IN ('passed', 'failed', 'blocked', 'retest')"


def upgrade() -> None:
    op.create_table(
        "test_attachments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mime", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_attachments_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name=op.f("fk_test_attachments_uploaded_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_attachments")),
        sa.UniqueConstraint(
            "business_project_id", "sha256", name="uq_test_attachments_project_sha"
        ),
    )
    op.create_index(
        op.f("ix_test_attachments_business_project_id"),
        "test_attachments",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "test_project_settings",
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("assigned_only", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("require_approval", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("jira_project_key", sa.String(), nullable=True),
        sa.Column("jira_issue_type", sa.String(), server_default="Bug", nullable=False),
        sa.Column(
            "jira_components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "jira_labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("updated_by", sa.Integer(), nullable=True),
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
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_project_settings_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_test_project_settings_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("business_project_id", name=op.f("pk_test_project_settings")),
    )
    op.create_table(
        "test_statuses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=False),
        sa.Column("done", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_statuses_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_statuses")),
        sa.UniqueConstraint("business_project_id", "key", name="uq_test_statuses_project_key"),
    )
    op.create_index(
        op.f("ix_test_statuses_business_project_id"),
        "test_statuses",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "test_report_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "params", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("cadence", sa.String(), server_default="weekly", nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("hour", sa.Integer(), server_default="8", nullable=False),
        sa.Column("webhook_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
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
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_report_schedules_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_report_schedules_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["webhook_id"],
            ["notification_webhooks.id"],
            name=op.f("fk_test_report_schedules_webhook_id_notification_webhooks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_report_schedules")),
    )
    op.create_index(
        op.f("ix_test_report_schedules_business_project_id"),
        "test_report_schedules",
        ["business_project_id"],
        unique=False,
    )
    op.add_column("test_results", sa.Column("failed_step", sa.Integer(), nullable=True))

    op.drop_constraint(op.f("ck_test_run_tests_status"), "test_run_tests", type_="check")
    op.drop_constraint(op.f("ck_test_results_status"), "test_results", type_="check")


def downgrade() -> None:
    """Lossy (see the module docstring): skipped / custom statuses become untested (tests) or
    NULL (results); attachment ids left in test_results.attachments dangle."""
    # Statuses the old CHECKs do not know fall back to the nearest built-in one.
    op.execute(
        "UPDATE test_run_tests SET status = 'untested' "
        "WHERE status NOT IN ('untested', 'passed', 'failed', 'blocked', 'retest')"
    )
    op.execute(
        "UPDATE test_results SET status = NULL "
        "WHERE status NOT IN ('passed', 'failed', 'blocked', 'retest')"
    )
    op.create_check_constraint(op.f("ck_test_results_status"), "test_results", OLD_RESULT)
    op.create_check_constraint(op.f("ck_test_run_tests_status"), "test_run_tests", OLD_TEST)
    op.drop_column("test_results", "failed_step")
    op.drop_index(
        op.f("ix_test_report_schedules_business_project_id"), table_name="test_report_schedules"
    )
    op.drop_table("test_report_schedules")
    op.drop_index(op.f("ix_test_statuses_business_project_id"), table_name="test_statuses")
    op.drop_table("test_statuses")
    op.drop_table("test_project_settings")
    op.drop_index(op.f("ix_test_attachments_business_project_id"), table_name="test_attachments")
    op.drop_table("test_attachments")
