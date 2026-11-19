"""`project.permissions` (docs/specs/21 §13): the Manager dự án hands out rights in their dự án
(its own permission sets, rights ticked for its people), within what they hold there. It joins
the built-in "Quản trị dự án" set at "project", which reaches only the dự án one is Manager dự án
of (`Principal.can_permit_project`); a Lead dự án holds the set too but not that role.

Revision ID: e4a6c8b0d2f9
Revises: d8f3b1a5c7e2
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e4a6c8b0d2f9"
down_revision: str | None = "d8f3b1a5c7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# A literal copy of app/permissions/catalog.BUILTIN_SETS at this revision (tests compare them).
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


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_set_items (set_id, permission, scope) "
        "SELECT id, 'project.permissions', 'project' FROM permission_sets "
        "WHERE auto_role = 'project_admin' ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM permission_set_items WHERE permission = 'project.permissions'")
