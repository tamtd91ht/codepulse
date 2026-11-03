"""permission_sets, permission_set_items, user_permission_sets (docs/specs/21, phase A)

The built-in sets are seeded with what each role could do before spec 21 (a literal copy of
app/permissions/catalog.BUILTIN_SETS at this revision), so nothing changes for anyone.

Revision ID: e7f9a1b3c5d7
Revises: d6e8f0a2b4c6
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f9a1b3c5d7"
down_revision: str | None = "d6e8f0a2b4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUILTIN = [
    {
        "auto_role": "member",
        "name": "Thành viên",
        "description": "Mọi người đang hoạt động: việc của chính mình.",
        "items": {
            "overview.view": "flag",
            "evaluation.view": "own",
            "metrics.person": "own",
            "review.view": "own",
            "review.request": "own",
            "review.rerun": "own",
            "finding.close_request": "own",
            "dispute.raise": "own",
            "evidence.view": "own",
            "evidence.edit_self": "own",
            "knowledge.view": "flag",
            "telegram.self": "flag",
        },
    },
    {
        "auto_role": "lead",
        "name": "Lead",
        "description": "Lead của một team: thấy và duyệt việc của người trong team mình.",
        "items": {
            "insights.view": "flag",
            "members.view": "flag",
            "evaluation.view": "team",
            "metrics.person": "team",
            "metrics.team": "team",
            "review.view": "team",
            "review.rerun": "team",
            "finding.close_decide": "team",
            "dispute.decide": "team",
            "dispute.ai_check": "team",
            "evidence.view": "team",
            "evidence.lead": "team",
        },
    },
    {
        "auto_role": "manager",
        "name": "Manager",
        "description": "Manager của team (và team con): chỉ số tổng hợp của team, "
        "duyệt yêu cầu đóng "
        "finding.",
        "items": {
            "insights.view": "flag",
            "members.view": "flag",
            "metrics.team": "team",
            "finding.close_decide": "team",
            "finding.lower_far": "team",
            "assistant.use": "flag",
        },
    },
    {
        "auto_role": "project_manager",
        "name": "Quản lý dự án",
        "description": "Có quyền manage ở dự án: thấy và xử lý việc của mọi người trong dự án đó.",
        "items": {
            "insights.view": "flag",
            "members.view": "flag",
            "evaluation.view": "project",
            "review.view": "project",
            "review.request": "project",
            "review.rerun": "project",
            "review.author": "project",
            "finding.close_decide": "project",
            "finding.lower_far": "project",
            "dispute.decide": "project",
            "dispute.ai_check": "project",
            "knowledge.edit": "project",
            "gitlab_members.import": "project",
        },
    },
    {
        "auto_role": "manage_all",
        "name": "Quản lý tổng",
        "description": "Quản lý mọi dự án (không phải admin: không vào màn Quản trị).",
        "items": {
            "insights.view": "flag",
            "members.view": "flag",
            "evaluation.view": "all",
            "review.view": "all",
            "review.request": "all",
            "review.rerun": "all",
            "review.author": "all",
            "finding.close_decide": "all",
            "finding.lower_far": "all",
            "dispute.decide": "all",
            "dispute.ai_check": "all",
            "knowledge.edit": "all",
            "gitlab_members.import": "all",
        },
    },
]


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
    sets = op.create_table(
        "permission_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_role", sa.String(), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "auto_role IS NULL OR auto_role IN "
            "('member','lead','manager','project_manager','manage_all')",
            name=op.f("ck_permission_sets_auto_role"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission_sets")),
        sa.UniqueConstraint("name", name=op.f("uq_permission_sets_name")),
        sa.UniqueConstraint("auto_role", name=op.f("uq_permission_sets_auto_role")),
    )
    items = op.create_table(
        "permission_set_items",
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("permission", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["permission_sets.id"],
            name=op.f("fk_permission_set_items_set_id_permission_sets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("set_id", "permission", name=op.f("pk_permission_set_items")),
    )
    op.create_table(
        "user_permission_sets",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_permission_sets_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["permission_sets.id"],
            name=op.f("fk_user_permission_sets_set_id_permission_sets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_user_permission_sets_granted_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("user_id", "set_id", name=op.f("pk_user_permission_sets")),
    )
    op.create_index(
        op.f("ix_user_permission_sets_set_id"), "user_permission_sets", ["set_id"], unique=False
    )
    bind = op.get_bind()
    for entry in BUILTIN:
        set_id = bind.execute(
            sa.insert(sets)
            .values(
                name=entry["name"],
                description=entry["description"],
                builtin=True,
                auto_role=entry["auto_role"],
            )
            .returning(sets.c.id)
        ).scalar_one()
        op.bulk_insert(
            items,
            [{"set_id": set_id, "permission": k, "scope": v} for k, v in entry["items"].items()],
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_permission_sets_set_id"), table_name="user_permission_sets")
    op.drop_table("user_permission_sets")
    op.drop_table("permission_set_items")
    op.drop_table("permission_sets")
