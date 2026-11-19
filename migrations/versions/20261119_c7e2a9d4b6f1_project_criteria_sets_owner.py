"""Criteria sets of one dự án (docs/specs/22 §11, docs/specs/21 §13).

- criteria_sets.business_project_id: NULL = a set for every dự án (criteria.manage); set = a
  set of that dự án only (project.criteria), gone with the dự án.
- The name is unique per owner (every dự án, or one dự án), case-insensitive as the API checks.
- A trigger keeps a dự án's sets to the shared ones and its own: no other dự án's set can be
  given to it, whatever path writes project_criteria_sets.
- Catalog: `project.criteria` joins the built-in "Quản trị dự án" set at "project".

Revision ID: c7e2a9d4b6f1
Revises: a2b4c6d8e0f1
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e2a9d4b6f1"
down_revision: str | None = "a2b4c6d8e0f1"
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


TRIGGER_FN = """
CREATE OR REPLACE FUNCTION project_criteria_sets_owner() RETURNS trigger AS $$
DECLARE owner integer;
BEGIN
    SELECT business_project_id INTO owner FROM criteria_sets WHERE id = NEW.set_id;
    IF owner IS NOT NULL AND owner <> NEW.business_project_id THEN
        RAISE EXCEPTION 'criteria set % belongs to another project', NEW.set_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column("criteria_sets", sa.Column("business_project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_criteria_sets_business_project_id_business_projects"),
        "criteria_sets",
        "business_projects",
        ["business_project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_criteria_sets_business_project_id"), "criteria_sets", ["business_project_id"]
    )
    op.drop_constraint(op.f("uq_criteria_sets_name"), "criteria_sets", type_="unique")
    # Names differing only by case were allowed before: keep them apart now.
    op.execute(
        "UPDATE criteria_sets c SET name = c.name || ' #' || c.id WHERE EXISTS ("
        "SELECT 1 FROM criteria_sets o WHERE lower(o.name) = lower(c.name) AND o.id < c.id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_criteria_sets_owner_name "
        "ON criteria_sets (COALESCE(business_project_id, 0), lower(name))"
    )
    op.execute(TRIGGER_FN)
    op.execute(
        "CREATE TRIGGER project_criteria_sets_owner BEFORE INSERT OR UPDATE "
        "ON project_criteria_sets FOR EACH ROW EXECUTE FUNCTION project_criteria_sets_owner()"
    )
    op.execute(
        "INSERT INTO permission_set_items (set_id, permission, scope) "
        "SELECT id, 'project.criteria', 'project' FROM permission_sets "
        "WHERE auto_role = 'project_admin' ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM permission_set_items WHERE permission = 'project.criteria'")
    op.execute("DROP TRIGGER IF EXISTS project_criteria_sets_owner ON project_criteria_sets")
    op.execute("DROP FUNCTION IF EXISTS project_criteria_sets_owner()")
    op.execute(
        "DELETE FROM project_criteria_sets WHERE set_id IN "
        "(SELECT id FROM criteria_sets WHERE business_project_id IS NOT NULL)"
    )
    op.execute("DELETE FROM criteria_sets WHERE business_project_id IS NOT NULL")
    op.execute("DROP INDEX IF EXISTS uq_criteria_sets_owner_name")
    op.create_unique_constraint(op.f("uq_criteria_sets_name"), "criteria_sets", ["name"])
    op.drop_index(op.f("ix_criteria_sets_business_project_id"), table_name="criteria_sets")
    op.drop_constraint(
        op.f("fk_criteria_sets_business_project_id_business_projects"),
        "criteria_sets",
        type_="foreignkey",
    )
    op.drop_column("criteria_sets", "business_project_id")
