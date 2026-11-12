"""The TestRail model of test cases (docs/specs/30 §13, user 2026-11-12): section tree, the
"steps" template, case type / estimate / milestone, shared steps, milestones, configurations, test
plans, runs, tests and results. The old flat "section" text becomes folders; the TC-0001 codes
CodePulse made up are dropped (a case's key is now C<id>); type comes from the old tags.

Revision ID: a2c4e6f8b0d3
Revises: e9a1c3d5f7b2
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a2c4e6f8b0d3"
down_revision: str | None = "e9a1c3d5f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TYPES = (
    "acceptance",
    "accessibility",
    "automated",
    "compatibility",
    "destructive",
    "functional",
    "other",
    "performance",
    "regression",
    "security",
    "smoke",
    "usability",
)
TAG_TYPES = {
    "smoke": "smoke",
    "sanity": "smoke",
    "regression": "regression",
    "security": "security",
    "performance": "performance",
    "usability": "usability",
    "ui": "usability",
    "accessibility": "accessibility",
    "compatibility": "compatibility",
    "acceptance": "acceptance",
    "uat": "acceptance",
}


def _sections() -> None:
    """Each suite's distinct "section" texts become folders ("A > B" two levels)."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, suite_id, section FROM test_cases "
            "WHERE section IS NOT NULL AND btrim(section) <> '' ORDER BY suite_id, position, id"
        )
    ).all()
    made: dict[tuple[int, int | None, str], int] = {}
    position: dict[tuple[int, int | None], int] = {}
    for case_id, suite_id, text in rows:
        parent: int | None = None
        for part in [p.strip() for p in str(text).split(">") if p.strip()][:6]:
            key = (suite_id, parent, part[:200])
            if key not in made:
                position[(suite_id, parent)] = position.get((suite_id, parent), 0) + 1
                made[key] = bind.execute(
                    sa.text(
                        "INSERT INTO test_sections (suite_id, parent_id, name, position) "
                        "VALUES (:s, :p, :n, :pos) RETURNING id"
                    ),
                    {
                        "s": suite_id,
                        "p": parent,
                        "n": part[:200],
                        "pos": position[(suite_id, parent)],
                    },
                ).scalar_one()
            parent = made[key]
        if parent is not None:
            bind.execute(
                sa.text("UPDATE test_cases SET section_id = :s WHERE id = :c"),
                {"s": parent, "c": case_id},
            )


def _types() -> None:
    bind = op.get_bind()
    for case_id, tags in bind.execute(sa.text("SELECT id, tags FROM test_cases")).all():
        for tag in tags or []:
            kind = TAG_TYPES.get(str(tag).strip().lower())
            if kind:
                bind.execute(
                    sa.text("UPDATE test_cases SET type = :t WHERE id = :c"),
                    {"t": kind, "c": case_id},
                )
                break


def upgrade() -> None:
    op.create_table(
        "test_config_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
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
            name=op.f("fk_test_config_groups_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_config_groups")),
    )
    op.create_index(
        op.f("ix_test_config_groups_business_project_id"),
        "test_config_groups",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "test_milestones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("start_on", sa.Date(), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_test_milestones_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_milestones_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_milestones")),
    )
    op.create_index(
        op.f("ix_test_milestones_business_project_id"),
        "test_milestones",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "test_shared_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column(
            "steps", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
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
            name=op.f("fk_test_shared_steps_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_shared_steps_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_test_shared_steps_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_shared_steps")),
    )
    op.create_index(
        op.f("ix_test_shared_steps_business_project_id"),
        "test_shared_steps",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "test_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
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
            ["group_id"],
            ["test_config_groups.id"],
            name=op.f("fk_test_configs_group_id_test_config_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_configs")),
    )
    op.create_index(op.f("ix_test_configs_group_id"), "test_configs", ["group_id"], unique=False)
    op.create_table(
        "test_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("milestone_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_test_plans_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_plans_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["milestone_id"],
            ["test_milestones.id"],
            name=op.f("fk_test_plans_milestone_id_test_milestones"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_plans")),
    )
    op.create_index(
        op.f("ix_test_plans_business_project_id"),
        "test_plans",
        ["business_project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_plans_milestone_id"), "test_plans", ["milestone_id"], unique=False
    )
    op.create_table(
        "test_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suite_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
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
            ["parent_id"],
            ["test_sections.id"],
            name=op.f("fk_test_sections_parent_id_test_sections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["test_suites.id"],
            name=op.f("fk_test_sections_suite_id_test_suites"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_sections")),
    )
    op.create_index(
        op.f("ix_test_sections_parent_id"), "test_sections", ["parent_id"], unique=False
    )
    op.create_index(op.f("ix_test_sections_suite_id"), "test_sections", ["suite_id"], unique=False)
    op.create_table(
        "test_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("milestone_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "config_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("config", sa.String(), nullable=True),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column(
            "refs", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("source", sa.String(), server_default="manual", nullable=False),
        sa.Column(
            "source_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "source IN ('manual', 'regression', 'plan')", name=op.f("ck_test_runs_source")
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name=op.f("fk_test_runs_assigned_to_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_runs_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_runs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["milestone_id"],
            ["test_milestones.id"],
            name=op.f("fk_test_runs_milestone_id_test_milestones"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["test_plans.id"],
            name=op.f("fk_test_runs_plan_id_test_plans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_runs")),
    )
    op.create_index(
        op.f("ix_test_runs_business_project_id"), "test_runs", ["business_project_id"], unique=False
    )
    op.create_index(op.f("ix_test_runs_milestone_id"), "test_runs", ["milestone_id"], unique=False)
    op.create_index(op.f("ix_test_runs_plan_id"), "test_runs", ["plan_id"], unique=False)
    op.create_table(
        "test_run_tests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column(
            "snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("status", sa.String(), server_default="untested", nullable=False),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('untested', 'passed', 'failed', 'blocked', 'retest')",
            name=op.f("ck_test_run_tests_status"),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name=op.f("fk_test_run_tests_assigned_to_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["test_cases.id"],
            name=op.f("fk_test_run_tests_case_id_test_cases"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["test_runs.id"],
            name=op.f("fk_test_run_tests_run_id_test_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_run_tests")),
        sa.UniqueConstraint("run_id", "case_id", name="uq_test_run_tests_run_case"),
    )
    op.create_index(op.f("ix_test_run_tests_case_id"), "test_run_tests", ["case_id"], unique=False)
    op.create_index(
        "ix_test_run_tests_run_id_status", "test_run_tests", ["run_id", "status"], unique=False
    )
    op.create_table(
        "test_results",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("test_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("version", sa.String(), nullable=True),
        sa.Column("elapsed_s", sa.Integer(), nullable=True),
        sa.Column(
            "defects", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column(
            "step_results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IS NULL OR status IN ('passed', 'failed', 'blocked', 'retest')",
            name=op.f("ck_test_results_status"),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name=op.f("fk_test_results_assigned_to_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_results_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["test_run_tests.id"],
            name=op.f("fk_test_results_test_id_test_run_tests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_results")),
    )
    op.create_index(op.f("ix_test_results_test_id"), "test_results", ["test_id"], unique=False)
    op.add_column("test_cases", sa.Column("section_id", sa.Integer(), nullable=True))
    op.add_column(
        "test_cases", sa.Column("template", sa.String(), server_default="text", nullable=False)
    )
    op.add_column(
        "test_cases",
        sa.Column(
            "steps_list",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column(
        "test_cases", sa.Column("type", sa.String(), server_default="functional", nullable=False)
    )
    op.add_column("test_cases", sa.Column("estimate_s", sa.Integer(), nullable=True))
    op.add_column("test_cases", sa.Column("milestone_id", sa.Integer(), nullable=True))
    op.alter_column("test_cases", "code", existing_type=sa.VARCHAR(), nullable=True)
    op.create_index(
        op.f("ix_test_cases_milestone_id"), "test_cases", ["milestone_id"], unique=False
    )
    op.create_index(op.f("ix_test_cases_section_id"), "test_cases", ["section_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_test_cases_section_id_test_sections"),
        "test_cases",
        "test_sections",
        ["section_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_test_cases_milestone_id_test_milestones"),
        "test_cases",
        "test_milestones",
        ["milestone_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_test_cases_template"), "test_cases", "template IN ('text', 'steps')"
    )
    op.create_check_constraint(op.f("ck_test_cases_type"), "test_cases", f"type IN {TYPES}")
    _sections()
    _types()
    # The TC-0001 codes CodePulse made up: a case's key is C<id> now; imported codes stay.
    op.execute(
        "UPDATE test_cases SET code = NULL WHERE source <> 'import' AND code ~ '^TC-[0-9]{4,}$'"
    )
    op.drop_column("test_cases", "section")


def _numbered(items: list[str]) -> str | None:
    lines = [f"{n}. {text}" for n, text in enumerate(items, 1) if text.strip()]
    return "\n".join(lines) or None


def _steps_back() -> None:
    """A "steps" case's step list becomes the numbered steps / expected text the old schema had
    (shared steps written out in place)."""
    bind = op.get_bind()
    shared = {
        int(i): steps or []
        for i, steps in bind.execute(sa.text("SELECT id, steps FROM test_shared_steps")).all()
    }
    rows = bind.execute(
        sa.text(
            "SELECT id, steps_list FROM test_cases "
            "WHERE template = 'steps' AND jsonb_array_length(steps_list) > 0"
        )
    ).all()
    for case_id, steps_list in rows:
        flat: list[dict[str, object]] = []
        for step in steps_list or []:
            if not isinstance(step, dict):
                continue
            if "shared" in step:
                flat += [s for s in shared.get(int(step["shared"]), []) if isinstance(s, dict)]
            else:
                flat.append(step)
        steps = [str(s.get("content") or "").strip() for s in flat]
        expected = [str(s.get("expected") or "").strip() for s in flat]
        bind.execute(
            sa.text("UPDATE test_cases SET steps = :s, expected = :e WHERE id = :c"),
            {"s": _numbered(steps), "e": _numbered(expected), "c": case_id},
        )


def downgrade() -> None:
    op.add_column(
        "test_cases", sa.Column("section", sa.VARCHAR(), autoincrement=False, nullable=True)
    )
    # The folder's full path "A > B", as the old flat text was.
    op.execute(
        "WITH RECURSIVE tree(id, path) AS ("
        " SELECT id, name::text FROM test_sections WHERE parent_id IS NULL"
        " UNION ALL"
        " SELECT s.id, t.path || ' > ' || s.name FROM test_sections s"
        " JOIN tree t ON s.parent_id = t.id"
        ") UPDATE test_cases c SET section = tree.path FROM tree WHERE tree.id = c.section_id"
    )
    _steps_back()
    # Codes are required again: C<id>, unless that is another case's code in the suite.
    op.execute(
        "UPDATE test_cases c SET code = CASE WHEN EXISTS ("
        " SELECT 1 FROM test_cases o WHERE o.suite_id = c.suite_id AND o.code = 'C' || c.id"
        ") THEN 'C' || c.id || '-' || c.id ELSE 'C' || c.id END WHERE c.code IS NULL"
    )
    op.drop_constraint(op.f("ck_test_cases_type"), "test_cases", type_="check")
    op.drop_constraint(op.f("ck_test_cases_template"), "test_cases", type_="check")
    op.drop_constraint(
        op.f("fk_test_cases_milestone_id_test_milestones"), "test_cases", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_test_cases_section_id_test_sections"), "test_cases", type_="foreignkey"
    )
    op.drop_index(op.f("ix_test_cases_section_id"), table_name="test_cases")
    op.drop_index(op.f("ix_test_cases_milestone_id"), table_name="test_cases")
    op.alter_column("test_cases", "code", existing_type=sa.VARCHAR(), nullable=False)
    op.drop_column("test_cases", "milestone_id")
    op.drop_column("test_cases", "estimate_s")
    op.drop_column("test_cases", "type")
    op.drop_column("test_cases", "steps_list")
    op.drop_column("test_cases", "template")
    op.drop_column("test_cases", "section_id")
    op.drop_index(op.f("ix_test_results_test_id"), table_name="test_results")
    op.drop_table("test_results")
    op.drop_index("ix_test_run_tests_run_id_status", table_name="test_run_tests")
    op.drop_index(op.f("ix_test_run_tests_case_id"), table_name="test_run_tests")
    op.drop_table("test_run_tests")
    op.drop_index(op.f("ix_test_runs_plan_id"), table_name="test_runs")
    op.drop_index(op.f("ix_test_runs_milestone_id"), table_name="test_runs")
    op.drop_index(op.f("ix_test_runs_business_project_id"), table_name="test_runs")
    op.drop_table("test_runs")
    op.drop_index(op.f("ix_test_sections_suite_id"), table_name="test_sections")
    op.drop_index(op.f("ix_test_sections_parent_id"), table_name="test_sections")
    op.drop_table("test_sections")
    op.drop_index(op.f("ix_test_plans_milestone_id"), table_name="test_plans")
    op.drop_index(op.f("ix_test_plans_business_project_id"), table_name="test_plans")
    op.drop_table("test_plans")
    op.drop_index(op.f("ix_test_configs_group_id"), table_name="test_configs")
    op.drop_table("test_configs")
    op.drop_index(op.f("ix_test_shared_steps_business_project_id"), table_name="test_shared_steps")
    op.drop_table("test_shared_steps")
    op.drop_index(op.f("ix_test_milestones_business_project_id"), table_name="test_milestones")
    op.drop_table("test_milestones")
    op.drop_index(
        op.f("ix_test_config_groups_business_project_id"), table_name="test_config_groups"
    )
    op.drop_table("test_config_groups")
