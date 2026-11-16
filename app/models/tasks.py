"""CodePulse's own task tracker (docs/specs/31): task spaces per dự án, tasks with a workflow,
sprints, versions, and their links to branches, commits and MRs. Independent of the Jira
integration (docs/specs/27): nothing here reads or writes `issues`."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

TASK_TYPES = ("epic", "story", "task", "bug", "subtask")
# The five priority levels: the arrow a priority shows and what it means elsewhere (DPI's P1/P2
# for production bugs). Each dự án names its own priorities on top (TaskPriority, §4 of 31).
PRIORITIES = ("highest", "high", "medium", "low", "lowest")
PRIORITY_COLORS = ("red", "orange", "amber", "green", "blue", "purple", "gray")
CATEGORIES = ("todo", "doing", "done")
# Why a task left work (docs/specs/31 §19.1): set on entering a "done" status, cleared on leaving.
# Only "done" counts as delivered (DPI, cycle time); the others close it without output.
RESOLUTIONS = ("done", "wont_do", "duplicate", "cannot_reproduce", "obsolete")
LINK_KINDS = ("blocks", "relates", "duplicates", "clones")
GIT_KINDS = ("branch", "commit", "mr")
RELATIONS = ("fixes", "refs")
VIA = ("web", "git", "smart_commit", "mcp", "ai")
FIELD_KINDS = ("text", "number", "date", "select", "user")
BOARD_MODES = ("scrum", "kanban")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class TaskSpace(TimestampMixin, Base):
    __tablename__ = "task_spaces"
    # A regex check: PostgreSQL only (some unit tests build the tables on SQLite).
    __table_args__ = (
        CheckConstraint("key ~ '^[A-Z][A-Z0-9]{1,9}$'", name="key").ddl_if(dialect="postgresql"),
        CheckConstraint(_in("board_mode", BOARD_MODES), name="board_mode"),
    )

    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(10), unique=True)
    next_number: Mapped[int] = mapped_column(server_default="1", default=1)
    types: Mapped[list[str]] = mapped_column(JSONB)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Scrum: the Board shows the active sprint; Kanban: every open task, no sprint (§19.6).
    board_mode: Mapped[str] = mapped_column(String(10), server_default="scrum", default="scrum")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TaskStatus(TimestampMixin, Base):
    __tablename__ = "task_statuses"
    __table_args__ = (
        UniqueConstraint("business_project_id", "name"),
        CheckConstraint(_in("category", CATEGORIES), name="category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("task_spaces.business_project_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    category: Mapped[str]
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    # Statuses a task here may move to; NULL: any.
    transitions: Mapped[list[int] | None] = mapped_column(JSONB)
    # Board hints (docs/specs/31 §19.3): a soft cap on tasks in the column, and the age (hours
    # in this status) after which a card is flagged. NULL: none.
    wip_limit: Mapped[int | None]
    age_warning_hours: Mapped[int | None]


class TaskPriority(TimestampMixin, Base):
    """A priority of a dự án's tasks (docs/specs/31 §18), as Jira's priority scheme: its own
    name, colour and order; `level` gives the arrow and the meaning. Tasks hold its `key`."""

    __tablename__ = "task_priorities"
    __table_args__ = (
        UniqueConstraint("business_project_id", "key"),
        CheckConstraint(_in("level", PRIORITIES), name="level"),
        CheckConstraint(_in("color", PRIORITY_COLORS), name="color"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("task_spaces.business_project_id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(10))
    color: Mapped[str] = mapped_column(String(12))
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    is_default: Mapped[bool] = mapped_column(server_default="false", default=False)


class Sprint(TimestampMixin, Base):
    __tablename__ = "sprints"
    __table_args__ = (
        CheckConstraint("state IN ('planned', 'active', 'closed')", name="state"),
        Index(
            "uq_sprints_active",
            "business_project_id",
            unique=True,
            postgresql_where="state = 'active'",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("task_spaces.business_project_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    goal: Mapped[str | None]
    state: Mapped[str] = mapped_column(server_default="planned", default="planned")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Story points in the sprint when it started (the burndown's top).
    committed_points: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))


class TaskVersion(TimestampMixin, Base):
    __tablename__ = "task_versions"
    __table_args__ = (UniqueConstraint("business_project_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("task_spaces.business_project_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None]
    release_date: Mapped[date | None] = mapped_column(Date)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("business_project_id", "number"),
        CheckConstraint(_in("type", TASK_TYPES), name="type"),
        CheckConstraint(
            "resolution IS NULL OR " + _in("resolution", RESOLUTIONS), name="resolution"
        ),
        Index("ix_tasks_space_status", "business_project_id", "status_id"),
        Index("ix_tasks_space_rank", "business_project_id", "rank"),
        Index("ix_tasks_labels", "labels", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("task_spaces.business_project_id", ondelete="CASCADE")
    )
    number: Mapped[int]
    key: Mapped[str] = mapped_column(String(24), unique=True)
    type: Mapped[str] = mapped_column(server_default="task", default="task")
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None]
    status_id: Mapped[int] = mapped_column(ForeignKey("task_statuses.id"), index=True)
    priority: Mapped[str] = mapped_column(String(30), server_default="medium", default="medium")
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    labels: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    components: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    story_points: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    estimate_min: Mapped[int | None]
    spent_min: Mapped[int] = mapped_column(server_default="0", default=0)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    sprint_id: Mapped[int | None] = mapped_column(
        ForeignKey("sprints.id", ondelete="SET NULL"), index=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_versions.id", ondelete="SET NULL"), index=True
    )
    rank: Mapped[float] = mapped_column(Float, server_default="0", default=0.0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(20))
    # First time the task entered a "doing" status: cycle time runs from here (§19.1).
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When it entered its current status: the age in its Board column (§19.3).
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    custom: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)


class TaskComment(TimestampMixin, Base):
    __tablename__ = "task_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str]
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskHistory(Base):
    __tablename__ = "task_history"
    __table_args__ = (CheckConstraint(_in("via", VIA), name="via"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    field: Mapped[str] = mapped_column(String(40))
    old: Mapped[str | None]
    new: Mapped[str | None]
    via: Mapped[str] = mapped_column(server_default="web", default="web")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskLink(TimestampMixin, Base):
    __tablename__ = "task_links"
    __table_args__ = (
        UniqueConstraint("from_task_id", "to_task_id", "kind"),
        CheckConstraint(_in("kind", LINK_KINDS), name="kind"),
        CheckConstraint("from_task_id <> to_task_id", name="not_self"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    to_task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TaskWatcher(TimestampMixin, Base):
    __tablename__ = "task_watchers"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class TaskAttachment(TimestampMixin, Base):
    __tablename__ = "task_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(120))
    size: Mapped[int]
    path: Mapped[str] = mapped_column(String(255))  # relative to TASK_FILES_DIR
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TaskWorklog(TimestampMixin, Base):
    __tablename__ = "task_worklogs"
    __table_args__ = (
        CheckConstraint("minutes > 0", name="minutes"),
        CheckConstraint(_in("via", VIA), name="via"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    minutes: Mapped[int]
    day: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None]
    via: Mapped[str] = mapped_column(server_default="web", default="web")


class TaskGitLink(TimestampMixin, Base):
    """A branch, commit or MR of a repo, tied to a task by its key (docs/specs/31 §5) or by
    hand. `state` follows an MR (opened/merged/closed): the git rules run when it changes."""

    __tablename__ = "task_git_links"
    __table_args__ = (
        CheckConstraint(_in("kind", GIT_KINDS), name="kind"),
        CheckConstraint(_in("relation", RELATIONS), name="relation"),
        CheckConstraint(
            "(kind = 'commit' AND sha IS NOT NULL)"
            " OR (kind = 'mr' AND mr_iid IS NOT NULL)"
            " OR (kind = 'branch' AND branch IS NOT NULL)",
            name="target",
        ),
        Index(
            "uq_task_git_links_commit",
            "task_id",
            "sha",
            unique=True,
            postgresql_where="kind = 'commit'",
        ),
        Index(
            "uq_task_git_links_mr",
            "task_id",
            "project_id",
            "mr_iid",
            unique=True,
            postgresql_where="kind = 'mr'",
        ),
        Index(
            "uq_task_git_links_branch",
            "task_id",
            "project_id",
            "branch",
            unique=True,
            postgresql_where="kind = 'branch'",
        ),
        Index("ix_task_git_links_sha", "sha"),
        Index("ix_task_git_links_mr", "project_id", "mr_iid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str]
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    sha: Mapped[str | None] = mapped_column(String(64))
    mr_iid: Mapped[int | None]
    branch: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None]
    relation: Mapped[str] = mapped_column(server_default="refs", default="refs")
    state: Mapped[str | None] = mapped_column(String(20))
    manual: Mapped[bool] = mapped_column(server_default="false", default=False)
    linked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TaskFilter(TimestampMixin, Base):
    __tablename__ = "task_filters"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    business_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_spaces.business_project_id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120))
    query: Mapped[dict[str, Any]] = mapped_column(JSONB)
    shared: Mapped[bool] = mapped_column(server_default="false", default=False)


class TaskField(TimestampMixin, Base):
    __tablename__ = "task_fields"
    __table_args__ = (CheckConstraint(_in("kind", FIELD_KINDS), name="kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("task_spaces.business_project_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str]
    options: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    required: Mapped[bool] = mapped_column(server_default="false", default=False)
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    archived: Mapped[bool] = mapped_column(server_default="false", default=False)


class TaskAiRun(TimestampMixin, Base):
    __tablename__ = "task_ai_runs"
    __table_args__ = (
        CheckConstraint("kind IN ('breakdown', 'summary')", name="kind"),
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="queued", default="queued")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None]
    model: Mapped[str | None]
    cost_usd: Mapped[float | None]
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskTestLink(TimestampMixin, Base):
    """A task and a testcase (docs/specs/31 §19.2, §19.4): `bug`: the task is a bug raised from
    a failed result of the case; `covers`: the case tests the task (a Story's acceptance)."""

    __tablename__ = "task_test_links"
    __table_args__ = (
        UniqueConstraint("task_id", "case_id", "kind"),
        CheckConstraint("kind IN ('bug', 'covers')", name="kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))
    result_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("test_results.id", ondelete="SET NULL")
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # The acceptance criterion a `covers` case tests (docs/specs/31 §20.3); None: the whole task.
    criterion_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_criteria.id", ondelete="SET NULL"), index=True
    )


class TaskCriterion(TimestampMixin, Base):
    """An acceptance criterion of a task (docs/specs/31 §20.3): a stable id that testcases link
    to, so a Story shows its coverage per criterion."""

    __tablename__ = "task_criteria"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    text: Mapped[str] = mapped_column(String(500))
    done: Mapped[bool] = mapped_column(server_default="false", default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TaskImport(TimestampMixin, Base):
    """A task made from another tracker's issue (docs/specs/31 §19.2): the old key keeps
    resolving to it (commits, MRs and test defects that still name ABC-123)."""

    __tablename__ = "task_imports"
    __table_args__ = (UniqueConstraint("source", "external_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(10))  # jira
    external_key: Mapped[str] = mapped_column(String(30))
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    imported_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
