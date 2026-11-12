"""Testcase upgrade, phase 1 (docs/specs/30 §15, 30a: G11, G1, G2): webhook deliveries kept
for retry, API tokens bound to a dự án, CI results (source, log, stack, attachments, CI facts),
CI report imports, a case's automation_id and flaky mark, code globs on suites and sections,
the AI's "links" runs and CI runs.

Revision ID: d5f7b9c1e3a6
Revises: c4e6a8b0d2f5
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5f7b9c1e3a6"
down_revision: str | None = "c4e6a8b0d2f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_SOURCES_OLD = "source IN ('manual', 'regression', 'plan')"
RUN_SOURCES_NEW = "source IN ('manual', 'regression', 'plan', 'ci')"
AI_KINDS_OLD = "kind IN ('generate', 'audit', 'impact')"
AI_KINDS_NEW = "kind IN ('generate', 'audit', 'impact', 'links')"


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("webhook_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')",
            name=op.f("ck_webhook_deliveries_status"),
        ),
        sa.ForeignKeyConstraint(
            ["webhook_id"],
            ["notification_webhooks.id"],
            name=op.f("fk_webhook_deliveries_webhook_id_notification_webhooks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
    )
    op.create_index(
        "ix_webhook_deliveries_webhook_id_created_at",
        "webhook_deliveries",
        ["webhook_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "test_ci_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("suite_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), server_default="queued", nullable=False),
        sa.Column("format", sa.String(), nullable=True),
        sa.Column("file_name", sa.String(), nullable=True),
        sa.Column(
            "params", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("done", sa.Integer(), server_default="0", nullable=False),
        sa.Column("mapped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_cases", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "unmapped", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name=op.f("ck_test_ci_imports_status"),
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_ci_imports_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_ci_imports_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["test_runs.id"],
            name=op.f("fk_test_ci_imports_run_id_test_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["test_suites.id"],
            name=op.f("fk_test_ci_imports_suite_id_test_suites"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_ci_imports")),
    )
    op.create_index(
        op.f("ix_test_ci_imports_business_project_id"),
        "test_ci_imports",
        ["business_project_id"],
        unique=False,
    )
    op.create_index(op.f("ix_test_ci_imports_run_id"), "test_ci_imports", ["run_id"], unique=False)
    op.create_index(
        op.f("ix_test_ci_imports_suite_id"), "test_ci_imports", ["suite_id"], unique=False
    )
    op.add_column("api_tokens", sa.Column("business_project_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_api_tokens_business_project_id"),
        "api_tokens",
        ["business_project_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_api_tokens_business_project_id_business_projects"),
        "api_tokens",
        "business_projects",
        ["business_project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column("test_cases", sa.Column("automation_id", sa.String(), nullable=True))
    op.add_column(
        "test_cases", sa.Column("flaky", sa.Boolean(), server_default="false", nullable=False)
    )
    op.create_index(
        "uq_test_cases_suite_automation_id",
        "test_cases",
        ["suite_id", "automation_id"],
        unique=True,
        postgresql_where="automation_id IS NOT NULL",
    )
    op.add_column(
        "test_results", sa.Column("source", sa.String(), server_default="manual", nullable=False)
    )
    op.add_column("test_results", sa.Column("log", sa.String(), nullable=True))
    op.add_column("test_results", sa.Column("stack", sa.String(), nullable=True))
    op.add_column(
        "test_results",
        sa.Column(
            "attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column(
        "test_results",
        sa.Column(
            "ci", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )
    op.add_column(
        "test_sections",
        sa.Column(
            "code_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column(
        "test_suites",
        sa.Column(
            "code_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )

    op.create_check_constraint(
        op.f("ck_test_results_source"), "test_results", "source IN ('manual', 'ci')"
    )
    op.drop_constraint(op.f("ck_test_runs_source"), "test_runs", type_="check")
    op.create_check_constraint(op.f("ck_test_runs_source"), "test_runs", RUN_SOURCES_NEW)
    op.drop_constraint(op.f("ck_test_ai_runs_kind"), "test_ai_runs", type_="check")
    op.create_check_constraint(op.f("ck_test_ai_runs_kind"), "test_ai_runs", AI_KINDS_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM test_ai_runs WHERE kind = 'links'")
    op.drop_constraint(op.f("ck_test_ai_runs_kind"), "test_ai_runs", type_="check")
    op.create_check_constraint(op.f("ck_test_ai_runs_kind"), "test_ai_runs", AI_KINDS_OLD)
    op.execute("UPDATE test_runs SET source = 'manual' WHERE source = 'ci'")
    op.drop_constraint(op.f("ck_test_runs_source"), "test_runs", type_="check")
    op.create_check_constraint(op.f("ck_test_runs_source"), "test_runs", RUN_SOURCES_OLD)
    op.drop_constraint(op.f("ck_test_results_source"), "test_results", type_="check")
    op.drop_column("test_suites", "code_paths")
    op.drop_column("test_sections", "code_paths")
    op.drop_column("test_results", "ci")
    op.drop_column("test_results", "attachments")
    op.drop_column("test_results", "stack")
    op.drop_column("test_results", "log")
    op.drop_column("test_results", "source")
    op.drop_index(
        "uq_test_cases_suite_automation_id",
        table_name="test_cases",
        postgresql_where="automation_id IS NOT NULL",
    )
    op.drop_column("test_cases", "flaky")
    op.drop_column("test_cases", "automation_id")
    # A token bound to one dự án would reach every dự án of its owner once the column is gone:
    # revoke it so a downgrade never widens a token.
    op.execute(
        "UPDATE api_tokens SET revoked_at = now(), revoked_by = NULL "
        "WHERE business_project_id IS NOT NULL AND revoked_at IS NULL"
    )
    op.drop_constraint(
        op.f("fk_api_tokens_business_project_id_business_projects"),
        "api_tokens",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_api_tokens_business_project_id"), table_name="api_tokens")
    op.drop_column("api_tokens", "business_project_id")
    op.drop_index(op.f("ix_test_ci_imports_suite_id"), table_name="test_ci_imports")
    op.drop_index(op.f("ix_test_ci_imports_run_id"), table_name="test_ci_imports")
    op.drop_index(op.f("ix_test_ci_imports_business_project_id"), table_name="test_ci_imports")
    op.drop_table("test_ci_imports")
    op.drop_index("ix_webhook_deliveries_webhook_id_created_at", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
