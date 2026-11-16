"""The DPI task source (docs/specs/27 §9, docs/specs/31 §15-16): which tasks DPI reads,
GitLab Issues, Jira or CodePulse's own tasks. One at a time, so nothing counts twice.

Moved out of the Jira integration (user 2026-11-16: "chuyển nút đổi nguồn sang trang DPI"):
the setting is DPI's, it stays when Jira goes. app/jira/settings.py re-exports these names.
"""

from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.config import Settings
from app.models import AppSetting, Project

TASK_SOURCE_KEY = "dpi_task_source"
# "codepulse": CodePulse's own tasks (docs/specs/31 §15).
TaskSource = Literal["gitlab", "jira", "codepulse"]
TASK_SOURCES: tuple[TaskSource, ...] = ("gitlab", "jira", "codepulse")
LABELS: dict[str, str] = {
    "codepulse": "Task CodePulse",
    "jira": "Jira",
    "gitlab": "GitLab Issues",
}
NOTES: dict[str, str] = {
    "codepulse": (
        "Mọi task CodePulse (trừ Epic và task Won't Do) thành task của DPI ngay; sửa task là DPI "
        "cập nhật theo. Collector GitLab ngừng lấy issue."
    ),
    "jira": "Collector GitLab ngừng lấy issue; dữ liệu GitLab Issues cũ vẫn giữ.",
    "gitlab": "GitLab lấy lại issue của mọi repo (đồng bộ đầy đủ chạy nền).",
}


def jira_ready(session: Session, settings: Settings) -> bool:
    """Jira is connected (the Jira integration may be gone: then never)."""
    try:
        from app.jira.settings import jira_configured
    except ImportError:  # pragma: no cover - once the Jira integration is removed
        return False
    try:
        return bool(jira_configured(session, settings))
    except Exception:  # an unreadable Jira setting never breaks DPI
        return False


def task_source(session: Session, settings: Settings) -> TaskSource:
    """The stored choice; none stored: Jira when Jira is configured, else GitLab."""
    row = session.get(AppSetting, TASK_SOURCE_KEY)
    if row is not None and row.value in TASK_SOURCES:
        return {"jira": "jira", "codepulse": "codepulse"}.get(row.value, "gitlab")  # type: ignore[return-value]
    return "jira" if jira_ready(session, settings) else "gitlab"


def set_task_source(session: Session, source: TaskSource, user_id: int | None) -> None:
    if source not in TASK_SOURCES:
        raise ValueError(f"unknown task source {source!r}")
    row = session.get(AppSetting, TASK_SOURCE_KEY)
    if row is None:
        session.add(AppSetting(key=TASK_SOURCE_KEY, value=source, set_by=user_id))
    else:
        row.value, row.set_by = source, user_id


def switch(
    session: Session, settings: Settings, source: TaskSource, user_id: int | None
) -> tuple[dict[str, Any], list[int]]:
    """Change the source (the caller commits, then queues the returned repos' full syncs).

    Back to GitLab: its issues were not fetched meanwhile and the repos' sync windows moved on,
    so every tracked repo syncs in full. To CodePulse: every task is written for DPI now."""
    before = task_source(session, settings)
    set_task_source(session, source, user_id)
    refetch: list[int] = []
    if before != "gitlab" and source == "gitlab":
        refetch = list(
            session.scalars(select(Project.id).where(Project.tracked).order_by(Project.id))
        )
    projected = 0
    if source == "codepulse" and before != "codepulse":
        from app.tasks.dpi import project_all

        projected = project_all(session)
    audit(
        session,
        user_id,
        "dpi.task_source",
        "dpi",
        before=before,
        source=source,
        full_syncs=refetch,
        tasks_projected=projected,
    )
    return {
        "task_source": source,
        "full_syncs_queued": len(refetch),
        "tasks_projected": projected,
    }, refetch


def queue_full_syncs(queue: Any, project_ids: list[int]) -> None:
    from app.collector.sync import SYNC_JOB_TIMEOUT

    for project_id in project_ids:
        queue.enqueue(
            "app.collector.sync.sync_project_job",
            project_id,
            full=True,
            job_id=f"sync-project-{project_id}-full",
            job_timeout=SYNC_JOB_TIMEOUT,
        )


def view(session: Session, settings: Settings, can_change: bool) -> dict[str, Any]:
    """What the DPI page shows: the source, the choices and whether this person may switch."""
    current = task_source(session, settings)
    ready = jira_ready(session, settings)
    return {
        "task_source": current,
        "label": LABELS[current],
        "can_change": can_change,
        "options": [
            {
                "value": value,
                "label": LABELS[value],
                "note": NOTES[value],
                "available": value != "jira" or ready,
            }
            for value in TASK_SOURCES
        ],
    }
