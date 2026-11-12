"""Testcase upgrade, phase 4 (docs/specs/30 §15.12-§15.15, 30a G8, G9, G12, G13): BDD and
exploratory templates (`bdd`, `charter`; a test's exploratory `session`), baselines
(test_baselines, test_baseline_cases; a run made from one keeps its content), the dự án of an audit
row (and an index on its target), parameterised cases (`params`; one test per parameter row, so a
run's tests are unique per (run, case, row)). The "convert" AI kind, the "baseline" run source.

Downgrade: BDD / exploratory cases go back to "text", "convert" AI runs and "baseline" run sources
go, and a parameterised case keeps only its first row's test in each run (the results of the other
rows are deleted); baselines and parameters are dropped.

Revision ID: a1c3e5f7b9d2
Revises: f9b1d3e5a7c0
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c3e5f7b9d2"
down_revision: str | None = "f9b1d3e5a7c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEMPLATE_OLD = "template IN ('text', 'steps')"
TEMPLATE_NEW = "template IN ('text', 'steps', 'bdd', 'exploratory')"
SOURCE_OLD = "source IN ('manual', 'regression', 'plan', 'ci')"
SOURCE_NEW = "source IN ('manual', 'regression', 'plan', 'ci', 'baseline')"
AI_OLD = "kind IN ('generate', 'audit', 'impact', 'links', 'duplicates')"
AI_NEW = "kind IN ('generate', 'audit', 'impact', 'links', 'duplicates', 'convert')"


def upgrade() -> None:
    op.create_table(
        "test_baselines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("suite_id", sa.Integer(), nullable=True),
        sa.Column("suite_name", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("milestone_id", sa.Integer(), nullable=True),
        sa.Column("case_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_baselines_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_baselines_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["milestone_id"],
            ["test_milestones.id"],
            name=op.f("fk_test_baselines_milestone_id_test_milestones"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["test_suites.id"],
            name=op.f("fk_test_baselines_suite_id_test_suites"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_baselines")),
    )
    op.create_index(
        op.f("ix_test_baselines_business_project_id"),
        "test_baselines",
        ["business_project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_baselines_suite_id"), "test_baselines", ["suite_id"], unique=False
    )
    op.create_table(
        "test_baseline_cases",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("baseline_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column("section_id", sa.Integer(), nullable=True),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["baseline_id"],
            ["test_baselines.id"],
            name=op.f("fk_test_baseline_cases_baseline_id_test_baselines"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["test_cases.id"],
            name=op.f("fk_test_baseline_cases_case_id_test_cases"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_baseline_cases")),
    )
    op.create_index(
        "ix_test_baseline_cases_baseline_position",
        "test_baseline_cases",
        ["baseline_id", "position"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_baseline_cases_case_id"), "test_baseline_cases", ["case_id"], unique=False
    )
    op.add_column("audit_log", sa.Column("business_project_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_audit_log_business_project_id_at",
        "audit_log",
        ["business_project_id", "at"],
        unique=False,
    )
    op.create_index("ix_audit_log_target", "audit_log", ["target"], unique=False)
    op.create_foreign_key(
        op.f("fk_audit_log_business_project_id_business_projects"),
        "audit_log",
        "business_projects",
        ["business_project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("test_cases", sa.Column("bdd", sa.String(), nullable=True))
    op.add_column("test_cases", sa.Column("charter", sa.String(), nullable=True))
    op.add_column(
        "test_cases",
        sa.Column(
            "params", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )
    op.add_column("test_run_tests", sa.Column("param_row", sa.Integer(), nullable=True))
    op.add_column(
        "test_run_tests",
        sa.Column(
            "params", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )
    op.add_column(
        "test_run_tests",
        sa.Column(
            "session", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )
    op.drop_constraint(op.f("uq_test_run_tests_run_case"), "test_run_tests", type_="unique")
    op.create_index(
        "uq_test_run_tests_run_case_row",
        "test_run_tests",
        ["run_id", "case_id", sa.literal_column("coalesce(param_row, 0)")],
        unique=True,
    )
    op.add_column("test_runs", sa.Column("baseline_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_test_runs_baseline_id"), "test_runs", ["baseline_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_test_runs_baseline_id_test_baselines"),
        "test_runs",
        "test_baselines",
        ["baseline_id"],
        ["id"],
        ondelete="SET NULL",
    )

    for name, table, rule in (
        ("ck_test_cases_template", "test_cases", TEMPLATE_NEW),
        ("ck_test_runs_source", "test_runs", SOURCE_NEW),
        ("ck_test_ai_runs_kind", "test_ai_runs", AI_NEW),
    ):
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, rule)


def downgrade() -> None:
    op.execute("UPDATE test_cases SET template = 'text' WHERE template IN ('bdd', 'exploratory')")
    op.execute("UPDATE test_runs SET source = 'manual' WHERE source = 'baseline'")
    op.execute("DELETE FROM test_ai_runs WHERE kind = 'convert'")
    op.execute("DELETE FROM test_run_tests WHERE param_row > 1")
    # A case added once without a row and once as row 1 (it gained a table in between): keep
    # the row-less test, or (run_id, case_id) would not be unique again.
    op.execute(
        "DELETE FROM test_run_tests t WHERE t.param_row = 1 AND EXISTS ("
        "SELECT 1 FROM test_run_tests o WHERE o.run_id = t.run_id AND o.case_id = t.case_id "
        "AND o.param_row IS NULL)"
    )
    op.execute("UPDATE test_run_tests SET param_row = NULL WHERE param_row IS NOT NULL")
    for name, table, rule in (
        ("ck_test_cases_template", "test_cases", TEMPLATE_OLD),
        ("ck_test_runs_source", "test_runs", SOURCE_OLD),
        ("ck_test_ai_runs_kind", "test_ai_runs", AI_OLD),
    ):
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, rule)
    op.drop_constraint(
        op.f("fk_test_runs_baseline_id_test_baselines"), "test_runs", type_="foreignkey"
    )
    op.drop_index(op.f("ix_test_runs_baseline_id"), table_name="test_runs")
    op.drop_column("test_runs", "baseline_id")
    op.drop_index("uq_test_run_tests_run_case_row", table_name="test_run_tests")
    op.create_unique_constraint(
        op.f("uq_test_run_tests_run_case"),
        "test_run_tests",
        ["run_id", "case_id"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_column("test_run_tests", "session")
    op.drop_column("test_run_tests", "params")
    op.drop_column("test_run_tests", "param_row")
    op.drop_column("test_cases", "params")
    op.drop_column("test_cases", "charter")
    op.drop_column("test_cases", "bdd")
    op.drop_constraint(
        op.f("fk_audit_log_business_project_id_business_projects"), "audit_log", type_="foreignkey"
    )
    op.drop_index("ix_audit_log_target", table_name="audit_log")
    op.drop_index("ix_audit_log_business_project_id_at", table_name="audit_log")
    op.drop_column("audit_log", "business_project_id")
    op.drop_index(op.f("ix_test_baseline_cases_case_id"), table_name="test_baseline_cases")
    op.drop_index("ix_test_baseline_cases_baseline_position", table_name="test_baseline_cases")
    op.drop_table("test_baseline_cases")
    op.drop_index(op.f("ix_test_baselines_suite_id"), table_name="test_baselines")
    op.drop_index(op.f("ix_test_baselines_business_project_id"), table_name="test_baselines")
    op.drop_table("test_baselines")
