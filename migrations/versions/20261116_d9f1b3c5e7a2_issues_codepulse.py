"""issues.source "codepulse": CodePulse tasks as the DPI task source (docs/specs/31 §15, user
2026-11-16). app/tasks/dpi.py writes one `issues` row per task (id = 2·10^15 + task id) when
the DPI source is CodePulse; such a row has no repo and no Jira project, only its key. Keys
become unique per source (a task may reuse an old Jira project's key).

Downgrade: the CodePulse rows go (their links and defects cascade) and the source goes back to
GitLab if it was CodePulse.

Revision ID: d9f1b3c5e7a2
Revises: c5e7a9b1d3f6
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9f1b3c5e7a2"
down_revision: str | None = "c5e7a9b1d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGIN_OLD = (
    "(source = 'gitlab' AND project_id IS NOT NULL)"
    " OR (source = 'jira' AND jira_project_id IS NOT NULL AND key IS NOT NULL)"
)
ORIGIN_NEW = ORIGIN_OLD + " OR (source = 'codepulse' AND key IS NOT NULL)"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_issues_source"), "issues", type_="check")
    op.create_check_constraint(
        op.f("ck_issues_source"), "issues", "source IN ('gitlab', 'jira', 'codepulse')"
    )
    op.drop_constraint(op.f("ck_issues_origin"), "issues", type_="check")
    op.create_check_constraint(op.f("ck_issues_origin"), "issues", ORIGIN_NEW)
    # A key is unique per source: a task CRM-1 may sit beside a Jira issue CRM-1 of old.
    op.drop_index("uq_issues_key", table_name="issues")
    op.create_index(
        "uq_issues_key",
        "issues",
        ["source", "key"],
        unique=True,
        postgresql_where=sa.text("key IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM issues WHERE source = 'codepulse'"))
    bind.execute(
        sa.text(
            "UPDATE app_settings SET value = to_jsonb('gitlab'::text) "
            "WHERE key = 'dpi_task_source' AND value = to_jsonb('codepulse'::text)"
        )
    )
    op.drop_index("uq_issues_key", table_name="issues")
    op.create_index(
        "uq_issues_key", "issues", ["key"], unique=True, postgresql_where=sa.text("key IS NOT NULL")
    )
    op.drop_constraint(op.f("ck_issues_origin"), "issues", type_="check")
    op.create_check_constraint(op.f("ck_issues_origin"), "issues", ORIGIN_OLD)
    op.drop_constraint(op.f("ck_issues_source"), "issues", type_="check")
    op.create_check_constraint(op.f("ck_issues_source"), "issues", "source IN ('gitlab', 'jira')")
