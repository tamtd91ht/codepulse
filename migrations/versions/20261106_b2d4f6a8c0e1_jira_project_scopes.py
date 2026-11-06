"""jira_project_scopes: one Jira project counts for several CodePulse dự án (e.g. a shared Jira
project split into FE and BE), by component / label rules; issues.components and
issues.business_project_ids keep where each Jira issue goes (docs/specs/27 §15, user
2026-11-06). jira_projects.business_project_id becomes one catch-all scope.

Downgrade keeps one dự án per Jira project: its first catch-all scope, else its first scope.

Revision ID: b2d4f6a8c0e1
Revises: a3c5e7f9b1d4
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2d4f6a8c0e1"
down_revision: str | None = "a3c5e7f9b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jira_project_scopes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jira_project_id", sa.Integer(), nullable=False),
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["jira_project_id"],
            ["jira_projects.id"],
            name=op.f("fk_jira_project_scopes_jira_project_id_jira_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_jira_project_scopes_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jira_project_scopes")),
        sa.UniqueConstraint(
            "jira_project_id",
            "business_project_id",
            name=op.f("uq_jira_project_scopes_jira_project_id_business_project_id"),
        ),
    )
    op.create_index(
        "ix_jira_project_scopes_jira_project_id", "jira_project_scopes", ["jira_project_id"]
    )
    op.create_index(
        "ix_jira_project_scopes_business_project_id",
        "jira_project_scopes",
        ["business_project_id"],
    )
    op.execute(
        "INSERT INTO jira_project_scopes (jira_project_id, business_project_id)"
        " SELECT id, business_project_id FROM jira_projects"
        " WHERE business_project_id IS NOT NULL"
    )
    op.drop_index("ix_jira_projects_business_project_id", table_name="jira_projects")
    op.drop_constraint(
        "fk_jira_projects_business_project_id_business_projects",
        "jira_projects",
        type_="foreignkey",
    )
    op.drop_column("jira_projects", "business_project_id")

    op.add_column(
        "issues", sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "issues", sa.Column("business_project_ids", postgresql.ARRAY(sa.Integer()), nullable=True)
    )
    op.create_index(
        "ix_issues_business_project_ids",
        "issues",
        ["business_project_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_issues_business_project_ids", table_name="issues")
    op.drop_column("issues", "business_project_ids")
    op.drop_column("issues", "components")
    op.add_column("jira_projects", sa.Column("business_project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_jira_projects_business_project_id_business_projects",
        "jira_projects",
        "business_projects",
        ["business_project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_jira_projects_business_project_id", "jira_projects", ["business_project_id"]
    )
    op.execute(
        "UPDATE jira_projects j SET business_project_id = ("
        " SELECT s.business_project_id FROM jira_project_scopes s"
        " WHERE s.jira_project_id = j.id"
        " ORDER BY (s.components = '[]'::jsonb AND s.labels = '[]'::jsonb) DESC,"
        " s.position, s.id LIMIT 1)"
    )
    op.drop_index("ix_jira_project_scopes_business_project_id", table_name="jira_project_scopes")
    op.drop_index("ix_jira_project_scopes_jira_project_id", table_name="jira_project_scopes")
    op.drop_table("jira_project_scopes")
