"""Testcase upgrade, phase 3 (docs/specs/30 §15.8-§15.10, 30a G5, G7, G10): custom fields
(test_fields + `custom` on cases and results), the approval workflow (case status "review",
test_case_reviews), bulk operations kept for undo (test_bulk_ops), the "duplicates" AI kind.

Permissions (§15.9): tests.delete and tests.approve are split from tests.edit / tests.manage; every
set holding tests.edit gets tests.delete at its scope, every set holding tests.manage gets
tests.approve, so nobody loses a right. Five sample sets (not built-in, an admin edits or assigns
them) give TestRail's roles. BUILTIN is a literal copy of app/permissions/catalog.BUILTIN_SETS at
this revision (tests compare the two).

Downgrade: "review" cases go back to "draft", "duplicates" AI runs are deleted, custom field values,
reviews and bulk history are dropped; the sample sets and the two permissions are removed.

Revision ID: f9b1d3e5a7c0
Revises: e7a9c1d3f5b8
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9b1d3e5a7c0"
down_revision: str | None = "e7a9c1d3f5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_OLD = "status IN ('draft', 'approved', 'obsolete')"
STATUS_NEW = "status IN ('draft', 'review', 'approved', 'obsolete')"
AI_OLD = "kind IN ('generate', 'audit', 'impact', 'links')"
AI_NEW = "kind IN ('generate', 'audit', 'impact', 'links', 'duplicates')"
NEW = ("tests.delete", "tests.approve")
ALL = ("tests.view", "tests.edit", "tests.delete", "tests.approve", "tests.run", "tests.manage")
# §15.9: TestRail's roles as sample sets, scope "all": every dự án the person sees (is in).
SAMPLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "Testcase - Admin dự án": ("Mọi quyền testcase trong dự án của mình", ALL),
    "Testcase - Lead": (
        "Thiết kế, xoá, duyệt testcase; ghi kết quả; quản lý run / plan / milestone",
        ALL,
    ),
    "Testcase - Tester": (
        "Thiết kế testcase và ghi kết quả; không xoá, không duyệt",
        ("tests.view", "tests.edit", "tests.run"),
    ),
    "Testcase - Developer": (
        "Xem testcase, ghi kết quả test được giao, tạo bug",
        ("tests.view", "tests.run"),
    ),
    "Testcase - Guest": ("Chỉ xem", ("tests.view",)),
}

BUILTIN: dict[str, dict[str, str]] = {
    "member": {
        "overview.view": "flag",
        "evaluation.view": "own",
        "report.member": "own",
        "report.mcp": "own",
        "report.export": "flag",
        "metrics.person": "own",
        "review.view": "own",
        "review.request": "own",
        "metrics.team": "own",
        "finding.close_request": "own",
        "dispute.raise": "own",
        "evidence.view": "own",
        "evidence.edit_self": "own",
        "dpi.view": "own",
        "dpi.self": "own",
        "dpi.dispute": "own",
        "knowledge.view": "flag",
        "telegram.self": "flag",
        "docs.view": "flag",
        "tests.view": "flag",
    },
    "lead": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "team",
        "report.member": "team",
        "report.mcp": "team",
        "report.export": "flag",
        "metrics.person": "team",
        "metrics.team": "team",
        "review.view": "team",
        "finding.close_decide": "team",
        "dispute.decide": "team",
        "dispute.ai_check": "team",
        "evidence.view": "team",
        "evidence.lead": "team",
        "dpi.view": "team",
        "dpi.assess": "team",
        "dpi.defects": "team",
    },
    "manager": {
        "insights.view": "flag",
        "members.view": "flag",
        "metrics.team": "team",
        "finding.close_decide": "team",
        "finding.lower_far": "team",
        "assistant.use": "flag",
        "dpi.distribution": "team",
    },
    "project_manager": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "project",
        "report.member": "project",
        "report.mcp": "project",
        "report.export": "flag",
        "review.view": "project",
        "review.request": "project",
        "review.rerun": "project",
        "review.author": "project",
        "knowledge.edit": "project",
        "criteria.assign": "project",
        "dpi.view": "project",
        "dpi.defects": "project",
        "docs.view": "flag",
        "docs.edit": "project",
        "tests.view": "flag",
        "tests.edit": "project",
        "tests.delete": "project",
        "tests.approve": "project",
        "tests.run": "project",
        "tests.manage": "project",
    },
    "manage_all": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "all",
        "report.member": "all",
        "report.mcp": "all",
        "report.export": "flag",
        "review.view": "all",
        "review.request": "all",
        "review.rerun": "all",
        "review.author": "all",
        "knowledge.edit": "all",
        "criteria.assign": "all",
        "dpi.view": "all",
        "dpi.distribution": "all",
        "dpi.calibrate": "all",
        "dpi.defects": "all",
        "docs.view": "flag",
        "docs.edit": "all",
        "tests.view": "flag",
        "tests.edit": "all",
        "tests.delete": "all",
        "tests.approve": "all",
        "tests.run": "all",
        "tests.manage": "all",
    },
}


def upgrade() -> None:
    op.create_table(
        "test_bulk_ops",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), server_default="", nullable=False),
        sa.Column(
            "before", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "created_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('edit', 'move', 'copy', 'replace', 'duplicates')",
            name=op.f("ck_test_bulk_ops_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_bulk_ops_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_test_bulk_ops_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_bulk_ops")),
    )
    op.create_index(
        op.f("ix_test_bulk_ops_business_project_id"),
        "test_bulk_ops",
        ["business_project_id"],
        unique=False,
    )
    op.create_index(op.f("ix_test_bulk_ops_user_id"), "test_bulk_ops", ["user_id"], unique=False)
    op.create_table(
        "test_fields",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("entity", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column(
            "options", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("required", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("default", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "suite_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "templates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("entity IN ('case', 'result')", name=op.f("ck_test_fields_entity")),
        sa.CheckConstraint(
            "type IN ('text', 'textarea', 'number', 'date', 'dropdown', 'multiselect', "
            "'checkbox', 'user', 'url')",
            name=op.f("ck_test_fields_type"),
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_test_fields_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_test_fields_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_fields")),
        sa.UniqueConstraint(
            "business_project_id", "entity", "key", name="uq_test_fields_bp_entity_key"
        ),
    )
    op.create_index(
        op.f("ix_test_fields_business_project_id"),
        "test_fields",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "test_case_reviews",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('submit', 'approve', 'reject')",
            name=op.f("ck_test_case_reviews_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["test_cases.id"],
            name=op.f("fk_test_case_reviews_case_id_test_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_test_case_reviews_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_case_reviews")),
    )
    op.create_index(
        op.f("ix_test_case_reviews_case_id"), "test_case_reviews", ["case_id"], unique=False
    )
    op.add_column(
        "test_cases",
        sa.Column(
            "custom", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )
    op.add_column(
        "test_results",
        sa.Column(
            "custom", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )

    op.drop_constraint(op.f("ck_test_cases_status"), "test_cases", type_="check")
    op.create_check_constraint(op.f("ck_test_cases_status"), "test_cases", STATUS_NEW)
    op.drop_constraint(op.f("ck_test_ai_runs_kind"), "test_ai_runs", type_="check")
    op.create_check_constraint(op.f("ck_test_ai_runs_kind"), "test_ai_runs", AI_NEW)
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT set_id, permission, scope FROM permission_set_items "
            "WHERE permission IN ('tests.edit', 'tests.manage')"
        )
    ).all()
    for set_id, permission, scope in rows:
        bind.execute(
            sa.text(
                "INSERT INTO permission_set_items (set_id, permission, scope) "
                "VALUES (:s, :p, :sc) ON CONFLICT DO NOTHING"
            ),
            {
                "s": set_id,
                "p": "tests.delete" if permission == "tests.edit" else "tests.approve",
                "sc": scope,
            },
        )
    for name, (description, keys) in SAMPLES.items():
        set_id = bind.execute(
            sa.text(
                "INSERT INTO permission_sets (name, description, builtin) "
                "VALUES (:n, :d, false) ON CONFLICT (name) DO NOTHING RETURNING id"
            ),
            {"n": name, "d": description},
        ).scalar()
        if set_id is None:
            continue  # an admin already has a set by that name: theirs stays
        for key in keys:
            bind.execute(
                sa.text(
                    "INSERT INTO permission_set_items (set_id, permission, scope) "
                    "VALUES (:s, :p, :sc) ON CONFLICT DO NOTHING"
                ),
                {"s": set_id, "p": key, "sc": "flag" if key == "tests.view" else "all"},
            )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM permission_set_items WHERE permission IN :keys").bindparams(
            sa.bindparam("keys", expanding=True, value=list(NEW))
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM permission_sets WHERE builtin = false AND name IN :names "
            "AND NOT EXISTS (SELECT 1 FROM user_permission_sets u "
            "WHERE u.set_id = permission_sets.id)"
        ).bindparams(sa.bindparam("names", expanding=True, value=list(SAMPLES)))
    )
    op.execute("DELETE FROM test_ai_runs WHERE kind = 'duplicates'")
    op.drop_constraint(op.f("ck_test_ai_runs_kind"), "test_ai_runs", type_="check")
    op.create_check_constraint(op.f("ck_test_ai_runs_kind"), "test_ai_runs", AI_OLD)
    op.execute("UPDATE test_cases SET status = 'draft' WHERE status = 'review'")
    op.drop_constraint(op.f("ck_test_cases_status"), "test_cases", type_="check")
    op.create_check_constraint(op.f("ck_test_cases_status"), "test_cases", STATUS_OLD)
    op.drop_column("test_results", "custom")
    op.drop_column("test_cases", "custom")
    op.drop_index(op.f("ix_test_case_reviews_case_id"), table_name="test_case_reviews")
    op.drop_table("test_case_reviews")
    op.drop_index(op.f("ix_test_fields_business_project_id"), table_name="test_fields")
    op.drop_table("test_fields")
    op.drop_index(op.f("ix_test_bulk_ops_user_id"), table_name="test_bulk_ops")
    op.drop_index(op.f("ix_test_bulk_ops_business_project_id"), table_name="test_bulk_ops")
    op.drop_table("test_bulk_ops")
