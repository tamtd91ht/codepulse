"""finding_closures: the author's argument, what the finding was closed as, proposals

A close request may carry an argument instead of (or with) the fixing commits: the author explains
why the finding does not hold where it matters (e.g. a key hardcoded only in the dev profile). The
check weighs it against the repository's current code; when it finds the argument holds, the
request becomes `proposed` and a manager or an admin confirms it (the finding then becomes
`accepted_risk`). `closed_as` is the finding's status a closing set: `fixed` or `accepted_risk`.

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6f8a0b2c3"
down_revision: str | None = "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "'pending','checking','closed','rejected','failed','cancelled','reopened'"
NEW_STATUSES = "'pending','checking','proposed','closed','rejected','failed','cancelled','reopened'"


def upgrade() -> None:
    op.add_column("finding_closures", sa.Column("argument", sa.String(), nullable=True))
    op.add_column("finding_closures", sa.Column("closed_as", sa.String(), nullable=True))
    # Every closing so far moved its finding to `fixed`.
    op.execute("UPDATE finding_closures SET closed_as = 'fixed' WHERE status = 'closed'")
    op.create_check_constraint(
        op.f("ck_finding_closures_closed_as"),
        "finding_closures",
        "closed_as IN ('fixed','accepted_risk')",
    )
    op.drop_constraint(op.f("ck_finding_closures_status"), "finding_closures", type_="check")
    op.create_check_constraint(
        op.f("ck_finding_closures_status"), "finding_closures", f"status IN ({NEW_STATUSES})"
    )
    # A proposal waiting for its confirmation is a request in flight too.
    op.drop_index("uq_finding_closures_pending", table_name="finding_closures")
    op.create_index(
        "uq_finding_closures_pending",
        "finding_closures",
        ["finding_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','checking','proposed')"),
    )


def downgrade() -> None:
    op.drop_index("uq_finding_closures_pending", table_name="finding_closures")
    op.create_index(
        "uq_finding_closures_pending",
        "finding_closures",
        ["finding_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','checking')"),
    )
    op.execute("UPDATE finding_closures SET status = 'pending' WHERE status = 'proposed'")
    op.drop_constraint(op.f("ck_finding_closures_status"), "finding_closures", type_="check")
    op.create_check_constraint(
        op.f("ck_finding_closures_status"), "finding_closures", f"status IN ({OLD_STATUSES})"
    )
    op.drop_constraint(op.f("ck_finding_closures_closed_as"), "finding_closures", type_="check")
    op.drop_column("finding_closures", "closed_as")
    op.drop_column("finding_closures", "argument")
