"""mcp_calls + the report.mcp permission: logging and reporting of the MCP flow (docs/specs/26
§13, user 2026-11-05)

One row per MCP tool call (never its result, free-text arguments or the token), kept 180 days
(app/retention.py). `report.mcp` (the "Báo cáo Claude Code" page and its Excel file) goes into
the built-in sets where they hold `report.member`, at the same scope. Only the new item is
inserted: whatever an admin changed in the built-in sets since stays. BUILTIN is a literal copy
of app/permissions/catalog.BUILTIN_SETS at this revision (tests compare the two).

Revision ID: d7f9b1c3e5a8
Revises: c6e8a0b2d4f6
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert

revision: str = "d7f9b1c3e5a8"
down_revision: str | None = "c6e8a0b2d4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUILTIN = {
    "member": {
        "overview.view": "flag",
        "evaluation.view": "own",
        "report.member": "own",
        "report.mcp": "own",
        "report.export": "flag",
        "metrics.person": "own",
        "review.view": "own",
        "review.request": "own",
        "metrics.team": "own",
        "finding.close_request": "own",
        "dispute.raise": "own",
        "evidence.view": "own",
        "evidence.edit_self": "own",
        "dpi.view": "own",
        "dpi.self": "own",
        "dpi.dispute": "own",
        "knowledge.view": "flag",
        "telegram.self": "flag",
    },
    "lead": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "team",
        "report.member": "team",
        "report.mcp": "team",
        "report.export": "flag",
        "metrics.person": "team",
        "metrics.team": "team",
        "review.view": "team",
        "finding.close_decide": "team",
        "dispute.decide": "team",
        "dispute.ai_check": "team",
        "evidence.view": "team",
        "evidence.lead": "team",
        "dpi.view": "team",
        "dpi.assess": "team",
        "dpi.defects": "team",
    },
    "manager": {
        "insights.view": "flag",
        "members.view": "flag",
        "metrics.team": "team",
        "finding.close_decide": "team",
        "finding.lower_far": "team",
        "assistant.use": "flag",
        "dpi.distribution": "team",
    },
    "project_manager": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "project",
        "report.member": "project",
        "report.mcp": "project",
        "report.export": "flag",
        "review.view": "project",
        "review.request": "project",
        "review.rerun": "project",
        "review.author": "project",
        "knowledge.edit": "project",
        "criteria.assign": "project",
        "dpi.view": "project",
        "dpi.defects": "project",
    },
    "manage_all": {
        "insights.view": "flag",
        "members.view": "flag",
        "evaluation.view": "all",
        "report.member": "all",
        "report.mcp": "all",
        "report.export": "flag",
        "review.view": "all",
        "review.request": "all",
        "review.rerun": "all",
        "review.author": "all",
        "knowledge.edit": "all",
        "criteria.assign": "all",
        "dpi.view": "all",
        "dpi.distribution": "all",
        "dpi.calibrate": "all",
        "dpi.defects": "all",
    },
}
NEW = ("report.mcp",)

sets = sa.table("permission_sets", sa.column("id", sa.Integer), sa.column("auto_role", sa.String))
items = sa.table(
    "permission_set_items",
    sa.column("set_id", sa.Integer),
    sa.column("permission", sa.String),
    sa.column("scope", sa.String),
)


def upgrade() -> None:
    op.create_table(
        "mcp_calls",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("token_id", sa.BigInteger(), nullable=True),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("finding_id", sa.BigInteger(), nullable=True),
        sa.Column("closure_id", sa.BigInteger(), nullable=True),
        sa.Column("commit_sha", sa.String(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("client", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mcp_calls_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["token_id"],
            ["api_tokens.id"],
            name=op.f("fk_mcp_calls_token_id_api_tokens"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_calls")),
    )
    op.create_index("ix_mcp_calls_created_at", "mcp_calls", ["created_at"])
    op.create_index("ix_mcp_calls_user_id_created_at", "mcp_calls", ["user_id", "created_at"])
    op.create_index("ix_mcp_calls_project_id_created_at", "mcp_calls", ["project_id", "created_at"])
    bind = op.get_bind()
    for role, held in BUILTIN.items():
        set_id = bind.execute(sa.select(sets.c.id).where(sets.c.auto_role == role)).scalar()
        rows = [
            {"set_id": set_id, "permission": k, "scope": v} for k, v in held.items() if k in NEW
        ]
        if set_id is None or not rows:
            continue
        bind.execute(insert(items).values(rows).on_conflict_do_nothing())


def downgrade() -> None:
    op.get_bind().execute(sa.delete(items).where(items.c.permission.in_(NEW)))
    op.drop_index("ix_mcp_calls_project_id_created_at", table_name="mcp_calls")
    op.drop_index("ix_mcp_calls_user_id_created_at", table_name="mcp_calls")
    op.drop_index("ix_mcp_calls_created_at", table_name="mcp_calls")
    op.drop_table("mcp_calls")
