"""finding_closures: the severity a risk-accepting close set, and the AI's suggestion

Closing by an argument or a note (no commit) lowers the finding to medium or low and re-scores
it; or the finding was wrong (`closed_as` = false_positive). Closing by commits keeps both.
User request 2026-11-03.

Revision ID: d6e8f0a2b4c6
Revises: c5d7e9f1a3b5
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6e8f0a2b4c6"
down_revision: str | None = "c5d7e9f1a3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("finding_closures", sa.Column("severity_before", sa.String(), nullable=True))
    op.add_column("finding_closures", sa.Column("severity_after", sa.String(), nullable=True))
    op.add_column("finding_closures", sa.Column("suggested_severity", sa.String(), nullable=True))
    op.drop_constraint(op.f("ck_finding_closures_closed_as"), "finding_closures", type_="check")
    op.create_check_constraint(
        op.f("ck_finding_closures_closed_as"),
        "finding_closures",
        "closed_as IN ('fixed','accepted_risk','false_positive')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_finding_closures_closed_as"), "finding_closures", type_="check")
    op.execute(
        "UPDATE finding_closures SET closed_as = 'accepted_risk' WHERE closed_as = 'false_positive'"
    )
    op.create_check_constraint(
        op.f("ck_finding_closures_closed_as"),
        "finding_closures",
        "closed_as IN ('fixed','accepted_risk')",
    )
    op.drop_column("finding_closures", "suggested_severity")
    op.drop_column("finding_closures", "severity_after")
    op.drop_column("finding_closures", "severity_before")
