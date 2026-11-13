"""Reviews queued or running have no end time: a failed / skipped / stale review run again kept
the end time of its earlier run, so the list showed "Đang chạy" beside a "Review xong" time (user
2026-11-13). The code no longer keeps it (app/review/service.py claim, reruns, requeues); this
clears the rows already in that state. Their finished run's time is not recoverable, and none is
needed: it is set again when the run ends.

Downgrade: nothing to undo.

Revision ID: 753d95ac9404
Revises: f0e34169d75e
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "753d95ac9404"
down_revision: str | None = "f0e34169d75e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE reviews SET finished_at = NULL "
        "WHERE status IN ('queued', 'running') AND finished_at IS NOT NULL"
    )


def downgrade() -> None:
    pass
