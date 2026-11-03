"""permission sets: built-in defaults aligned with the behaviour before spec 21 (phase B)

Phase B found the first defaults reaching further than the code did (docs/specs/21 §8):
managers of a dự án never decided close requests or disputes nor lowered a critical; a rerun
always needed managing the dự án; members see their own team's aggregates; the shared .claude
repo, the project profile and the GitLab import were admin-only. The built-in sets are reset
to these items (a literal copy of app/permissions/catalog.BUILTIN_SETS at this revision).

Revision ID: f8a0b2c4d6e8
Revises: e7f9a1b3c5d7
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8a0b2c4d6e8"
down_revision: str | None = "e7f9a1b3c5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUILTIN = {
    "member": {
        "overview.view": "flag",
        "evaluation.view": "own",
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
        "review.view": "project",
        "review.request": "project",
        "review.rerun": "project",
        "review.author": "project",
        "knowledge.edit": "project",
    },
    "manage_all": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "all",
        "review.view": "all",
        "review.request": "all",
        "review.rerun": "all",
        "review.author": "all",
        "knowledge.edit": "all",
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
    bind = op.get_bind()
    for role, held in BUILTIN.items():
        set_id = bind.execute(sa.select(sets.c.id).where(sets.c.auto_role == role)).scalar()
        if set_id is None:
            continue
        bind.execute(sa.delete(items).where(items.c.set_id == set_id))
        op.bulk_insert(
            items, [{"set_id": set_id, "permission": k, "scope": v} for k, v in held.items()]
        )


def downgrade() -> None:
    # The first defaults are not restored: they gave rights no one had before spec 21.
    pass
