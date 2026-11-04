"""permission sets: report.member and report.export (docs/specs/23 §9)

The member report gets rights of its own: `report.member` (whose report one sees, scoped like
`evaluation.view`) and `report.export` (the Excel files). The built-in sets get them where they
hold `evaluation.view`, at the same scope; Manager (team aggregates only) gets neither. Only the
new items are inserted: whatever an admin changed in the built-in sets since stays. BUILTIN is a
literal copy of app/permissions/catalog.BUILTIN_SETS at this revision (tests compare the two).

Revision ID: d2e4f6a8b0c3
Revises: c1d3e5f7a9b2
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert

revision: str = "d2e4f6a8b0c3"
down_revision: str | None = "c1d3e5f7a9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
    },
    "manager": {
        "insights.view": "flag",
        "members.view": "flag",
        "metrics.team": "team",
        "finding.close_decide": "team",
        "finding.lower_far": "team",
        "assistant.use": "flag",
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
    },
}
NEW = ("report.member", "report.export")

sets = sa.table("permission_sets", sa.column("id", sa.Integer), sa.column("auto_role", sa.String))
items = sa.table(
    "permission_set_items",
    sa.column("set_id", sa.Integer),
    sa.column("permission", sa.String),
    sa.column("scope", sa.String),
)


def upgrade() -> None:
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
