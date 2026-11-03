"""evidence_packs.ai_draft_started_at; merge_requests.merge_commit_sha / squash_commit_sha

- ai_draft_started_at: an AI draft is being written (docs/specs/20 §6.4): one at a time per pack.
- merge/squash commit sha of an MR: those commits went through review, so the pack's
  "commits not in any MR" leaves them out (§6.2, 9.10b).

Revision ID: f2a4b6c8d0e2
Revises: e1f3a5b7c9d1
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a4b6c8d0e2"
down_revision: str | None = "e1f3a5b7c9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence_packs",
        sa.Column("ai_draft_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("merge_requests", sa.Column("merge_commit_sha", sa.String(), nullable=True))
    op.add_column("merge_requests", sa.Column("squash_commit_sha", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_merge_requests_merge_commit_sha"), "merge_requests", ["merge_commit_sha"]
    )
    op.create_index(
        op.f("ix_merge_requests_squash_commit_sha"), "merge_requests", ["squash_commit_sha"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_merge_requests_squash_commit_sha"), table_name="merge_requests")
    op.drop_index(op.f("ix_merge_requests_merge_commit_sha"), table_name="merge_requests")
    op.drop_column("merge_requests", "squash_commit_sha")
    op.drop_column("merge_requests", "merge_commit_sha")
    op.drop_column("evidence_packs", "ai_draft_started_at")
