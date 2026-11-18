"""Phản ánh & Đề xuất (docs/specs/32): feedback_items, feedback_exports, and the right to send
feedback (`feedback.submit`) in the built-in "Thành viên" set. `feedback.manage` is in no built-in
set: admins hold it, and an admin may put it in a set.

BUILTIN is a literal copy of app/permissions/catalog.BUILTIN_SETS at this revision (tests
compare the two).

Revision ID: a2b4c6d8e0f1
Revises: f1a2b3c4d5e6
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert

revision: str = "a2b4c6d8e0f1"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "('bug', 'feature', 'improvement', 'question')"
AREAS = (
    "('overview', 'review', 'tasks', 'testcases', 'docs', 'dpi', 'reports', 'insights', "
    "'stetho', 'admin', 'integrations', 'general')"
)

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
        "tasks.view": "flag",
        "tasks.create": "flag",
        "tasks.edit": "own",
        "tasks.assign": "own",
        "tasks.transition": "own",
        "tasks.comment": "flag",
        "tasks.attach": "flag",
        "tasks.worklog": "own",
        "tasks.link": "own",
        "feedback.submit": "flag",
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
        "tasks.edit": "team",
        "tasks.assign": "team",
        "tasks.transition": "team",
        "tasks.worklog": "team",
        "tasks.link": "team",
        "tasks.moderate": "team",
        "tests.run": "team",
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
        "tasks.view": "flag",
        "tasks.create": "flag",
        "tasks.edit": "project",
        "tasks.assign": "project",
        "tasks.transition": "project",
        "tasks.delete": "project",
        "tasks.comment": "flag",
        "tasks.attach": "flag",
        "tasks.worklog": "project",
        "tasks.link": "project",
        "tasks.moderate": "project",
        "tasks.sprint": "project",
        "tasks.manage": "project",
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
        "tasks.view": "flag",
        "tasks.create": "flag",
        "tasks.edit": "all",
        "tasks.assign": "all",
        "tasks.transition": "all",
        "tasks.delete": "all",
        "tasks.comment": "flag",
        "tasks.attach": "flag",
        "tasks.worklog": "all",
        "tasks.link": "all",
        "tasks.moderate": "all",
        "tasks.sprint": "all",
        "tasks.manage": "all",
    },
    "project_admin": {
        "project.setup": "project",
    },
    "tester": {
        "overview.view": "flag",
        "tests.view": "flag",
        "tests.edit": "all",
        "tests.run": "all",
        "tasks.view": "flag",
        "tasks.create": "flag",
        "tasks.edit": "own",
        "tasks.assign": "own",
        "tasks.transition": "own",
        "tasks.comment": "flag",
        "tasks.attach": "flag",
        "tasks.worklog": "own",
        "tasks.link": "own",
        "docs.view": "flag",
    },
}

sets = sa.table(
    "permission_sets",
    sa.column("id", sa.Integer),
    sa.column("auto_role", sa.String),
)
items = sa.table(
    "permission_set_items",
    sa.column("set_id", sa.Integer),
    sa.column("permission", sa.String),
    sa.column("scope", sa.String),
)


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "feedback_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("area", sa.String(), server_default="general", nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("page_url", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="new", nullable=False),
        sa.Column("triage_note", sa.String(), nullable=True),
        sa.Column("analysis_status", sa.String(), server_default="queued", nullable=False),
        sa.Column("ai_kind", sa.String(), nullable=True),
        sa.Column("ai_area", sa.String(), nullable=True),
        sa.Column("feature", sa.String(), nullable=True),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("ai_title", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("analysis_md", sa.String(), nullable=True),
        sa.Column("ai_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("export_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("triaged_by", sa.Integer(), nullable=True),
        sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint(f"kind IN {KINDS}", name=op.f("ck_feedback_items_kind")),
        sa.CheckConstraint(
            "status IN ('new', 'accepted', 'planned', 'done', 'rejected')",
            name=op.f("ck_feedback_items_status"),
        ),
        sa.CheckConstraint(
            "analysis_status IN ('queued', 'analyzing', 'ready', 'failed')",
            name=op.f("ck_feedback_items_analysis_status"),
        ),
        sa.CheckConstraint(f"area IN {AREAS}", name=op.f("ck_feedback_items_area")),
        sa.CheckConstraint(
            f"ai_area IS NULL OR ai_area IN {AREAS}", name=op.f("ck_feedback_items_ai_area")
        ),
        sa.CheckConstraint(
            f"ai_kind IS NULL OR ai_kind IN {KINDS}", name=op.f("ck_feedback_items_ai_kind")
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('p1', 'p2', 'p3')",
            name=op.f("ck_feedback_items_priority"),
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_feedback_items_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["triaged_by"],
            ["users.id"],
            name=op.f("fk_feedback_items_triaged_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback_items")),
    )
    op.create_index(
        "ix_feedback_items_author_id_created_at",
        "feedback_items",
        ["author_id", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_feedback_items_status"), "feedback_items", ["status"], unique=False)
    op.create_index(op.f("ix_feedback_items_ai_area"), "feedback_items", ["ai_area"], unique=False)
    op.create_table(
        "feedback_exports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("item_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_feedback_exports_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback_exports")),
    )
    op.create_index(
        op.f("ix_feedback_exports_created_at"), "feedback_exports", ["created_at"], unique=False
    )
    bind = op.get_bind()
    member = bind.scalar(sa.select(sets.c.id).where(sets.c.auto_role == "member"))
    if member is not None:
        bind.execute(
            insert(items)
            .values(set_id=member, permission="feedback.submit", scope="flag")
            .on_conflict_do_nothing()
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.delete(items).where(items.c.permission.in_(("feedback.submit", "feedback.manage")))
    )
    op.drop_index(op.f("ix_feedback_exports_created_at"), table_name="feedback_exports")
    op.drop_table("feedback_exports")
    op.drop_index(op.f("ix_feedback_items_ai_area"), table_name="feedback_items")
    op.drop_index(op.f("ix_feedback_items_status"), table_name="feedback_items")
    op.drop_index("ix_feedback_items_author_id_created_at", table_name="feedback_items")
    op.drop_table("feedback_items")
