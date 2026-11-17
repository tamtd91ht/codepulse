"""The "tester" auto role (docs/specs/31 §21.0, decision R2 "vai trò tester").

- users.is_tester and teams.is_qa: a person flagged tester, or a current member of a QA team,
  holds the built-in "Tester" set (auto_role `tester`), as load_principal decides.
- The custom "Tester" set made by a7c9e1b3d5f8 becomes that built-in set (what an admin changed
  in it stays); if there is none, it is created with the catalog items. The teams it was given
  to become QA teams and the people it was given to become testers; those grants go (a built-in
  set is never given by hand).

BUILTIN is a literal copy of app/permissions/catalog.BUILTIN_SETS at this revision (tests
compare the two).

Revision ID: f1a2b3c4d5e6
Revises: d5e6f7a8b9c0
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES_OLD = (
    "auto_role IS NULL OR auto_role IN "
    "('member','lead','manager','project_manager','manage_all','project_admin')"
)
ROLES_NEW = (
    "auto_role IS NULL OR auto_role IN "
    "('member','lead','manager','project_manager','manage_all','project_admin','tester')"
)
SET_NAME = "Tester"
SET_DESCRIPTION = "Người có cờ tester hoặc ở team QA: viết và chạy testcase, báo bug thành task."

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
    sa.column("name", sa.String),
    sa.column("description", sa.String),
    sa.column("builtin", sa.Boolean),
    sa.column("auto_role", sa.String),
    sa.column("personal_user_id", sa.Integer),
)
items = sa.table(
    "permission_set_items",
    sa.column("set_id", sa.Integer),
    sa.column("permission", sa.String),
    sa.column("scope", sa.String),
)
team_sets = sa.table(
    "team_permission_sets", sa.column("team_id", sa.Integer), sa.column("set_id", sa.Integer)
)
user_sets = sa.table(
    "user_permission_sets", sa.column("user_id", sa.Integer), sa.column("set_id", sa.Integer)
)
teams = sa.table("teams", sa.column("id", sa.Integer), sa.column("is_qa", sa.Boolean))
users = sa.table("users", sa.column("id", sa.Integer), sa.column("is_tester", sa.Boolean))


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("is_tester", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column(
        "teams", sa.Column("is_qa", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.drop_constraint(op.f("ck_permission_sets_auto_role"), "permission_sets", type_="check")
    op.create_check_constraint(op.f("ck_permission_sets_auto_role"), "permission_sets", ROLES_NEW)
    bind = op.get_bind()
    set_id = bind.execute(
        sa.select(sets.c.id)
        .where(
            sa.func.lower(sets.c.name) == "tester",
            sets.c.builtin.is_(False),
            sets.c.auto_role.is_(None),
            sets.c.personal_user_id.is_(None),
        )
        .order_by(sets.c.id)
        .limit(1)
    ).scalar()
    if set_id is not None:
        bind.execute(
            sa.update(sets).where(sets.c.id == set_id).values(builtin=True, auto_role="tester")
        )
        # Whoever was given the set by team or by hand now holds it by the role.
        bind.execute(
            sa.update(teams)
            .where(
                teams.c.id.in_(sa.select(team_sets.c.team_id).where(team_sets.c.set_id == set_id))
            )
            .values(is_qa=True)
        )
        bind.execute(
            sa.update(users)
            .where(
                users.c.id.in_(sa.select(user_sets.c.user_id).where(user_sets.c.set_id == set_id))
            )
            .values(is_tester=True)
        )
        bind.execute(sa.delete(team_sets).where(team_sets.c.set_id == set_id))
        bind.execute(sa.delete(user_sets).where(user_sets.c.set_id == set_id))
        return
    # None to convert: a new built-in set (a name already taken gets a suffix).
    name, n = SET_NAME, 1
    while bind.execute(sa.select(sets.c.id).where(sets.c.name == name)).scalar() is not None:
        n += 1
        name = f"{SET_NAME} ({n})"
    new_id = bind.execute(
        sa.insert(sets)
        .values(name=name, description=SET_DESCRIPTION, builtin=True, auto_role="tester")
        .returning(sets.c.id)
    ).scalar_one()
    bind.execute(
        sa.insert(items).values(
            [{"set_id": new_id, "permission": k, "scope": v} for k, v in BUILTIN["tester"].items()]
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    set_id = bind.execute(sa.select(sets.c.id).where(sets.c.auto_role == "tester")).scalar()
    if set_id is not None:
        # Back to a custom set given to the QA teams and the testers, as before this revision.
        bind.execute(
            sa.update(sets).where(sets.c.id == set_id).values(builtin=False, auto_role=None)
        )
        bind.execute(
            sa.insert(team_sets).from_select(
                ["team_id", "set_id"],
                sa.select(teams.c.id, sa.literal(set_id)).where(teams.c.is_qa.is_(True)),
            )
        )
        bind.execute(
            sa.insert(user_sets).from_select(
                ["user_id", "set_id"],
                sa.select(users.c.id, sa.literal(set_id)).where(users.c.is_tester.is_(True)),
            )
        )
    op.drop_constraint(op.f("ck_permission_sets_auto_role"), "permission_sets", type_="check")
    op.create_check_constraint(op.f("ck_permission_sets_auto_role"), "permission_sets", ROLES_OLD)
    op.drop_column("teams", "is_qa")
    op.drop_column("users", "is_tester")
