"""Task (docs/specs/31, user 2026-11-16): CodePulse's own task tracker tied to git and code
review, independent of the Jira integration. Task spaces per dự án, statuses, tasks, comments,
history, links, watchers, attachments, worklogs, git links, sprints, versions, saved filters,
custom fields, AI runs; the tasks.view / tasks.edit / tasks.manage permissions in the built-in
sets (only the new items are inserted: what an admin changed in those sets stays). BUILTIN is a
literal copy of app/permissions/catalog.BUILTIN_SETS at this revision (tests compare the two).

Revision ID: c5e7a9b1d3f6
Revises: b3d5f7a9c1e4
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert

revision: str = "c5e7a9b1d3f6"
down_revision: str | None = "b3d5f7a9c1e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW = ("tasks.view", "tasks.edit", "tasks.manage")

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
        "tasks.edit": "own",
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
        "tasks.view": "flag",
        "tasks.edit": "project",
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
        "tasks.edit": "all",
        "tasks.manage": "all",
    },
    "project_admin": {
        "project.setup": "project",
    },
}

sets = sa.table("permission_sets", sa.column("id", sa.Integer), sa.column("auto_role", sa.String))
items = sa.table(
    "permission_set_items",
    sa.column("set_id", sa.Integer),
    sa.column("permission", sa.String),
    sa.column("scope", sa.String),
)


def upgrade() -> None:
    op.create_table(
        "task_spaces",
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=10), nullable=False),
        sa.Column("next_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column("types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint("key ~ '^[A-Z][A-Z0-9]{1,9}$'", name=op.f("ck_task_spaces_key")),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_task_spaces_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_task_spaces_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("business_project_id", name=op.f("pk_task_spaces")),
        sa.UniqueConstraint("key", name=op.f("uq_task_spaces_key")),
    )
    op.create_table(
        "sprints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("goal", sa.String(), nullable=True),
        sa.Column("state", sa.String(), server_default="planned", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_points", sa.Numeric(precision=8, scale=1), nullable=True),
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
            "state IN ('planned', 'active', 'closed')", name=op.f("ck_sprints_state")
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["task_spaces.business_project_id"],
            name=op.f("fk_sprints_business_project_id_task_spaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sprints")),
    )
    op.create_index(
        op.f("ix_sprints_business_project_id"), "sprints", ["business_project_id"], unique=False
    )
    op.create_index(
        "uq_sprints_active",
        "sprints",
        ["business_project_id"],
        unique=True,
        postgresql_where="state = 'active'",
    )
    op.create_table(
        "task_fields",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "options", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("required", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("archived", sa.Boolean(), server_default="false", nullable=False),
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
            "kind IN ('text', 'number', 'date', 'select', 'user')", name=op.f("ck_task_fields_kind")
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["task_spaces.business_project_id"],
            name=op.f("fk_task_fields_business_project_id_task_spaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_fields")),
    )
    op.create_index(
        op.f("ix_task_fields_business_project_id"),
        "task_fields",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "task_filters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("shared", sa.Boolean(), server_default="false", nullable=False),
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
            ["task_spaces.business_project_id"],
            name=op.f("fk_task_filters_business_project_id_task_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_filters_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_filters")),
    )
    op.create_index(op.f("ix_task_filters_user_id"), "task_filters", ["user_id"], unique=False)
    op.create_table(
        "task_statuses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("transitions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "category IN ('todo', 'doing', 'done')", name=op.f("ck_task_statuses_category")
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["task_spaces.business_project_id"],
            name=op.f("fk_task_statuses_business_project_id_task_spaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_statuses")),
        sa.UniqueConstraint(
            "business_project_id", "name", name=op.f("uq_task_statuses_business_project_id_name")
        ),
    )
    op.create_index(
        op.f("ix_task_statuses_business_project_id"),
        "task_statuses",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "task_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
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
            ["task_spaces.business_project_id"],
            name=op.f("fk_task_versions_business_project_id_task_spaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_versions")),
        sa.UniqueConstraint(
            "business_project_id", "name", name=op.f("uq_task_versions_business_project_id_name")
        ),
    )
    op.create_index(
        op.f("ix_task_versions_business_project_id"),
        "task_versions",
        ["business_project_id"],
        unique=False,
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=24), nullable=False),
        sa.Column("type", sa.String(), server_default="task", nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("status_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.String(), server_default="medium", nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("reporter_id", sa.Integer(), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("story_points", sa.Numeric(precision=6, scale=1), nullable=True),
        sa.Column("estimate_min", sa.Integer(), nullable=True),
        sa.Column("spent_min", sa.Integer(), server_default="0", nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("sprint_id", sa.Integer(), nullable=True),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("rank", sa.Float(), server_default="0", nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "custom", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
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
            "priority IN ('highest', 'high', 'medium', 'low', 'lowest')",
            name=op.f("ck_tasks_priority"),
        ),
        sa.CheckConstraint(
            "type IN ('epic', 'story', 'task', 'bug', 'subtask')", name=op.f("ck_tasks_type")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_tasks_assignee_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["task_spaces.business_project_id"],
            name=op.f("fk_tasks_business_project_id_task_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["tasks.id"], name=op.f("fk_tasks_parent_id_tasks"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reporter_id"],
            ["users.id"],
            name=op.f("fk_tasks_reporter_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sprint_id"],
            ["sprints.id"],
            name=op.f("fk_tasks_sprint_id_sprints"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["status_id"], ["task_statuses.id"], name=op.f("fk_tasks_status_id_task_statuses")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["task_versions.id"],
            name=op.f("fk_tasks_version_id_task_versions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint(
            "business_project_id", "number", name=op.f("uq_tasks_business_project_id_number")
        ),
        sa.UniqueConstraint("key", name=op.f("uq_tasks_key")),
    )
    op.create_index(op.f("ix_tasks_assignee_id"), "tasks", ["assignee_id"], unique=False)
    op.create_index(op.f("ix_tasks_due_date"), "tasks", ["due_date"], unique=False)
    op.create_index("ix_tasks_labels", "tasks", ["labels"], unique=False, postgresql_using="gin")
    op.create_index(op.f("ix_tasks_parent_id"), "tasks", ["parent_id"], unique=False)
    op.create_index("ix_tasks_space_rank", "tasks", ["business_project_id", "rank"], unique=False)
    op.create_index(
        "ix_tasks_space_status", "tasks", ["business_project_id", "status_id"], unique=False
    )
    op.create_index(op.f("ix_tasks_sprint_id"), "tasks", ["sprint_id"], unique=False)
    op.create_index(op.f("ix_tasks_status_id"), "tasks", ["status_id"], unique=False)
    op.create_index(op.f("ix_tasks_version_id"), "tasks", ["version_id"], unique=False)
    op.create_table(
        "task_ai_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="queued", nullable=False),
        sa.Column(
            "input", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Double(), nullable=True),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("kind IN ('breakdown', 'summary')", name=op.f("ck_task_ai_runs_kind")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name=op.f("ck_task_ai_runs_status")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_task_ai_runs_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_ai_runs_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_ai_runs")),
    )
    op.create_index(op.f("ix_task_ai_runs_task_id"), "task_ai_runs", ["task_id"], unique=False)
    op.create_table(
        "task_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
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
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_attachments_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name=op.f("fk_task_attachments_uploaded_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_attachments")),
    )
    op.create_index(
        op.f("ix_task_attachments_task_id"), "task_attachments", ["task_id"], unique=False
    )
    op.create_table(
        "task_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
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
            ["author_id"],
            ["users.id"],
            name=op.f("fk_task_comments_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_comments_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_comments")),
    )
    op.create_index(op.f("ix_task_comments_task_id"), "task_comments", ["task_id"], unique=False)
    op.create_table(
        "task_git_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("sha", sa.String(length=64), nullable=True),
        sa.Column("mr_iid", sa.Integer(), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("relation", sa.String(), server_default="refs", nullable=False),
        sa.Column("state", sa.String(length=20), nullable=True),
        sa.Column("manual", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("linked_by", sa.Integer(), nullable=True),
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
            (
                "(kind = 'commit' AND sha IS NOT NULL)"
                " OR (kind = 'mr' AND mr_iid IS NOT NULL)"
                " OR (kind = 'branch' AND branch IS NOT NULL)"
            ),
            name=op.f("ck_task_git_links_target"),
        ),
        sa.CheckConstraint(
            "kind IN ('branch', 'commit', 'mr')", name=op.f("ck_task_git_links_kind")
        ),
        sa.CheckConstraint(
            "relation IN ('fixes', 'refs')", name=op.f("ck_task_git_links_relation")
        ),
        sa.ForeignKeyConstraint(
            ["linked_by"],
            ["users.id"],
            name=op.f("fk_task_git_links_linked_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_task_git_links_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_git_links_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_git_links")),
    )
    op.create_index(
        "ix_task_git_links_mr", "task_git_links", ["project_id", "mr_iid"], unique=False
    )
    op.create_index("ix_task_git_links_sha", "task_git_links", ["sha"], unique=False)
    op.create_index(op.f("ix_task_git_links_task_id"), "task_git_links", ["task_id"], unique=False)
    op.create_index(
        "uq_task_git_links_branch",
        "task_git_links",
        ["task_id", "project_id", "branch"],
        unique=True,
        postgresql_where="kind = 'branch'",
    )
    op.create_index(
        "uq_task_git_links_commit",
        "task_git_links",
        ["task_id", "sha"],
        unique=True,
        postgresql_where="kind = 'commit'",
    )
    op.create_index(
        "uq_task_git_links_mr",
        "task_git_links",
        ["task_id", "project_id", "mr_iid"],
        unique=True,
        postgresql_where="kind = 'mr'",
    )
    op.create_table(
        "task_history",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("field", sa.String(length=40), nullable=False),
        sa.Column("old", sa.String(), nullable=True),
        sa.Column("new", sa.String(), nullable=True),
        sa.Column("via", sa.String(), server_default="web", nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "via IN ('web', 'git', 'smart_commit', 'mcp', 'ai')", name=op.f("ck_task_history_via")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_history_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_history_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_history")),
    )
    op.create_index(op.f("ix_task_history_task_id"), "task_history", ["task_id"], unique=False)
    op.create_table(
        "task_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_task_id", sa.Integer(), nullable=False),
        sa.Column("to_task_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
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
            "kind IN ('blocks', 'relates', 'duplicates', 'clones')", name=op.f("ck_task_links_kind")
        ),
        sa.CheckConstraint("from_task_id <> to_task_id", name=op.f("ck_task_links_not_self")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_task_links_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["from_task_id"],
            ["tasks.id"],
            name=op.f("fk_task_links_from_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_task_id"],
            ["tasks.id"],
            name=op.f("fk_task_links_to_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_links")),
        sa.UniqueConstraint(
            "from_task_id",
            "to_task_id",
            "kind",
            name=op.f("uq_task_links_from_task_id_to_task_id_kind"),
        ),
    )
    op.create_index(
        op.f("ix_task_links_from_task_id"), "task_links", ["from_task_id"], unique=False
    )
    op.create_index(op.f("ix_task_links_to_task_id"), "task_links", ["to_task_id"], unique=False)
    op.create_table(
        "task_watchers",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
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
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_watchers_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_watchers_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "user_id", name=op.f("pk_task_watchers")),
    )
    op.create_index(op.f("ix_task_watchers_user_id"), "task_watchers", ["user_id"], unique=False)
    op.create_table(
        "task_worklogs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("via", sa.String(), server_default="web", nullable=False),
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
            "via IN ('web', 'git', 'smart_commit', 'mcp', 'ai')", name=op.f("ck_task_worklogs_via")
        ),
        sa.CheckConstraint("minutes > 0", name=op.f("ck_task_worklogs_minutes")),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_worklogs_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_worklogs_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_worklogs")),
    )
    op.create_index(op.f("ix_task_worklogs_task_id"), "task_worklogs", ["task_id"], unique=False)
    op.create_index(op.f("ix_task_worklogs_user_id"), "task_worklogs", ["user_id"], unique=False)

    bind = op.get_bind()
    for role, held in BUILTIN.items():
        set_id = bind.execute(sa.select(sets.c.id).where(sets.c.auto_role == role)).scalar()
        rows = [
            {"set_id": set_id, "permission": k, "scope": v} for k, v in held.items() if k in NEW
        ]
        if set_id is None or not rows:
            continue
        bind.execute(insert(items).values(rows).on_conflict_do_nothing())


def downgrade() -> None:
    op.get_bind().execute(sa.delete(items).where(items.c.permission.in_(NEW)))
    op.drop_index(op.f("ix_task_worklogs_user_id"), table_name="task_worklogs")
    op.drop_index(op.f("ix_task_worklogs_task_id"), table_name="task_worklogs")
    op.drop_table("task_worklogs")
    op.drop_index(op.f("ix_task_watchers_user_id"), table_name="task_watchers")
    op.drop_table("task_watchers")
    op.drop_index(op.f("ix_task_links_to_task_id"), table_name="task_links")
    op.drop_index(op.f("ix_task_links_from_task_id"), table_name="task_links")
    op.drop_table("task_links")
    op.drop_index(op.f("ix_task_history_task_id"), table_name="task_history")
    op.drop_table("task_history")
    op.drop_index(
        "uq_task_git_links_mr", table_name="task_git_links", postgresql_where="kind = 'mr'"
    )
    op.drop_index(
        "uq_task_git_links_commit", table_name="task_git_links", postgresql_where="kind = 'commit'"
    )
    op.drop_index(
        "uq_task_git_links_branch", table_name="task_git_links", postgresql_where="kind = 'branch'"
    )
    op.drop_index(op.f("ix_task_git_links_task_id"), table_name="task_git_links")
    op.drop_index("ix_task_git_links_sha", table_name="task_git_links")
    op.drop_index("ix_task_git_links_mr", table_name="task_git_links")
    op.drop_table("task_git_links")
    op.drop_index(op.f("ix_task_comments_task_id"), table_name="task_comments")
    op.drop_table("task_comments")
    op.drop_index(op.f("ix_task_attachments_task_id"), table_name="task_attachments")
    op.drop_table("task_attachments")
    op.drop_index(op.f("ix_task_ai_runs_task_id"), table_name="task_ai_runs")
    op.drop_table("task_ai_runs")
    op.drop_index(op.f("ix_tasks_version_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_status_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_sprint_id"), table_name="tasks")
    op.drop_index("ix_tasks_space_status", table_name="tasks")
    op.drop_index("ix_tasks_space_rank", table_name="tasks")
    op.drop_index(op.f("ix_tasks_parent_id"), table_name="tasks")
    op.drop_index("ix_tasks_labels", table_name="tasks", postgresql_using="gin")
    op.drop_index(op.f("ix_tasks_due_date"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_assignee_id"), table_name="tasks")
    op.drop_table("tasks")
    op.drop_index(op.f("ix_task_versions_business_project_id"), table_name="task_versions")
    op.drop_table("task_versions")
    op.drop_index(op.f("ix_task_statuses_business_project_id"), table_name="task_statuses")
    op.drop_table("task_statuses")
    op.drop_index(op.f("ix_task_filters_user_id"), table_name="task_filters")
    op.drop_table("task_filters")
    op.drop_index(op.f("ix_task_fields_business_project_id"), table_name="task_fields")
    op.drop_table("task_fields")
    op.drop_index("uq_sprints_active", table_name="sprints", postgresql_where="state = 'active'")
    op.drop_index(op.f("ix_sprints_business_project_id"), table_name="sprints")
    op.drop_table("sprints")
    op.drop_table("task_spaces")
