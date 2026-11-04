"""Close requests still waiting on findings already decided (user report 2026-11-04)

A finding judged not an error (accepted dispute), accepted as a risk or fixed while a close
request of it was waiting showed both "không phải lỗi" and "chưa đủ điều kiện đóng". Those
waiting requests are closed with the finding's decision; `checking` (a worker's) is left alone.

Revision ID: d7f9b1c3e5a7
Revises: b4d6f8a0c2e4
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d7f9b1c3e5a7"
down_revision: str | None = "b4d6f8a0c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE finding_closures AS c
        SET status = 'closed',
            closed_as = f.status,
            decided_at = COALESCE(c.decided_at, now()),
            reason = 'Finding đã được quyết định trước đó (' ||
                CASE f.status
                    WHEN 'false_positive' THEN 'không phải lỗi'
                    WHEN 'accepted_risk' THEN 'chấp nhận rủi ro'
                    ELSE 'đã sửa'
                END || '): yêu cầu đóng không còn cần.'
        FROM review_findings AS f
        WHERE f.id = c.finding_id
          AND c.status IN ('pending', 'proposed', 'unmet', 'failed')
          AND f.status IN ('false_positive', 'accepted_risk', 'fixed')
        """
    )


def downgrade() -> None:
    pass  # the requests were moot; nothing to restore
