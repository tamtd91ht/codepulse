"""CodePulse tasks as the DPI task source (docs/specs/31 §15, user 2026-11-16: "làm luôn phần
DPI đọc task CodePulse").

DPI reads its tasks from `issues` (docs/specs/25 §4.1), one source at a time (spec 27 §9). With
the source "codepulse", every task is written there one way (tasks → issues), together with its
MR / commit links and its production defect, so DPI scores it like any other task. The rows are
rebuilt from the task whenever it changes (a session hook, whatever the path: web, git, MCP);
nothing here imports the Jira integration.
"""

import math
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, event, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Commit,
    Issue,
    IssueCommitLink,
    IssueMrLink,
    MergeRequest,
    ProdDefect,
    Sprint,
    Task,
    TaskGitLink,
    TaskSpace,
    TaskStatus,
    TaskVersion,
)

SOURCE = "codepulse"
# issues.id of a task: far above GitLab's ids and Jira's (JIRA_ID_OFFSET = 10**15).
TASK_ID_OFFSET = 2 * 10**15
ACCOUNT_KIND = "codepulse_user"
# Task type → DPI type (spec 25 §4.1); a sub-task takes its parent's; epics are not tasks.
TYPES = {"story": "feature", "task": "chore", "bug": "bug"}
# A finished status with one of these names is a cancellation: no output, no row.
CANCELLED = {"won-t-do", "wont-do", "cancelled", "canceled", "huy", "khong-lam", "rejected"}
PROD_LABELS = {"production", "prod", "prod-bug"}
# Story points → (complexity, points), as spec 27 §6: the smallest level the value fits under.
SIZES: tuple[tuple[float, str, int], ...] = ((1, "XS", 1), (2, "S", 2), (3, "M", 3), (5, "L", 5))
TOP_SIZE = ("XL", 8)
PENDING = "codepulse_dpi_pending"


def issue_id(task_id: int) -> int:
    return TASK_ID_OFFSET + task_id


def enabled(session: Session) -> bool:
    from app.config import get_settings
    from app.jira.settings import task_source  # the DPI setting lives there for now

    return task_source(session, get_settings()) == SOURCE


def complexity(points: Decimal | None) -> tuple[str | None, int | None]:
    if points is None or points <= 0:
        return None, None
    value = float(points)
    for top, size, score in SIZES:
        if value <= top or math.isclose(value, top):
            return size, score
    return TOP_SIZE


def _slug(name: str) -> str:
    import re
    import unicodedata

    plain = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(c for c in plain if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def _account(session: Session, user_id: int | None) -> list[int]:
    """The person as an account DPI can match (issues name their assignees by account)."""
    if user_id is None:
        return []
    key = str(user_id)
    found = session.scalar(
        select(Account.id).where(Account.kind == ACCOUNT_KIND, Account.external_key == key)
    )
    if found is None:
        stmt = (
            insert(Account)
            .values(kind=ACCOUNT_KIND, external_key=key, user_id=user_id)
            .on_conflict_do_nothing(index_elements=["kind", "external_key"])
            .returning(Account.id)
        )
        found = session.scalar(stmt) or session.scalar(
            select(Account.id).where(Account.kind == ACCOUNT_KIND, Account.external_key == key)
        )
    return [int(found)] if found is not None else []


def _severity(task: Task, dpi_type: str | None) -> str | None:
    """A Bug labelled production (prod, prod-bug): P1 / P2 by priority, else P3."""
    if dpi_type != "bug" or not {x.lower() for x in task.labels or []} & PROD_LABELS:
        return None
    return {"highest": "P1", "high": "P2"}.get(task.priority, "P3")


def _row(session: Session, task: Task, base_url: str) -> dict[str, Any] | None:
    """The `issues` row of a task; None when it is not a DPI task (an epic, a cancelled one)."""
    if task.type == "epic":
        return None
    status = session.get(TaskStatus, task.status_id)
    if status is not None and status.category == "done" and _slug(status.name) in CANCELLED:
        return None
    parent = session.get(Task, task.parent_id) if task.parent_id else None
    if task.type == "subtask":
        dpi_type = TYPES.get(parent.type if parent else "", "chore")
    else:
        dpi_type = TYPES.get(task.type, "chore")
    size, size_points = complexity(task.story_points)
    sprint = session.get(Sprint, task.sprint_id) if task.sprint_id else None
    version = session.get(TaskVersion, task.version_id) if task.version_id else None
    due = (
        task.due_date
        or (sprint.end_date if sprint else None)
        or (version.release_date if version else None)
    )
    milestone = sprint.name if sprint else (version.name if version else None)
    labels = list(task.labels or [])
    if status is not None and "block" in status.name.lower():
        labels.append(f"blocked::{status.name}")
    closed = status is not None and status.category == "done"
    return {
        "id": issue_id(task.id),
        "source": SOURCE,
        "project_id": None,
        "jira_project_id": None,
        "iid": task.number,
        "key": task.key,
        # Only a sub-task has a parent here: a story under an epic is a task of its own.
        "parent_key": parent.key if parent is not None and task.type == "subtask" else None,
        "story_points": task.story_points,
        "title": task.title,
        "state": "closed" if closed else "opened",
        "labels": labels,
        "components": list(task.components or []),
        "assignee_account_ids": _account(session, task.assignee_id),
        "type": dpi_type,
        "complexity": size,
        "complexity_points": size_points,
        "prod_severity": _severity(task, dpi_type),
        "milestone": milestone,
        "due_date": due,
        "web_url": f"{base_url.rstrip('/')}/tasks?task={task.key}",
        "created_at_src": task.created_at,
        "closed_at_src": task.resolved_at if closed else None,
        "time_estimate_s": task.estimate_min * 60 if task.estimate_min else None,
        "time_spent_s": task.spent_min * 60 if task.spent_min else None,
        "business_project_ids": [task.business_project_id],
    }


def _links(session: Session, task: Task, ident: int) -> None:
    """The task's MRs and commits as DPI links (closes / mention / branch; fixes / refs)."""
    session.execute(delete(IssueMrLink).where(IssueMrLink.issue_id == ident))
    session.execute(delete(IssueCommitLink).where(IssueCommitLink.issue_id == ident))
    links = list(session.scalars(select(TaskGitLink).where(TaskGitLink.task_id == task.id)))
    mr_source: dict[int, str] = {}
    rank = {"closes": 3, "branch": 2, "mention": 1}

    def want(mr_id: int, source: str) -> None:
        if rank[source] > rank.get(mr_source.get(mr_id, ""), 0):
            mr_source[mr_id] = source

    for link in links:
        if link.kind == "mr" and link.mr_iid is not None:
            mr_id = session.scalar(
                select(MergeRequest.id).where(
                    MergeRequest.project_id == link.project_id, MergeRequest.iid == link.mr_iid
                )
            )
            if mr_id is not None:
                want(int(mr_id), "closes" if link.relation == "fixes" else "mention")
        elif link.kind == "branch" and link.branch:
            for mr_id in session.scalars(
                select(MergeRequest.id).where(
                    MergeRequest.project_id == link.project_id,
                    MergeRequest.source_branch == link.branch,
                )
            ):
                want(int(mr_id), "branch")
    for mr_id, source in mr_source.items():
        session.add(IssueMrLink(issue_id=ident, mr_id=mr_id, source=source))
    shas = {link.sha: link.relation for link in links if link.kind == "commit" and link.sha}
    known = (
        set(session.scalars(select(Commit.sha).where(Commit.sha.in_(list(shas)))))
        if shas
        else set()
    )
    for sha in known:
        session.add(IssueCommitLink(issue_id=ident, sha=sha, kind=shas[sha]))


def _defect(session: Session, ident: int, severity: str | None) -> None:
    """As the other sources do: a production bug has one defect row (RCA kept once decided)."""
    if not severity:
        session.execute(
            delete(ProdDefect).where(
                ProdDefect.issue_id == ident, ProdDefect.rca_status == "pending"
            )
        )
        session.execute(
            update(ProdDefect)
            .where(ProdDefect.issue_id == ident, ProdDefect.rca_status != "pending")
            .values(rca_status="withdrawn")
        )
        return
    stmt = insert(ProdDefect).values(issue_id=ident, severity=severity)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["issue_id"],
            set_={"severity": stmt.excluded.severity, "updated_at": func.now()},
        )
    )


def project(session: Session, task_ids: Iterable[int], base_url: str | None = None) -> int:
    """Rewrite the DPI rows of these tasks (gone or not a DPI task: their row goes)."""
    from app.config import get_settings

    base = base_url or get_settings().app_base_url
    written = 0
    for task_id in sorted(set(task_ids)):
        ident = issue_id(task_id)
        task = session.get(Task, task_id)
        row = _row(session, task, base) if task is not None else None
        if task is None or row is None:
            session.execute(delete(Issue).where(Issue.id == ident))  # links and defect cascade
            continue
        stmt = insert(Issue).values(**row)
        updates: dict[str, Any] = {k: stmt.excluded[k] for k in row if k != "id"}
        updates["updated_at"] = func.now()
        session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=updates))
        _links(session, task, ident)
        _defect(session, ident, row["prod_severity"])
        written += 1
    session.flush()
    return written


def project_all(session: Session) -> int:
    """Every task of every Task space (switching the DPI source to CodePulse, or a rebuild)."""
    ids = list(
        session.scalars(
            select(Task.id).where(
                Task.business_project_id.in_(select(TaskSpace.business_project_id))
            )
        )
    )
    stale = select(Issue.id).where(Issue.source == SOURCE)
    keep = {issue_id(i) for i in ids}
    gone = [i for i in session.scalars(stale) if i not in keep]
    if gone:
        session.execute(delete(Issue).where(Issue.id.in_(gone)))
    return project(session, ids)


# -- keeping the rows current ----------------------------------------------------------------


def _touched(session: Session) -> set[int]:
    pending: set[int] = session.info.setdefault(PENDING, set())
    return pending


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _context: Any) -> None:
    """Remember the tasks a flush changed (a task row, its git links, its status ...)."""
    if session.info.get("codepulse_dpi_running"):
        return
    pending = _touched(session)
    for obj in [*session.new, *session.dirty, *session.deleted]:
        if isinstance(obj, Task) and obj.id is not None:
            pending.add(obj.id)
            if obj in session.deleted:
                session.info[PENDING + "_deleted"] = True
            if obj.parent_id:
                pending.add(obj.parent_id)  # a sub-task's parent type / key may matter
        elif isinstance(obj, TaskGitLink) and obj.task_id is not None:
            pending.add(obj.task_id)


@event.listens_for(Session, "before_commit")
def _write(session: Session) -> None:
    """Before the commit: rewrite the DPI rows of the tasks changed, when DPI reads CodePulse.
    The commit's own flush comes after this hook, so it is done here first: a task deleted just
    before the commit is seen too."""
    if session.info.get("codepulse_dpi_running"):
        return
    if session.new or session.dirty or session.deleted:
        session.flush()
    pending = session.info.pop(PENDING, None)
    deleted = session.info.pop(PENDING + "_deleted", False)
    if not pending:
        return
    session.info["codepulse_dpi_running"] = True
    try:
        if enabled(session):
            with session.no_autoflush:
                project(session, pending)
                if deleted:
                    _drop_orphans(session)
    finally:
        session.info.pop("codepulse_dpi_running", None)


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    session.info.pop(PENDING, None)
    session.info.pop(PENDING + "_deleted", None)


def _drop_orphans(session: Session) -> None:
    """Rows of tasks deleted with their parent (the DB cascade): nothing names them."""
    from sqlalchemy import exists

    session.execute(
        delete(Issue).where(
            Issue.source == SOURCE,
            ~exists().where(Task.id == Issue.id - TASK_ID_OFFSET),
        )
    )
