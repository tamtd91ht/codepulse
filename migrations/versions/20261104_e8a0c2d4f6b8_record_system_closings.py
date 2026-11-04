"""Closing rows for findings closed without one (user report 2026-11-04)

A finding an MR's new push no longer reported (status fixed), or that took a decision made on
the same finding in another review, was closed without a finding_closures row: "Đóng finding"
then showed only its older refused request ("Bị từ chối") while the review said closed. Every
such finding whose newest row is not a closing gets one now (kind auto).

Revision ID: e8a0c2d4f6b8
Revises: d7f9b1c3e5a7
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e8a0c2d4f6b8"
down_revision: str | None = "d7f9b1c3e5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO finding_closures
            (finding_id, kind, status, closed_as, reason, decided_at, shas, created_at, updated_at)
        SELECT f.id, 'auto', 'closed', f.status,
               'Finding đã đóng sau yêu cầu trước (lần push sau không còn báo, hoặc quyết định '
               || 'ở cùng lỗi trong review khác).',
               now(), '[]'::jsonb, now(), now()
        FROM review_findings AS f
        JOIN LATERAL (
            SELECT c.status
            FROM finding_closures AS c
            WHERE c.finding_id = f.id
            ORDER BY c.id DESC
            LIMIT 1
        ) AS last ON true
        WHERE f.status IN ('fixed', 'accepted_risk', 'false_positive')
          AND last.status IN ('rejected', 'failed', 'cancelled')
          AND NOT EXISTS (
              SELECT 1 FROM finding_disputes AS d
              WHERE d.finding_id = f.id AND d.decision = 'accepted'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM finding_closures
        WHERE kind = 'auto' AND reason LIKE 'Finding đã đóng sau yêu cầu trước%'
        """
    )
