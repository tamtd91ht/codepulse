"""finding_closures: `unmet`, a check that found the request not (yet) good enough

Until now a check that did not close the finding ended the request (`rejected`): it showed in the
history only, with no button, and the lead/manager who pressed "Chạy review" never got to weigh
the model's reason. Now it waits for them (`unmet`, a request in flight, in "Chờ duyệt"): they
close the finding anyway (note required), run the check again, or confirm the refusal.

Requests the check refused in the last 30 days whose finding is still open (nothing else in flight)
become `unmet`, so they show up for a decision.

Revision ID: b4d6f8a0c2e4
Revises: a1c3e5b7d9f1
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4d6f8a0c2e4"
down_revision: str | None = "a1c3e5b7d9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "'pending','checking','proposed','closed','rejected','failed','cancelled','reopened'"
NEW_STATUSES = (
    "'pending','checking','proposed','unmet','closed','rejected','failed','cancelled','reopened'"
)
OLD_OPEN = "status IN ('pending','checking','proposed')"
NEW_OPEN = "status IN ('pending','checking','proposed','unmet')"


def _status_check(statuses: str) -> None:
    op.drop_constraint(op.f("ck_finding_closures_status"), "finding_closures", type_="check")
    op.create_check_constraint(
        op.f("ck_finding_closures_status"), "finding_closures", f"status IN ({statuses})"
    )


def _open_index(where: str) -> None:
    op.drop_index("uq_finding_closures_pending", table_name="finding_closures")
    op.create_index(
        "uq_finding_closures_pending",
        "finding_closures",
        ["finding_id"],
        unique=True,
        postgresql_where=sa.text(where),
    )


def upgrade() -> None:
    _status_check(NEW_STATUSES)
    _open_index(NEW_OPEN)
    # The newest request per finding the check refused (not a person's refusal, not an admin's
    # close, not "the finding moved meanwhile"), the finding still open, nothing else in flight.
    op.execute(
        """
        UPDATE finding_closures c SET status = 'unmet'
        FROM (
            SELECT DISTINCT ON (fc.finding_id) fc.id
            FROM finding_closures fc
            JOIN review_findings f ON f.id = fc.finding_id AND f.status = 'open'
            WHERE fc.kind = 'request' AND fc.status = 'rejected'
              AND fc.decided_by IS NOT NULL
              AND fc.decided_at > now() - interval '30 days'
              AND NOT starts_with(coalesce(fc.reason, ''), 'Từ chối:')
              AND NOT starts_with(coalesce(fc.reason, ''), 'Admin đã đóng')
              AND NOT starts_with(coalesce(fc.reason, ''), 'Finding ')
              AND NOT EXISTS (
                  SELECT 1 FROM finding_closures o
                  WHERE o.finding_id = fc.finding_id
                    AND o.status IN ('pending','checking','proposed')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM finding_closures n
                  WHERE n.finding_id = fc.finding_id AND n.id > fc.id
              )
            ORDER BY fc.finding_id, fc.id DESC
        ) pick
        WHERE c.id = pick.id
        """
    )


def downgrade() -> None:
    op.execute("UPDATE finding_closures SET status = 'rejected' WHERE status = 'unmet'")
    _open_index(OLD_OPEN)
    _status_check(OLD_STATUSES)
