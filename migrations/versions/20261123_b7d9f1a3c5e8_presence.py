"""Presence (docs/specs/33): `presence_segments`, one row per stretch a person spent online or
away (offline is the gap between rows), and the right to see it, `presence.view`: one's own
times for everyone ("Thành viên" at "own"), the team's for its Lead and Manager ("team").

BUILTIN is a literal copy of app/permissions/catalog.BUILTIN_SETS at this revision (tests
compare the two).

Revision ID: b7d9f1a3c5e8
Revises: e4a6c8b0d2f9
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d9f1a3c5e8"
down_revision: str | None = "e4a6c8b0d2f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        "presence.view": "own",
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
        "presence.view": "team",
    },
    "manager": {
        "insights.view": "flag",
        "members.view": "flag",
        "metrics.team": "team",
        "finding.close_decide": "team",
        "finding.lower_far": "team",
        "assistant.use": "flag",
        "dpi.distribution": "team",
        "presence.view": "team",
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
        "project.criteria": "project",
        "project.permissions": "project",
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

GIVEN = (("member", "own"), ("lead", "team"), ("manager", "team"))


def upgrade() -> None:
    op.create_table(
        "presence_segments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('online', 'away')", name=op.f("ck_presence_segments_state")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_presence_segments_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_presence_segments")),
    )
    op.create_index(
        "ix_presence_segments_user_id_started_at",
        "presence_segments",
        ["user_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_presence_segments_ended_at", "presence_segments", ["ended_at"], unique=False
    )
    op.create_index(
        "uq_presence_segments_open",
        "presence_segments",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    give = sa.text(
        "INSERT INTO permission_set_items (set_id, permission, scope) "
        "SELECT id, 'presence.view', :scope FROM permission_sets "
        "WHERE auto_role = :role ON CONFLICT DO NOTHING"
    )
    for role, scope in GIVEN:
        op.get_bind().execute(give, {"role": role, "scope": scope})


def downgrade() -> None:
    op.execute("DELETE FROM permission_set_items WHERE permission = 'presence.view'")
    op.drop_index("uq_presence_segments_open", table_name="presence_segments")
    op.drop_index("ix_presence_segments_ended_at", table_name="presence_segments")
    op.drop_index("ix_presence_segments_user_id_started_at", table_name="presence_segments")
    op.drop_table("presence_segments")
