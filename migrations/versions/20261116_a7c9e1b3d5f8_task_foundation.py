"""Task roadmap P0 (docs/specs/31 §19, user 2026-11-16: "làm toàn bộ theo đề xuất đi").

- tasks.resolution (done / wont_do / duplicate / cannot_reproduce), tasks.started_at (first
  "doing" status: cycle time), tasks.status_changed_at (age in the Board column). Backfilled from
  task_history; a task already in a "done" status gets "done", or "wont_do" in a cancelled one.
- Cancelled statuses ("Won't Do", "Huỷ", "Không làm"… in the "done" group) go: their tasks move to
  the dự án's Done status with resolution wont_do, transitions forget them. A dự án whose only
  "done" status is the cancelled one keeps it.
- Sub-tasks follow their parent's sprint and version.
- team_permission_sets: a custom set given to a team's members (docs/specs/21 §11); a "Tester"
  set (custom, not built-in) to give the QA team.

Downgrade drops the columns and the team table; moved tasks stay in Done (no way back to a status
that is gone), the Tester set stays.

Revision ID: a7c9e1b3d5f8
Revises: f3b5d7e9a1c4
Create Date: 2026-11-16
"""

import json
import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1b3d5f8"
down_revision: str | None = "f3b5d7e9a1c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESOLUTIONS = ("done", "wont_do", "duplicate", "cannot_reproduce")
CANCELLED = {"won-t-do", "wont-do", "cancelled", "canceled", "huy", "khong-lam", "rejected"}
TESTER: dict[str, str] = {
    "overview.view": "flag",
    "tests.view": "flag",
    "tests.edit": "all",
    "tests.run": "all",
    "tasks.view": "flag",
    "tasks.create": "flag",
    "tasks.edit": "own",
    "tasks.assign": "own",
    "tasks.transition": "own",
    "tasks.comment": "flag",
    "tasks.attach": "flag",
    "tasks.worklog": "own",
    "tasks.link": "own",
    "docs.view": "flag",
}


def _slug(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    ascii_name = folded.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")


def upgrade() -> None:
    op.add_column("tasks", sa.Column("resolution", sa.String(length=20), nullable=True))
    op.add_column("tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "tasks", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    allowed = ", ".join(repr(r) for r in RESOLUTIONS)
    op.create_check_constraint(
        op.f("ck_tasks_resolution"), "tasks", f"resolution IS NULL OR resolution IN ({allowed})"
    )
    op.create_table(
        "team_permission_sets",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_team_permission_sets_granted_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["permission_sets.id"],
            name=op.f("fk_team_permission_sets_set_id_permission_sets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_team_permission_sets_team_id_teams"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("team_id", "set_id", name=op.f("pk_team_permission_sets")),
    )
    op.create_index(
        op.f("ix_team_permission_sets_set_id"), "team_permission_sets", ["set_id"], unique=False
    )

    migrate_data(op.get_bind())


def migrate_data(bind: sa.engine.Connection) -> None:
    """The data part of the upgrade (tests run it again on data they made)."""
    # started_at: the first move into a "doing" status (history keeps status names).
    bind.execute(
        sa.text(
            "UPDATE tasks t SET started_at = sub.at FROM ("
            " SELECT h.task_id, min(h.at) AS at FROM task_history h"
            " JOIN tasks x ON x.id = h.task_id"
            " JOIN task_statuses s ON s.business_project_id = x.business_project_id"
            "  AND s.name = h.new AND s.category = 'doing'"
            " WHERE h.field = 'status' GROUP BY h.task_id) sub"
            " WHERE sub.task_id = t.id"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE tasks t SET status_changed_at = coalesce(("
            " SELECT max(h.at) FROM task_history h"
            " WHERE h.task_id = t.id AND h.field = 'status'), t.created_at)"
        )
    )
    statuses = bind.execute(
        sa.text("SELECT id, business_project_id, name, category, transitions FROM task_statuses")
    ).all()
    cancelled = {
        sid for sid, _bp, name, cat, _tr in statuses if cat == "done" and _slug(name) in CANCELLED
    }
    done_ids = [sid for sid, _bp, _n, cat, _tr in statuses if cat == "done"]
    if done_ids:
        bind.execute(
            sa.text("UPDATE tasks SET resolution = 'done' WHERE status_id = ANY(:ids)"),
            {"ids": done_ids},
        )
    if cancelled:
        bind.execute(
            sa.text("UPDATE tasks SET resolution = 'wont_do' WHERE status_id = ANY(:ids)"),
            {"ids": list(cancelled)},
        )
    rules = {
        int(bp): (r or {})
        for bp, r in bind.execute(sa.text("SELECT business_project_id, rules FROM task_spaces"))
    }
    by_bp: dict[int, list[tuple[int, str, str, object]]] = {}
    for sid, bp, name, cat, tr in statuses:
        by_bp.setdefault(int(bp), []).append((int(sid), name, cat, tr))
    for bp, rows in by_bp.items():
        gone = [sid for sid, _n, _c, _t in rows if sid in cancelled]
        if not gone:
            continue
        kept_done = [sid for sid, _n, cat, _t in rows if cat == "done" and sid not in cancelled]
        if not kept_done:
            continue  # the dự án has no other "done" status: keep it
        wanted = rules.get(bp, {}).get("done")
        target = wanted if wanted in kept_done else kept_done[0]
        bind.execute(
            sa.text("UPDATE tasks SET status_id = :t WHERE status_id = ANY(:ids)"),
            {"t": target, "ids": gone},
        )
        # A move to a cancelled status is a move to Done now (with its resolution).
        for sid, _n, _c, tr in rows:
            if sid in gone or not isinstance(tr, list):
                continue
            left = list(dict.fromkeys(target if x in gone else x for x in tr))
            left = [x for x in left if x != sid]
            if left != tr:
                bind.execute(
                    sa.text(
                        "UPDATE task_statuses SET transitions = CAST(:tr AS jsonb) WHERE id = :id"
                    ),
                    {"tr": json.dumps(left), "id": sid},
                )
        space_rules = dict(rules.get(bp, {}))
        if any(space_rules.get(k) in gone for k in ("start", "review", "done")):
            for k in ("start", "review", "done"):
                if space_rules.get(k) in gone:
                    space_rules[k] = target
            bind.execute(
                sa.text(
                    "UPDATE task_spaces SET rules = CAST(:r AS jsonb)"
                    " WHERE business_project_id = :bp"
                ),
                {"r": json.dumps(space_rules), "bp": bp},
            )
        bind.execute(sa.text("DELETE FROM task_statuses WHERE id = ANY(:ids)"), {"ids": gone})
    # Sub-tasks follow their parent's sprint and version.
    bind.execute(
        sa.text(
            "UPDATE tasks c SET sprint_id = p.sprint_id, version_id = p.version_id"
            " FROM tasks p WHERE c.parent_id = p.id AND c.type = 'subtask'"
            " AND (c.sprint_id IS DISTINCT FROM p.sprint_id"
            "  OR c.version_id IS DISTINCT FROM p.version_id)"
        )
    )
    # The Tester set, unless a set of that name exists.
    taken = bind.execute(
        sa.text("SELECT id FROM permission_sets WHERE lower(name) = 'tester'")
    ).scalar()
    if taken is None:
        set_id = bind.execute(
            sa.text(
                "INSERT INTO permission_sets (name, description, builtin)"
                " VALUES ('Tester', :d, false) RETURNING id"
            ),
            {
                "d": "Viết và chạy testcase ở mọi dự án mình xem được, báo bug thành task. "
                "Gán cho team QA ở màn Phân quyền (mục Gán cho team)."
            },
        ).scalar()
        bind.execute(
            sa.text(
                "INSERT INTO permission_set_items (set_id, permission, scope) VALUES (:s, :p, :v)"
            ),
            [{"s": set_id, "p": k, "v": v} for k, v in TESTER.items()],
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_team_permission_sets_set_id"), table_name="team_permission_sets")
    op.drop_table("team_permission_sets")
    op.drop_constraint(op.f("ck_tasks_resolution"), "tasks", type_="check")
    op.drop_column("tasks", "status_changed_at")
    op.drop_column("tasks", "started_at")
    op.drop_column("tasks", "resolution")
