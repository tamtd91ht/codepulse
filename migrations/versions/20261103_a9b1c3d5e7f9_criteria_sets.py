"""criteria_sets, criteria_set_versions, project_criteria_sets, review_criteria_scores,
review_findings.criteria (docs/specs/22 §3)

Revision ID: a9b1c3d5e7f9
Revises: f8a0b2c4d6e8
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9b1c3d5e7f9"
down_revision: str | None = "f8a0b2c4d6e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _stamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "criteria_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_criteria_sets_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_criteria_sets")),
        sa.UniqueConstraint("name", name=op.f("uq_criteria_sets_name")),
    )
    op.create_table(
        "criteria_set_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("apply_caps", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["criteria_sets.id"],
            name=op.f("fk_criteria_set_versions_set_id_criteria_sets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_criteria_set_versions_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_criteria_set_versions")),
        sa.UniqueConstraint(
            "set_id", "version", name=op.f("uq_criteria_set_versions_set_id_version")
        ),
    )
    op.create_index(
        op.f("ix_criteria_set_versions_set_id"), "criteria_set_versions", ["set_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_criteria_sets_current_version_id_criteria_set_versions"),
        "criteria_sets",
        "criteria_set_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "project_criteria_sets",
        sa.Column("business_project_id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_project_id"],
            ["business_projects.id"],
            name=op.f("fk_project_criteria_sets_business_project_id_business_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["criteria_sets.id"],
            name=op.f("fk_project_criteria_sets_set_id_criteria_sets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "business_project_id", "set_id", name=op.f("pk_project_criteria_sets")
        ),
    )
    op.create_index(
        op.f("ix_project_criteria_sets_set_id"), "project_criteria_sets", ["set_id"], unique=False
    )
    op.create_index(
        "uq_project_criteria_sets_primary",
        "project_criteria_sets",
        ["business_project_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_table(
        "review_criteria_scores",
        sa.Column("review_id", sa.BigInteger(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["reviews.id"],
            name=op.f("fk_review_criteria_scores_review_id_reviews"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["criteria_sets.id"],
            name=op.f("fk_review_criteria_scores_set_id_criteria_sets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["criteria_set_versions.id"],
            name=op.f("fk_review_criteria_scores_version_id_criteria_set_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("review_id", "set_id", name=op.f("pk_review_criteria_scores")),
    )
    op.create_index(
        op.f("ix_review_criteria_scores_set_id"), "review_criteria_scores", ["set_id"], unique=False
    )
    op.add_column(
        "review_findings",
        sa.Column("criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_findings", "criteria")
    op.drop_index(op.f("ix_review_criteria_scores_set_id"), table_name="review_criteria_scores")
    op.drop_table("review_criteria_scores")
    op.drop_index("uq_project_criteria_sets_primary", table_name="project_criteria_sets")
    op.drop_index(op.f("ix_project_criteria_sets_set_id"), table_name="project_criteria_sets")
    op.drop_table("project_criteria_sets")
    op.drop_constraint(
        op.f("fk_criteria_sets_current_version_id_criteria_set_versions"),
        "criteria_sets",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_criteria_set_versions_set_id"), table_name="criteria_set_versions")
    op.drop_table("criteria_set_versions")
    op.drop_table("criteria_sets")
