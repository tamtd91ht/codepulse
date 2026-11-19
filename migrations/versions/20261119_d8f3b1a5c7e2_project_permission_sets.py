"""Permission sets of one dự án (docs/specs/21 §13).

- permission_sets.business_project_id: NULL = a set that holds across the system, as before;
  set = a set of that dự án only, made by its Manager dự án and given to people of it. Its
  items count over that dự án only, while the person still has a grant on it; gone with it.
- Names are unique per owner (the system, or one dự án), case-insensitive as the API checks.
- A person's own set (personal_user_id, §9) becomes one per owner: their own system-wide set,
  and one per dự án where a Manager dự án ticked rights for them.

Revision ID: d8f3b1a5c7e2
Revises: c7e2a9d4b6f1
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f3b1a5c7e2"
down_revision: str | None = "c7e2a9d4b6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("permission_sets", sa.Column("business_project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_permission_sets_business_project_id_business_projects"),
        "permission_sets",
        "business_projects",
        ["business_project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_permission_sets_business_project_id"), "permission_sets", ["business_project_id"]
    )
    op.drop_constraint(op.f("uq_permission_sets_name"), "permission_sets", type_="unique")
    # Names differing only by case were allowed before: keep them apart now.
    op.execute(
        "UPDATE permission_sets s SET name = s.name || ' #' || s.id WHERE EXISTS ("
        "SELECT 1 FROM permission_sets o WHERE lower(o.name) = lower(s.name) AND o.id < s.id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_permission_sets_owner_name "
        "ON permission_sets (COALESCE(business_project_id, 0), lower(name))"
    )
    op.drop_constraint(
        op.f("uq_permission_sets_personal_user_id"), "permission_sets", type_="unique"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_permission_sets_personal_owner "
        "ON permission_sets (personal_user_id, COALESCE(business_project_id, 0)) "
        "WHERE personal_user_id IS NOT NULL"
    )
    # A dự án's set is never a built-in one (those follow a role across the system).
    op.create_check_constraint(
        op.f("ck_permission_sets_project_not_builtin"),
        "permission_sets",
        "business_project_id IS NULL OR (NOT builtin AND auto_role IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_permission_sets_project_not_builtin"), "permission_sets", type_="check"
    )
    op.execute("DELETE FROM permission_sets WHERE business_project_id IS NOT NULL")
    op.execute("DROP INDEX IF EXISTS uq_permission_sets_personal_owner")
    op.create_unique_constraint(
        op.f("uq_permission_sets_personal_user_id"), "permission_sets", ["personal_user_id"]
    )
    op.execute("DROP INDEX IF EXISTS uq_permission_sets_owner_name")
    op.create_unique_constraint(op.f("uq_permission_sets_name"), "permission_sets", ["name"])
    op.drop_index(op.f("ix_permission_sets_business_project_id"), table_name="permission_sets")
    op.drop_constraint(
        op.f("fk_permission_sets_business_project_id_business_projects"),
        "permission_sets",
        type_="foreignkey",
    )
    op.drop_column("permission_sets", "business_project_id")
