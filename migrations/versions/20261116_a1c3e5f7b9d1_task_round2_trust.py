"""Task roadmap round 2, stage 2.0 (docs/specs/31 §20.0).

- Resolution "obsolete" (Lỗi thời): one more way to close without output.
- Scope "team" for the per-task rights and tests.run; the built-in Lead set gets them at "team"
  (the tasks of the people one leads or manages). Only new items are inserted: what an admin
  changed stays. BUILTIN is a literal copy of app/permissions/catalog.BUILTIN_SETS at this
  revision (tests compare the two).

Revision ID: a1c3e5f7b9d1
Revises: e2b4d6f8a0c3
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert

revision: str = "a1c3e5f7b9d1"
down_revision: str | None = "e2b4d6f8a0c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("done", "wont_do", "duplicate", "cannot_reproduce")
NEW = (*OLD, "obsolete")
LEAD_NEW = (
    "tasks.edit",
    "tasks.assign",
    "tasks.transition",
    "tasks.worklog",
    "tasks.link",
    "tasks.moderate",
    "tests.run",
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
}

sets = sa.table("permission_sets", sa.column("id", sa.Integer), sa.column("auto_role", sa.String))
items = sa.table(
    "permission_set_items",
    sa.column("set_id", sa.Integer),
    sa.column("permission", sa.String),
    sa.column("scope", sa.String),
)


def _check(values: tuple[str, ...]) -> str:
    return "resolution IS NULL OR resolution IN (" + ", ".join(repr(v) for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_tasks_resolution"), "tasks", type_="check")
    op.create_check_constraint(op.f("ck_tasks_resolution"), "tasks", _check(NEW))
    bind = op.get_bind()
    lead = bind.execute(sa.select(sets.c.id).where(sets.c.auto_role == "lead")).scalar()
    if lead is not None:
        rows = [
            {"set_id": lead, "permission": k, "scope": BUILTIN["lead"][k]}
            for k in LEAD_NEW
            if k in BUILTIN["lead"]
        ]
        bind.execute(insert(items).values(rows).on_conflict_do_nothing())


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE tasks SET resolution = 'wont_do' WHERE resolution = 'obsolete'"))
    op.drop_constraint(op.f("ck_tasks_resolution"), "tasks", type_="check")
    op.create_check_constraint(op.f("ck_tasks_resolution"), "tasks", _check(OLD))
    bind.execute(sa.delete(items).where(items.c.scope == "team", items.c.permission.in_(LEAD_NEW)))
