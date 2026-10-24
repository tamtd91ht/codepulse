"""review verdicts, design intent, triggers and per-project review rules

Requested by the user (2026-10-25, docs/specs/17_TECHLEAD_REVIEW.md): a verdict (approve /
fix_required / block) and a risk score next to the 0-100 score, the model's design & intent
notes, who/what triggered the review, and review rules per dự án / repo that go into the prompt.

Revision ID: a7c3e9b1d2f4
Revises: f4b2c6d8e1a3
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c3e9b1d2f4"
down_revision: str | None = "f4b2c6d8e1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("business_projects", sa.Column("review_rules", sa.String(), nullable=True))
    op.add_column("projects", sa.Column("review_rules", sa.String(), nullable=True))
    op.add_column("projects", sa.Column("review_rules_paths", sa.String(), nullable=True))

    op.add_column("reviews", sa.Column("verdict", sa.String(), nullable=True))
    op.add_column("reviews", sa.Column("risk_score", sa.Integer(), nullable=True))
    op.add_column(
        "reviews", sa.Column("intent", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "reviews", sa.Column("trigger", sa.String(), server_default="webhook", nullable=False)
    )
    op.add_column("reviews", sa.Column("requested_by_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_reviews_requested_by_user_id_users"),
        "reviews",
        "users",
        ["requested_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Existing finished reviews get their verdict from the findings they already have
    # (false positives excluded), the same formula as app/scoring/verdict.py.
    op.execute(
        """
        WITH counts AS (
            SELECT r.id,
                   COUNT(*) FILTER (WHERE f.severity = 'critical') AS critical,
                   COUNT(*) FILTER (WHERE f.severity = 'high') AS high,
                   COUNT(*) FILTER (WHERE f.severity = 'medium') AS medium
            FROM reviews r
            LEFT JOIN review_findings f
                   ON f.review_id = r.id AND COALESCE(f.status, 'open') <> 'false_positive'
            WHERE r.status IN ('done', 'superseded')
            GROUP BY r.id
        )
        UPDATE reviews r
           SET risk_score = c.critical * 10 + c.high * 3 + c.medium,
               verdict = CASE WHEN c.critical > 0 THEN 'block'
                              WHEN c.high > 0 THEN 'fix_required'
                              ELSE 'approve' END
          FROM counts c
         WHERE c.id = r.id
        """
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_reviews_requested_by_user_id_users"), "reviews", type_="foreignkey")
    op.drop_column("reviews", "requested_by_user_id")
    op.drop_column("reviews", "trigger")
    op.drop_column("reviews", "intent")
    op.drop_column("reviews", "risk_score")
    op.drop_column("reviews", "verdict")
    op.drop_column("projects", "review_rules_paths")
    op.drop_column("projects", "review_rules")
    op.drop_column("business_projects", "review_rules")
