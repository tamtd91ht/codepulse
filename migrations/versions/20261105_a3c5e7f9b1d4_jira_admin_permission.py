"""jira.admin: its own permission for the Jira integration (docs/specs/27 §11, user 2026-11-05)

Until now the Jira screens needed `dpi.admin`. Every permission set holding `dpi.admin` also gets
`jira.admin`, so nobody loses access; from here on the two are granted apart. No built-in set
holds either (admins have every permission).

Revision ID: a3c5e7f9b1d4
Revises: f1b3d5e7a9c2
Create Date: 2026-11-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3c5e7f9b1d4"
down_revision: str | None = "f1b3d5e7a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_set_items (set_id, permission, scope) "
        "SELECT set_id, 'jira.admin', 'flag' FROM permission_set_items "
        "WHERE permission = 'dpi.admin' "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM permission_set_items WHERE permission = 'jira.admin'")
