"""test_suites, test_suite_links, test_cases, test_case_revisions, test_ai_runs: the test cases of
a dự án and the AI's work on them (docs/specs/30, user 2026-11-12).

Revision ID: e9a1c3d5f7b2
Revises: d8f0b2c4e6a9
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e9a1c3d5f7b2"
down_revision: str | None = "d8f0b2c4e6a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "test_suites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("source_sheet", sa.String(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
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
            name=op.f("fk_test_suites_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_suites_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_test_suites_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_suites")),
    )
    op.create_index(
        op.f("ix_test_suites_business_project_id"),
        "test_suites",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "test_cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suite_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("preconditions", sa.String(), nullable=True),
        sa.Column("steps", sa.String(), nullable=True),
        sa.Column("test_data", sa.String(), nullable=True),
        sa.Column("expected", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("priority", sa.String(), server_default="medium", nullable=False),
        sa.Column("kind", sa.String(), server_default="manual", nullable=False),
        sa.Column("automation", sa.String(), nullable=True),
        sa.Column(
            "tags", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column(
            "jira_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "code_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        sa.Column("source", sa.String(), server_default="manual", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("search", sa.String(), server_default="", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
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
        sa.CheckConstraint("kind IN ('manual', 'automated')", name=op.f("ck_test_cases_kind")),
        sa.CheckConstraint(
            "priority IN ('critical', 'high', 'medium', 'low')", name=op.f("ck_test_cases_priority")
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'import', 'ai')", name=op.f("ck_test_cases_source")
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'obsolete')", name=op.f("ck_test_cases_status")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_cases_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["test_suites.id"],
            name=op.f("fk_test_cases_suite_id_test_suites"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_test_cases_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_cases")),
        sa.UniqueConstraint("suite_id", "code", name="uq_test_cases_suite_code"),
    )
    op.create_index(
        "ix_test_cases_jira_keys",
        "test_cases",
        ["jira_keys"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"jira_keys": "jsonb_path_ops"},
    )
    op.create_index(op.f("ix_test_cases_suite_id"), "test_cases", ["suite_id"], unique=False)
    op.create_table(
        "test_suite_links",
        sa.Column("suite_id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("linked_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_suite_links_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by"],
            ["users.id"],
            name=op.f("fk_test_suite_links_linked_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["test_suites.id"],
            name=op.f("fk_test_suite_links_suite_id_test_suites"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "suite_id", "business_project_id", name=op.f("pk_test_suite_links")
        ),
    )
    op.create_index(
        op.f("ix_test_suite_links_business_project_id"),
        "test_suite_links",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "test_ai_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("suite_id", sa.Integer(), nullable=True),
        sa.Column("merge_request_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "input", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(), server_default="queued", nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("applied_count", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('generate', 'audit', 'impact')", name=op.f("ck_test_ai_runs_kind")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'fetching', 'writing', 'ready', 'failed', 'applied', "
            "'discarded')",
            name=op.f("ck_test_ai_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_ai_runs_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_ai_runs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["merge_request_id"],
            ["merge_requests.id"],
            name=op.f("fk_test_ai_runs_merge_request_id_merge_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_test_ai_runs_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["test_suites.id"],
            name=op.f("fk_test_ai_runs_suite_id_test_suites"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_ai_runs")),
    )
    op.create_index(
        op.f("ix_test_ai_runs_business_project_id"),
        "test_ai_runs",
        ["business_project_id"],
        unique=False,
    )
    op.create_index(
        "ix_test_ai_runs_created_by_status", "test_ai_runs", ["created_by", "status"], unique=False
    )
    op.create_index(
        op.f("ix_test_ai_runs_merge_request_id"), "test_ai_runs", ["merge_request_id"], unique=False
    )
    op.create_index(op.f("ix_test_ai_runs_suite_id"), "test_ai_runs", ["suite_id"], unique=False)
    op.create_table(
        "test_case_revisions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("via", sa.String(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "via IN ('editor', 'import', 'ai', 'restore', 'bulk')",
            name=op.f("ck_test_case_revisions_via"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["test_cases.id"],
            name=op.f("fk_test_case_revisions_case_id_test_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by"],
            ["users.id"],
            name=op.f("fk_test_case_revisions_changed_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_case_revisions")),
        sa.UniqueConstraint("case_id", "number", name="uq_test_case_revisions_case_number"),
    )
    op.create_index(
        op.f("ix_test_case_revisions_case_id"), "test_case_revisions", ["case_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_test_case_revisions_case_id"), table_name="test_case_revisions")
    op.drop_table("test_case_revisions")
    op.drop_index(op.f("ix_test_ai_runs_suite_id"), table_name="test_ai_runs")
    op.drop_index(op.f("ix_test_ai_runs_merge_request_id"), table_name="test_ai_runs")
    op.drop_index("ix_test_ai_runs_created_by_status", table_name="test_ai_runs")
    op.drop_index(op.f("ix_test_ai_runs_business_project_id"), table_name="test_ai_runs")
    op.drop_table("test_ai_runs")
    op.drop_index(op.f("ix_test_suite_links_business_project_id"), table_name="test_suite_links")
    op.drop_table("test_suite_links")
    op.drop_index(op.f("ix_test_cases_suite_id"), table_name="test_cases")
    op.drop_index(
        "ix_test_cases_jira_keys",
        table_name="test_cases",
        postgresql_using="gin",
        postgresql_ops={"jira_keys": "jsonb_path_ops"},
    )
    op.drop_table("test_cases")
    op.drop_index(op.f("ix_test_suites_business_project_id"), table_name="test_suites")
    op.drop_table("test_suites")
