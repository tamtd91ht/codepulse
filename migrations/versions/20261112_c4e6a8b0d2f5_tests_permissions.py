"""tests.view / tests.edit / tests.run / tests.manage: the test cases get their own permissions
(docs/specs/30 §2, user 2026-11-12: "bổ sung module testcase vào bộ quyền"). Until now they used
the project documents' (docs.*); every permission set keeps what it could do: a set holding
docs.view gets tests.view, one holding docs.edit at a scope gets tests.edit, tests.run and
tests.manage at that scope (built-in sets and the ones admins made alike).

Revision ID: c4e6a8b0d2f5
Revises: a2c4e6f8b0d3
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e6a8b0d2f5"
down_revision: str | None = "a2c4e6f8b0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The built-in sets as the catalog now has them (tests/permissions/test_catalog.py checks it).
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
        "tests.run": "all",
        "tests.manage": "all",
    },
}

NEW = ("tests.view", "tests.edit", "tests.run", "tests.manage")
FROM_EDIT = ("tests.edit", "tests.run", "tests.manage")


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT set_id, permission, scope FROM permission_set_items "
            "WHERE permission IN ('docs.view', 'docs.edit')"
        )
    ).all()
    wanted: dict[tuple[int, str], str] = {}
    for set_id, permission, scope in rows:
        if permission == "docs.view":
            wanted[(set_id, "tests.view")] = "flag"
        else:
            for key in FROM_EDIT:
                wanted[(set_id, key)] = scope
    for (set_id, key), scope in wanted.items():
        bind.execute(
            sa.text(
                "INSERT INTO permission_set_items (set_id, permission, scope) "
                "VALUES (:s, :p, :sc) ON CONFLICT DO NOTHING"
            ),
            {"s": set_id, "p": key, "sc": scope},
        )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM permission_set_items WHERE permission IN :keys").bindparams(
            sa.bindparam("keys", expanding=True, value=list(NEW))
        )
    )
