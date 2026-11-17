"""Task round 3, stage 3.4 - find anything (docs/specs/31 §21.4).

- tasks.search_text: key, title, description and comments folded (lower case, Vietnamese accents
  dropped) for the full-text search; `unaccent` is an extension a managed database may not
  allow, so the text is folded in Python and matched with ILIKE. Filled here for existing tasks.
- task_filters.tree (the condition tree), shared_scope (me | team | project; an older shared
  filter becomes "project"), pinned (a Board quick filter), subscription (a daily / weekly
  digest of the matching tasks).

Revision ID: c4d5e6f7a8b9
Revises: d4f6b8c0e2a5
Create Date: 2026-11-17
"""

import unicodedata
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "d4f6b8c0e2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_TEXT = 200_000
BATCH = 500


def _fold(text: str) -> str:
    """As app/tasks/search.fold (copied: a migration does not import app code)."""
    plain = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    out = "".join(c for c in plain if unicodedata.category(c) != "Mn").lower()
    return " ".join(out.split())[:MAX_TEXT]


def upgrade() -> None:
    op.add_column("tasks", sa.Column("search_text", sa.Text(), server_default="", nullable=False))
    op.add_column(
        "task_filters",
        sa.Column("tree", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "task_filters",
        sa.Column("shared_scope", sa.String(length=10), server_default="me", nullable=False),
    )
    op.add_column(
        "task_filters",
        sa.Column("pinned", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "task_filters",
        sa.Column("subscription", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_task_filters_shared_scope"),
        "task_filters",
        "shared_scope IN ('me', 'team', 'project')",
    )
    op.execute("UPDATE task_filters SET shared_scope = 'project' WHERE shared")
    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.Integer()),
        sa.column("key", sa.String()),
        sa.column("title", sa.String()),
        sa.column("description", sa.String()),
        sa.column("search_text", sa.Text()),
    )
    comments = sa.table(
        "task_comments", sa.column("task_id", sa.Integer()), sa.column("body", sa.String())
    )
    last = 0
    while True:
        rows = bind.execute(
            sa.select(tasks.c.id, tasks.c.key, tasks.c.title, tasks.c.description)
            .where(tasks.c.id > last)
            .order_by(tasks.c.id)
            .limit(BATCH)
        ).all()
        if not rows:
            return
        ids = [r.id for r in rows]
        bodies: dict[int, list[str]] = defaultdict(list)
        for task_id, body in bind.execute(
            sa.select(comments.c.task_id, comments.c.body)
            .where(comments.c.task_id.in_(ids))
            .order_by(sa.text("id"))
        ).all():
            bodies[task_id].append(body or "")
        bind.execute(
            tasks.update()
            .where(tasks.c.id == sa.bindparam("tid"))
            .values(search_text=sa.bindparam("text")),
            [
                {
                    "tid": r.id,
                    "text": _fold(
                        " ".join([r.key or "", r.title or "", r.description or "", *bodies[r.id]])
                    ),
                }
                for r in rows
            ],
        )
        last = ids[-1]


def downgrade() -> None:
    op.drop_constraint(op.f("ck_task_filters_shared_scope"), "task_filters", type_="check")
    op.drop_column("task_filters", "subscription")
    op.drop_column("task_filters", "pinned")
    op.drop_column("task_filters", "shared_scope")
    op.drop_column("task_filters", "tree")
    op.drop_column("tasks", "search_text")
