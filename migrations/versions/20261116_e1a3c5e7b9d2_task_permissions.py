"""Task rights split as Jira's permission scheme (docs/specs/31 §4.1, user 2026-11-16:
"phân quyền cho menu này, giống phân quyền jira"): tasks.create, tasks.assign,
tasks.transition, tasks.delete, tasks.comment, tasks.attach, tasks.worklog, tasks.link,
tasks.moderate, tasks.sprint join tasks.view / tasks.edit / tasks.manage.

Every permission set (built-in or made by an admin) keeps what it allowed: a set holding
tasks.view gains comment and attach; tasks.edit at a scope gains create and assign / transition /
worklog / link at that scope; tasks.manage at a scope gains delete / moderate / sprint at it.
Only new items are inserted. BUILTIN is a literal copy of app/permissions/catalog.BUILTIN_SETS
at this revision (tests compare the two).

Revision ID: e1a3c5e7b9d2
Revises: d9f1b3c5e7a2
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert

revision: str = "e1a3c5e7b9d2"
down_revision: str | None = "d9f1b3c5e7a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW = (
    "tasks.create",
    "tasks.assign",
    "tasks.transition",
    "tasks.delete",
    "tasks.comment",
    "tasks.attach",
    "tasks.worklog",
    "tasks.link",
    "tasks.moderate",
    "tasks.sprint",
)
# old item -> the new items it carried, None: at the old item's scope.
CARRIED: dict[str, dict[str, str | None]] = {
    "tasks.view": {"tasks.comment": "flag", "tasks.attach": "flag"},
    "tasks.edit": {
        "tasks.create": "flag",
        "tasks.assign": None,
        "tasks.transition": None,
        "tasks.worklog": None,
        "tasks.link": None,
    },
    "tasks.manage": {"tasks.delete": None, "tasks.moderate": None, "tasks.sprint": None},
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
        "tasks.view": "flag",
        "tasks.create": "flag",
        "tasks.edit": "own",
        "tasks.assign": "own",
        "tasks.transition": "own",
        "tasks.comment": "flag",
        "tasks.attach": "flag",
        "tasks.worklog": "own",
        "tasks.link": "own",
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
}

items = sa.table(
    "permission_set_items",
    sa.column("set_id", sa.Integer),
    sa.column("permission", sa.String),
    sa.column("scope", sa.String),
)


def upgrade() -> None:
    bind = op.get_bind()
    held = bind.execute(
        sa.select(items.c.set_id, items.c.permission, items.c.scope).where(
            items.c.permission.in_(list(CARRIED))
        )
    ).all()
    wanted: dict[tuple[int, str], str] = {}
    rank = {"own": 1, "project": 3, "all": 4, "flag": 4}
    for set_id, permission, scope in held:
        for key, fixed in CARRIED[permission].items():
            value = fixed or scope
            old = wanted.get((set_id, key))
            if old is None or rank.get(value, 0) > rank.get(old, 0):
                wanted[(set_id, key)] = value
    rows = [{"set_id": s, "permission": k, "scope": v} for (s, k), v in wanted.items()]
    if rows:
        bind.execute(insert(items).values(rows).on_conflict_do_nothing())


def downgrade() -> None:
    op.get_bind().execute(sa.delete(items).where(items.c.permission.in_(NEW)))
