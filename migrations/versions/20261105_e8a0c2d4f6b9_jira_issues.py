"""jira_projects, issues from Jira, merge_requests.jira_keys: Jira as the DPI task source
(docs/specs/27, user 2026-11-05)

Downgrade deletes the Jira issues (they have no repo), cascading to their prod_defects; it
refuses (RuntimeError) while any such defect has an RCA (rca_status != 'pending'), so a
confirmed RCA is never lost silently. Pending ones go with their issues.

Revision ID: e8a0c2d4f6b9
Revises: d7f9b1c3e5a8
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8a0c2d4f6b9"
down_revision: str | None = "d7f9b1c3e5a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jira_projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("business_project_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("synced_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("issues_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_jira_projects_business_project_id_business_projects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jira_projects")),
        sa.UniqueConstraint("key", name=op.f("uq_jira_projects_key")),
    )
    op.create_index(
        "ix_jira_projects_business_project_id", "jira_projects", ["business_project_id"]
    )

    op.add_column(
        "issues", sa.Column("source", sa.String(), server_default="gitlab", nullable=False)
    )
    op.add_column("issues", sa.Column("jira_project_id", sa.Integer(), nullable=True))
    op.add_column("issues", sa.Column("key", sa.String(), nullable=True))
    op.add_column("issues", sa.Column("parent_key", sa.String(), nullable=True))
    op.add_column("issues", sa.Column("story_points", sa.Numeric(8, 2), nullable=True))
    op.alter_column("issues", "project_id", existing_type=sa.Integer(), nullable=True)
    op.create_foreign_key(
        op.f("fk_issues_jira_project_id_jira_projects"),
        "issues",
        "jira_projects",
        ["jira_project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_issues_jira_project_id", "issues", ["jira_project_id"])
    op.create_index(
        "uq_issues_key", "issues", ["key"], unique=True, postgresql_where=sa.text("key IS NOT NULL")
    )
    op.create_check_constraint(op.f("ck_issues_source"), "issues", "source IN ('gitlab', 'jira')")
    op.create_check_constraint(
        op.f("ck_issues_origin"),
        "issues",
        "(source = 'gitlab' AND project_id IS NOT NULL)"
        " OR (source = 'jira' AND jira_project_id IS NOT NULL AND key IS NOT NULL)",
    )

    op.add_column(
        "merge_requests",
        sa.Column("jira_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_merge_requests_jira_keys",
        "merge_requests",
        ["jira_keys"],
        postgresql_using="gin",
        postgresql_ops={"jira_keys": "jsonb_path_ops"},
    )


def downgrade() -> None:
    # Jira issues are deleted below, and their prod_defects with them (ON DELETE CASCADE): a
    # defect someone already confirmed or analysed (rca_status != 'pending') must not vanish.
    reviewed = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM prod_defects d JOIN issues i ON i.id = d.issue_id"
                " WHERE i.source = 'jira' AND d.rca_status <> 'pending'"
            )
        )
        .scalar_one()
    )
    if reviewed:
        raise RuntimeError(
            f"refusing to downgrade e8a0c2d4f6b9: {reviewed} production defect(s) of Jira issues"
            " have an RCA (rca_status != 'pending') and would be deleted with their issues;"
            " export or remove them first"
        )
    op.drop_index("ix_merge_requests_jira_keys", table_name="merge_requests")
    op.drop_column("merge_requests", "jira_keys")
    # Jira rows have no repo: they cannot stay once project_id is NOT NULL again.
    op.execute("DELETE FROM issues WHERE source = 'jira'")
    op.drop_constraint(op.f("ck_issues_origin"), "issues", type_="check")
    op.drop_constraint(op.f("ck_issues_source"), "issues", type_="check")
    op.drop_index("uq_issues_key", table_name="issues")
    op.drop_index("ix_issues_jira_project_id", table_name="issues")
    op.drop_constraint(
        op.f("fk_issues_jira_project_id_jira_projects"), "issues", type_="foreignkey"
    )
    op.alter_column("issues", "project_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("issues", "story_points")
    op.drop_column("issues", "parent_key")
    op.drop_column("issues", "key")
    op.drop_column("issues", "jira_project_id")
    op.drop_column("issues", "source")
    op.drop_index("ix_jira_projects_business_project_id", table_name="jira_projects")
    op.drop_table("jira_projects")
