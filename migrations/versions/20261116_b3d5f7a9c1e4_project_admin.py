"""project_grants.admin_role + the "Quản trị dự án" built-in set (docs/specs/21 §10, user
2026-11-16): an admin creates a dự án with no repo yet, gives it to someone as its Lead or
Manager (a "manage" grant with `admin_role`), and that person adds the repos and sets up the
dự án's .claude repo, rule files and review rules themselves.

`project.setup` (scope project/all) is the new permission; the new built-in set (auto_role
`project_admin`) holds it at "project", i.e. on the dự án one is Lead / Manager of. BUILTIN is a
literal copy of app/permissions/catalog.BUILTIN_SETS at this revision (tests compare the two).

Revision ID: b3d5f7a9c1e4
Revises: 753d95ac9404
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d5f7a9c1e4"
down_revision: str | None = "753d95ac9404"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES_OLD = (
    "auto_role IS NULL OR auto_role IN ('member','lead','manager','project_manager','manage_all')"
)
ROLES_NEW = (
    "auto_role IS NULL OR auto_role IN "
    "('member','lead','manager','project_manager','manage_all','project_admin')"
)
SET_NAME = "Quản trị dự án"
SET_DESCRIPTION = (
    "Lead / Manager của một dự án: thêm repo, cấu hình repo, repo .claude, file quy tắc và "
    "quy tắc review của dự án đó."
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
    },
    "project_admin": {
        "project.setup": "project",
    },
}

sets = sa.table(
    "permission_sets",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("description", sa.String),
    sa.column("builtin", sa.Boolean),
    sa.column("auto_role", sa.String),
)
items = sa.table(
    "permission_set_items",
    sa.column("set_id", sa.Integer),
    sa.column("permission", sa.String),
    sa.column("scope", sa.String),
)


def upgrade() -> None:
    op.add_column("project_grants", sa.Column("admin_role", sa.String(), nullable=True))
    op.create_check_constraint(
        op.f("ck_project_grants_admin_role"),
        "project_grants",
        "admin_role IS NULL OR (admin_role IN ('lead','manager') AND role = 'manage')",
    )
    op.drop_constraint(op.f("ck_permission_sets_auto_role"), "permission_sets", type_="check")
    op.create_check_constraint(op.f("ck_permission_sets_auto_role"), "permission_sets", ROLES_NEW)
    bind = op.get_bind()
    # A set an admin made may already carry the name: the built-in one then takes a suffix.
    name, n = SET_NAME, 1
    while bind.execute(sa.select(sets.c.id).where(sets.c.name == name)).scalar() is not None:
        n += 1
        name = f"{SET_NAME} ({n})"
    set_id = bind.execute(
        sa.insert(sets)
        .values(name=name, description=SET_DESCRIPTION, builtin=True, auto_role="project_admin")
        .returning(sets.c.id)
    ).scalar_one()
    bind.execute(
        sa.insert(items).values(
            [
                {"set_id": set_id, "permission": k, "scope": v}
                for k, v in BUILTIN["project_admin"].items()
            ]
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.delete(sets).where(sets.c.auto_role == "project_admin"))
    bind.execute(sa.delete(items).where(items.c.permission == "project.setup"))
    op.drop_constraint(op.f("ck_permission_sets_auto_role"), "permission_sets", type_="check")
    op.create_check_constraint(op.f("ck_permission_sets_auto_role"), "permission_sets", ROLES_OLD)
    op.drop_constraint(op.f("ck_project_grants_admin_role"), "project_grants", type_="check")
    op.drop_column("project_grants", "admin_role")
