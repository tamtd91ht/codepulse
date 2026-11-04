"""DPI (docs/specs/25): issues and their MRs, MR labels and checklist, pipeline coverage and
tests, production defects, review comment labels, the rubric, profiles, the monthly cycle and
its snapshots; the DPI permissions in the built-in sets.

RUBRIC / PROFILES / BUILTIN are literal copies of app/dpi/rubric.py and
app/permissions/catalog.BUILTIN_SETS at this revision (tests compare them). Only the new
permission items are inserted: what an admin changed in the built-in sets stays.

Revision ID: e7b9d1f3a5c7
Revises: e5a7c9b1d3f5
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, insert

revision: str = "e7b9d1f3a5c7"
down_revision: str | None = "e5a7c9b1d3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUBRIC_VERSION = "dpi-v1"
RUBRIC = [
    {
        "key": "A1",
        "group": "A",
        "max": 15,
        "source": "auto",
        "label": "Complexity-adjusted delivery",
    },
    {"key": "A2", "group": "A", "max": 5, "source": "auto", "label": "Completed contributions"},
    {
        "key": "A3",
        "group": "A",
        "max": 5,
        "source": "manager",
        "label": "Business/Technical impact",
    },
    {"key": "B1", "group": "B", "max": 8, "source": "auto", "label": "Quality Gate Compliance"},
    {"key": "B2", "group": "B", "max": 8, "source": "auto", "label": "Escaped Defects"},
    {"key": "B3", "group": "B", "max": 5, "source": "auto", "label": "Automated Test Adequacy"},
    {"key": "B4", "group": "B", "max": 4, "source": "auto", "label": "Maintainability & Rework"},
    {"key": "B5", "group": "B", "max": 5, "source": "manager", "label": "Code Design Quality"},
    {"key": "C1", "group": "C", "max": 5, "source": "auto", "label": "Development Flow Efficiency"},
    {"key": "C2", "group": "C", "max": 6, "source": "auto", "label": "Delivery Predictability"},
    {"key": "C3", "group": "C", "max": 4, "source": "auto", "label": "Process Compliance"},
    {
        "key": "D1",
        "group": "D",
        "max": 6,
        "source": "auto",
        "label": "Meaningful Review Contributions",
    },
    {"key": "D2", "group": "D", "max": 4, "source": "auto", "label": "Knowledge Sharing"},
    {"key": "D3", "group": "D", "max": 5, "source": "manager", "label": "Teamwork Quality"},
    {
        "key": "E1",
        "group": "E",
        "max": 5,
        "source": "manager",
        "label": "Ownership & accountability",
    },
    {"key": "E2", "group": "E", "max": 5, "source": "manager", "label": "Learning & growth"},
    {"key": "E3", "group": "E", "max": 5, "source": "manager", "label": "Initiative & mentoring"},
]
PROFILES = [
    {
        "key": "junior",
        "label": "Junior",
        "weights": {"A": 30, "B": 35, "C": 20, "D": 10, "E": 5},
        "a1_target": 20,
    },
    {
        "key": "middle",
        "label": "Middle",
        "weights": {"A": 25, "B": 30, "C": 15, "D": 15, "E": 15},
        "a1_target": 28,
    },
    {
        "key": "senior",
        "label": "Senior",
        "weights": {"A": 20, "B": 30, "C": 15, "D": 15, "E": 20},
        "a1_target": 32,
    },
    {
        "key": "tech_lead",
        "label": "Tech Lead",
        "weights": {"A": 15, "B": 25, "C": 15, "D": 20, "E": 25},
        "a1_target": 20,
    },
    {
        "key": "devops_sre",
        "label": "DevOps / SRE",
        "weights": {"A": 25, "B": 30, "C": 15, "D": 15, "E": 15},
        "a1_target": 28,
    },
    {
        "key": "qa_automation",
        "label": "QA Automation",
        "weights": {"A": 25, "B": 30, "C": 15, "D": 15, "E": 15},
        "a1_target": 28,
    },
    {
        "key": "ai_ml",
        "label": "AI / ML",
        "weights": {"A": 25, "B": 30, "C": 15, "D": 15, "E": 15},
        "a1_target": 28,
    },
]
BUILTIN = {
    "member": {
        "overview.view": "flag",
        "evaluation.view": "own",
        "report.member": "own",
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
    },
    "lead": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "team",
        "report.member": "team",
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
        "report.export": "flag",
        "review.view": "project",
        "review.request": "project",
        "review.rerun": "project",
        "review.author": "project",
        "knowledge.edit": "project",
        "criteria.assign": "project",
        "dpi.view": "project",
        "dpi.defects": "project",
    },
    "manage_all": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "all",
        "report.member": "all",
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
    },
}
NEW = (
    "dpi.view",
    "dpi.self",
    "dpi.dispute",
    "dpi.assess",
    "dpi.distribution",
    "dpi.calibrate",
    "dpi.final_view",
    "dpi.defects",
    "dpi.admin",
)


def _ts() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.add_column("merge_requests", sa.Column("labels", JSONB(), nullable=True))
    op.add_column("merge_requests", sa.Column("tasks_total", sa.Integer(), nullable=True))
    op.add_column("merge_requests", sa.Column("tasks_done", sa.Integer(), nullable=True))
    op.add_column("pipelines", sa.Column("source", sa.String(), nullable=True))
    op.add_column("pipelines", sa.Column("coverage", sa.Numeric(5, 2), nullable=True))
    op.add_column("pipelines", sa.Column("tests_total", sa.Integer(), nullable=True))
    op.add_column("pipelines", sa.Column("tests_failed", sa.Integer(), nullable=True))
    op.add_column("pipelines", sa.Column("tests_skipped", sa.Integer(), nullable=True))

    op.create_table(
        "issues",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("iid", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("labels", JSONB(), nullable=True),
        sa.Column("assignee_account_ids", JSONB(), nullable=True),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("complexity", sa.String(), nullable=True),
        sa.Column("complexity_points", sa.Integer(), nullable=True),
        sa.Column("prod_severity", sa.String(), nullable=True),
        sa.Column("milestone", sa.String(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("web_url", sa.String(), nullable=True),
        sa.Column("created_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_estimate_s", sa.Integer(), nullable=True),
        sa.Column("time_spent_s", sa.Integer(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_issues_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issues")),
        sa.UniqueConstraint("project_id", "iid", name=op.f("uq_issues_project_id_iid")),
    )
    op.create_index("ix_issues_closed_at_src", "issues", ["closed_at_src"])
    op.create_index("ix_issues_due_date", "issues", ["due_date"])

    op.create_table(
        "issue_mr_links",
        sa.Column("issue_id", sa.BigInteger(), nullable=False),
        sa.Column("mr_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        *_ts(),
        sa.CheckConstraint(
            "source IN ('closes', 'mention', 'branch')", name=op.f("ck_issue_mr_links_source")
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["issues.id"],
            name=op.f("fk_issue_mr_links_issue_id_issues"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mr_id"],
            ["merge_requests.id"],
            name=op.f("fk_issue_mr_links_mr_id_merge_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("issue_id", "mr_id", name=op.f("pk_issue_mr_links")),
    )
    op.create_index(op.f("ix_issue_mr_links_mr_id"), "issue_mr_links", ["mr_id"])

    op.create_table(
        "prod_defects",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("issue_id", sa.BigInteger(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("suggestions", JSONB(), nullable=True),
        sa.Column("root_commit_sha", sa.String(), nullable=True),
        sa.Column("root_mr_id", sa.BigInteger(), nullable=True),
        sa.Column("rca_status", sa.String(), server_default="pending", nullable=False),
        sa.Column("responsible", JSONB(), nullable=True),
        sa.Column("process_gaps", JSONB(), nullable=True),
        sa.Column("rca_note", sa.String(), nullable=True),
        sa.Column("rca_by", sa.Integer(), nullable=True),
        sa.Column("rca_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["issues.id"],
            name=op.f("fk_prod_defects_issue_id_issues"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["root_mr_id"],
            ["merge_requests.id"],
            name=op.f("fk_prod_defects_root_mr_id_merge_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rca_by"], ["users.id"], name=op.f("fk_prod_defects_rca_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prod_defects")),
        sa.UniqueConstraint("issue_id", name=op.f("uq_prod_defects_issue_id")),
    )

    op.create_table(
        "review_comments",
        sa.Column("note_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("by", sa.String(), server_default="rule", nullable=False),
        sa.Column("acted_on", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        *_ts(),
        sa.CheckConstraint(
            "label IN ('actionable', 'design', 'correctness', 'security', 'testability', "
            "'cosmetic', 'other')",
            name=op.f("ck_review_comments_label"),
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["mr_notes.id"],
            name=op.f("fk_review_comments_note_id_mr_notes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["users.id"],
            name=op.f("fk_review_comments_confirmed_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("note_id", name=op.f("pk_review_comments")),
    )

    op.create_table(
        "dpi_rubrics",
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("metrics", JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_dpi_rubrics_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_dpi_rubrics")),
    )
    op.create_table(
        "dpi_profiles",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("weights", JSONB(), nullable=False),
        sa.Column("a1_target", sa.Float(), server_default="28", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_dpi_profiles")),
    )
    op.create_table(
        "dpi_user_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("profile", sa.String(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_dpi_user_profiles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile"],
            ["dpi_profiles.key"],
            name=op.f("fk_dpi_user_profiles_profile_dpi_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["set_by"],
            ["users.id"],
            name=op.f("fk_dpi_user_profiles_set_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpi_user_profiles")),
        sa.UniqueConstraint(
            "user_id", "effective_from", name=op.f("uq_dpi_user_profiles_user_id_effective_from")
        ),
    )
    op.create_index(op.f("ix_dpi_user_profiles_user_id"), "dpi_user_profiles", ["user_id"])

    op.create_table(
        "dpi_reviews",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        sa.Column("profile", sa.String(), nullable=False),
        sa.Column("leave_days", sa.Numeric(4, 1), server_default="0", nullable=False),
        sa.Column("adjustment", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("adjustment_reason", sa.String(), nullable=True),
        sa.Column("adjusted_by", sa.Integer(), nullable=True),
        sa.Column("action_plan", sa.String(), nullable=True),
        sa.Column("one_on_one_at", sa.Date(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assessed_by", sa.Integer(), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by", sa.Integer(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.CheckConstraint(
            "status IN ('draft', 'self_review', 'lead_review', 'calibration', 'finalized', "
            "'locked')",
            name=op.f("ck_dpi_reviews_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_dpi_reviews_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["adjusted_by"],
            ["users.id"],
            name=op.f("fk_dpi_reviews_adjusted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assessed_by"],
            ["users.id"],
            name=op.f("fk_dpi_reviews_assessed_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["finalized_by"],
            ["users.id"],
            name=op.f("fk_dpi_reviews_finalized_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpi_reviews")),
        sa.UniqueConstraint("user_id", "month", name=op.f("uq_dpi_reviews_user_id_month")),
    )
    op.create_index(op.f("ix_dpi_reviews_month"), "dpi_reviews", ["month"])

    for table, extra in (
        (
            "dpi_assessments",
            [
                sa.Column("score", sa.Numeric(4, 2), nullable=False),
                sa.Column("comment", sa.String(), nullable=False),
                sa.Column("evidence", JSONB(), nullable=False),
            ],
        ),
        (
            "dpi_metric_overrides",
            [
                sa.Column("value", sa.Numeric(4, 3), nullable=False),
                sa.Column("reason", sa.String(), nullable=False),
            ],
        ),
    ):
        op.create_table(
            table,
            sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("review_id", sa.BigInteger(), nullable=False),
            sa.Column("metric", sa.String(), nullable=False),
            *extra,
            sa.Column("by", sa.Integer(), nullable=True),
            *_ts(),
            sa.ForeignKeyConstraint(
                ["review_id"],
                ["dpi_reviews.id"],
                name=op.f(f"fk_{table}_review_id_dpi_reviews"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["by"], ["users.id"], name=op.f(f"fk_{table}_by_users"), ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
            sa.UniqueConstraint("review_id", "metric", name=op.f(f"uq_{table}_review_id_metric")),
        )
        op.create_index(op.f(f"ix_{table}_review_id"), table, ["review_id"])

    op.create_table(
        "dpi_disputes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("review_id", sa.BigInteger(), nullable=False),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("raised_by", sa.Integer(), nullable=True),
        sa.Column("decision", sa.String(), nullable=True),
        sa.Column("answer", sa.String(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["dpi_reviews.id"],
            name=op.f("fk_dpi_disputes_review_id_dpi_reviews"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["raised_by"],
            ["users.id"],
            name=op.f("fk_dpi_disputes_raised_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_dpi_disputes_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpi_disputes")),
    )
    op.create_index(op.f("ix_dpi_disputes_review_id"), "dpi_disputes", ["review_id"])

    op.create_table(
        "dpi_peer_feedback",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("from_user_id", sa.Integer(), nullable=False),
        sa.Column("to_user_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(), nullable=True),
        *_ts(),
        sa.CheckConstraint("score BETWEEN 1 AND 5", name=op.f("ck_dpi_peer_feedback_score")),
        sa.ForeignKeyConstraint(
            ["from_user_id"],
            ["users.id"],
            name=op.f("fk_dpi_peer_feedback_from_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_user_id"],
            ["users.id"],
            name=op.f("fk_dpi_peer_feedback_to_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpi_peer_feedback")),
        sa.UniqueConstraint(
            "month",
            "from_user_id",
            "to_user_id",
            name=op.f("uq_dpi_peer_feedback_month_from_user_id_to_user_id"),
        ),
    )
    op.create_index(op.f("ix_dpi_peer_feedback_to_user_id"), "dpi_peer_feedback", ["to_user_id"])

    op.create_table(
        "dpi_snapshots",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("rubric_version", sa.String(), nullable=False),
        sa.Column("profile", sa.String(), nullable=False),
        sa.Column("inputs_hash", sa.String(), nullable=False),
        sa.Column("values", JSONB(), nullable=False),
        sa.Column("total", sa.Numeric(5, 2), nullable=True),
        sa.Column("rating", sa.String(), nullable=True),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_dpi_snapshots_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dpi_snapshots")),
    )
    op.create_index("ix_dpi_snapshots_user_id_month", "dpi_snapshots", ["user_id", "month"])

    bind = op.get_bind()
    rubrics = sa.table(
        "dpi_rubrics",
        sa.column("version", sa.String),
        sa.column("metrics", JSONB),
        sa.column("active", sa.Boolean),
    )
    bind.execute(insert(rubrics).values(version=RUBRIC_VERSION, metrics=RUBRIC, active=True))
    profiles = sa.table(
        "dpi_profiles",
        sa.column("key", sa.String),
        sa.column("label", sa.String),
        sa.column("weights", JSONB),
        sa.column("a1_target", sa.Float),
        sa.column("position", sa.Integer),
    )
    bind.execute(insert(profiles).values([{**p, "position": i} for i, p in enumerate(PROFILES)]))

    sets = sa.table(
        "permission_sets", sa.column("id", sa.Integer), sa.column("auto_role", sa.String)
    )
    items = sa.table(
        "permission_set_items",
        sa.column("set_id", sa.Integer),
        sa.column("permission", sa.String),
        sa.column("scope", sa.String),
    )
    for role, held in BUILTIN.items():
        set_id = bind.execute(sa.select(sets.c.id).where(sets.c.auto_role == role)).scalar()
        rows = [
            {"set_id": set_id, "permission": k, "scope": v} for k, v in held.items() if k in NEW
        ]
        if set_id is None or not rows:
            continue
        bind.execute(insert(items).values(rows).on_conflict_do_nothing())


def downgrade() -> None:
    items = sa.table("permission_set_items", sa.column("permission", sa.String))
    op.get_bind().execute(sa.delete(items).where(items.c.permission.in_(NEW)))
    op.drop_index("ix_dpi_snapshots_user_id_month", table_name="dpi_snapshots")
    op.drop_table("dpi_snapshots")
    op.drop_index(op.f("ix_dpi_peer_feedback_to_user_id"), table_name="dpi_peer_feedback")
    op.drop_table("dpi_peer_feedback")
    op.drop_index(op.f("ix_dpi_disputes_review_id"), table_name="dpi_disputes")
    op.drop_table("dpi_disputes")
    for table in ("dpi_metric_overrides", "dpi_assessments"):
        op.drop_index(op.f(f"ix_{table}_review_id"), table_name=table)
        op.drop_table(table)
    op.drop_index(op.f("ix_dpi_reviews_month"), table_name="dpi_reviews")
    op.drop_table("dpi_reviews")
    op.drop_index(op.f("ix_dpi_user_profiles_user_id"), table_name="dpi_user_profiles")
    op.drop_table("dpi_user_profiles")
    op.drop_table("dpi_profiles")
    op.drop_table("dpi_rubrics")
    op.drop_table("review_comments")
    op.drop_table("prod_defects")
    op.drop_index(op.f("ix_issue_mr_links_mr_id"), table_name="issue_mr_links")
    op.drop_table("issue_mr_links")
    op.drop_index("ix_issues_due_date", table_name="issues")
    op.drop_index("ix_issues_closed_at_src", table_name="issues")
    op.drop_table("issues")
    for column in ("tests_skipped", "tests_failed", "tests_total", "coverage", "source"):
        op.drop_column("pipelines", column)
    for column in ("tasks_done", "tasks_total", "labels"):
        op.drop_column("merge_requests", column)
