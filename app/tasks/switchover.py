"""Switching a dự án over from Jira to Task (docs/specs/31 §21.6, roadmap round 3 §3.9).

CodePulse cannot make a Jira project read-only (that is a Jira admin's job); what it does on its
own side:

- **Old Jira keys still in use.** When a commit, MR or branch reaches a task through the key of
  an imported Jira issue (`task_imports`), the use is recorded (`task_key_aliases_seen`, once per
  key and commit / MR / branch) and its author gets one inbox note a day ("Dùng OCX-12 thay cho
  OMX-5"). Task > Cài đặt lists the uses of the last 7 days.
- **"Ngừng dùng Jira"** per dự án (`task_spaces.jira_retired_at`): bugs of testcases always go to
  CodePulse, the Jira import card stops importing. Readiness: every synced Jira issue imported and
  no old key used in the last READY_DAYS days; otherwise a manager may still switch, with a reason
  that goes into the audit log.
- **Lead checklist**: WIP + age warning, DoD, automation rules, first sprint, Jira import, members,
  each ticked from the data.
- **Demo data** for a training session: a small realistic set labelled `mau-demo`, titled
  "[Mẫu] …", and its removal (only tasks with that label; the demo sprint / version when empty).
"""

import logging
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Commit,
    Project,
    ProjectGrant,
    Sprint,
    Task,
    TaskComment,
    TaskSpace,
    TaskStatus,
    TaskVersion,
    User,
    UserNotification,
)
from app.models.tasks import TaskKeyAliasSeen
from app.notifications.inbox import notify

log = logging.getLogger(__name__)

USE_DAYS = 7  # the "Mã Jira cũ vẫn được dùng" list
READY_DAYS = 14  # no old key used for this long before "Ngừng dùng Jira" (about one sprint)
MAX_USES = 100
NOTE_KIND = "task.remind"
NOTE_FRESH = timedelta(days=1)  # older uses (a first sync of history) notify nobody
MIN_REASON = 10
SUGGEST_DAYS = 90

DEMO_LABEL = "mau-demo"
DEMO_PREFIX = "[Mẫu] "
DEMO_SPRINT = "[Mẫu] Sprint demo"
DEMO_VERSION = "[Mẫu] v1.0"
# The five rules the checklist recommends (§21.6); five rules on, whichever, also count.
RECOMMENDED_RULES = (
    "parent_done_ask",
    "assign_on_start",
    "bug_highest_notify",
    "unblock_notify",
    "overdue_remind",
)
MIN_RULES = 5
MIN_MEMBERS = 2


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# -- old Jira keys still in use ------------------------------------------------------------------


def alias_used(session: Session, space: TaskSpace, external_key: str, task: Task) -> None:
    """Called by `app.tasks.git._tasks` when an imported Jira key names `task`. What used it is
    read from the caller of `_tasks` (`_commit`: the commit; `on_sync`: the MR; `on_push`: the
    branch), so git.py needs a single line. Never fails the git sync."""
    try:
        caller = sys._getframe(2)
        where, scope = caller.f_code.co_name, caller.f_locals
        del caller
        with session.begin_nested():  # a failure takes back only this record
            _record_from(session, space, external_key, task, where, scope)
    except Exception:
        log.warning(
            "jira alias use not recorded",
            extra={"key": external_key, "task": task.key},
            exc_info=True,
        )


def _gitlab_user(session: Session, author: Any) -> tuple[int | None, str | None]:
    if author is None:
        return None, None
    name = getattr(author, "username", None)
    ident = getattr(author, "id", None)
    user_id = None
    if ident is not None:
        user_id = session.scalar(
            select(Account.user_id).where(
                Account.kind == "gitlab_user", Account.external_key == str(ident)
            )
        )
    return (int(user_id) if user_id is not None else None), name


def _record_from(
    session: Session,
    space: TaskSpace,
    external_key: str,
    task: Task,
    where: str,
    scope: dict[str, Any],
) -> None:
    from app.tasks.git import user_of_email  # git imports this module

    project = scope.get("project")
    project_id = getattr(project, "id", None)
    if where == "_commit" and scope.get("sha"):
        sha = str(scope["sha"])
        email = scope.get("author_email")
        record(
            session,
            space,
            external_key,
            task,
            kind="commit",
            ref=f"commit:{sha}",
            project_id=project_id,
            sha=sha,
            author_user_id=user_of_email(session, email),
            author=email or None,
            at=_aware(scope.get("when")),
        )
    elif where == "on_sync" and scope.get("mr") is not None:
        mr = scope["mr"]
        user_id, name = _gitlab_user(session, getattr(mr, "author", None))
        record(
            session,
            space,
            external_key,
            task,
            kind="mr",
            ref=f"mr:{project_id}:{mr.iid}",
            project_id=project_id,
            mr_iid=int(mr.iid),
            author_user_id=user_id,
            author=name,
            at=_aware(getattr(mr, "updated_at", None) or getattr(mr, "created_at", None)),
        )
    elif where == "on_push" and scope.get("branch"):
        branch = str(scope["branch"])
        email = scope.get("pusher_email")
        record(
            session,
            space,
            external_key,
            task,
            kind="branch",
            ref=f"branch:{project_id}:{branch}"[:300],
            project_id=project_id,
            branch=branch[:255],
            author_user_id=user_of_email(session, email),
            author=email or None,
        )


def record(
    session: Session,
    space: TaskSpace,
    external_key: str,
    task: Task,
    *,
    kind: str,
    ref: str,
    project_id: int | None = None,
    sha: str | None = None,
    mr_iid: int | None = None,
    branch: str | None = None,
    author_user_id: int | None = None,
    author: str | None = None,
    at: datetime | None = None,
) -> bool:
    """One use of an old key (once per key and commit / MR / branch); True when new. A new,
    recent use tells its author once a day which key to use instead."""
    when = at or _now()
    new_id = session.scalar(
        pg_insert(TaskKeyAliasSeen)
        .values(
            business_project_id=space.business_project_id,
            external_key=external_key[:30],
            task_id=task.id,
            kind=kind,
            ref=ref,
            project_id=project_id,
            sha=sha,
            mr_iid=mr_iid,
            branch=branch,
            author_user_id=author_user_id,
            author=(author or "")[:255] or None,
            at=when,
        )
        .on_conflict_do_nothing()
        .returning(TaskKeyAliasSeen.id)
    )
    if new_id is None:
        return False
    if author_user_id is not None and when >= _now() - NOTE_FRESH:
        _tell_author(session, author_user_id, external_key, task, kind, sha, mr_iid, branch)
    return True


def _tell_author(
    session: Session,
    user_id: int,
    external_key: str,
    task: Task,
    kind: str,
    sha: str | None,
    mr_iid: int | None,
    branch: str | None,
) -> None:
    ref = f"jira-alias:{user_id}:{_now().date().isoformat()}"
    sent = session.scalar(
        select(UserNotification.id).where(
            UserNotification.user_id == user_id, UserNotification.ref == ref
        )
    )
    if sent is not None:
        return
    where = {
        "commit": f"Commit {(sha or '')[:8]}",
        "mr": f"MR !{mr_iid}",
        "branch": f"Nhánh {branch}",
    }[kind]
    notify(
        session,
        [user_id],
        kind=NOTE_KIND,
        title=f"Dùng {task.key} thay cho {external_key}",
        body=(
            f"{where} vẫn ghi mã Jira cũ {external_key}. Mã cũ vẫn nối đúng task, nhưng dự án "
            f"đang chuyển khỏi Jira: hãy ghi {task.key} trong commit, MR và tên nhánh."
        ),
        link=f"/tasks?task={task.key}",
        tone="warn",
        ref=ref,
    )


def uses(session: Session, bp_id: int, days: int = USE_DAYS) -> dict[str, Any]:
    """The old keys used in the last `days` days: newest first, who and where."""
    since = _now() - timedelta(days=days)
    where = (
        TaskKeyAliasSeen.business_project_id == bp_id,
        TaskKeyAliasSeen.at >= since,
    )
    total = int(
        session.scalar(select(func.count()).select_from(TaskKeyAliasSeen).where(*where)) or 0
    )
    rows = session.execute(
        select(TaskKeyAliasSeen, Task.key, Project.path_with_namespace, User)
        .join(Task, Task.id == TaskKeyAliasSeen.task_id)
        .outerjoin(Project, Project.id == TaskKeyAliasSeen.project_id)
        .outerjoin(User, User.id == TaskKeyAliasSeen.author_user_id)
        .where(*where)
        .order_by(TaskKeyAliasSeen.at.desc(), TaskKeyAliasSeen.id.desc())
        .limit(MAX_USES)
    ).tuples()
    items = []
    authors: set[str] = set()
    for seen, task_key, repo, user in rows:
        who = user.full_name if user is not None else seen.author
        if who:
            authors.add(who)
        items.append(
            {
                "external_key": seen.external_key,
                "task_key": task_key,
                "kind": seen.kind,
                "sha": seen.sha,
                "mr_iid": seen.mr_iid,
                "branch": seen.branch,
                "repo": repo,
                "author": (
                    {"id": user.id, "full_name": user.full_name, "email": user.primary_email}
                    if user is not None
                    else None
                ),
                "author_text": seen.author,
                "at": seen.at.isoformat(),
            }
        )
    return {"days": days, "total": total, "authors": len(authors), "items": items}


def _uses_since(session: Session, bp_id: int, days: int) -> int:
    since = _now() - timedelta(days=days)
    return int(
        session.scalar(
            select(func.count())
            .select_from(TaskKeyAliasSeen)
            .where(TaskKeyAliasSeen.business_project_id == bp_id, TaskKeyAliasSeen.at >= since)
        )
        or 0
    )


# -- "Ngừng dùng Jira" ---------------------------------------------------------------------------


def is_retired(session: Session, bp_id: int) -> bool:
    space = session.get(TaskSpace, bp_id)
    return space is not None and space.jira_retired_at is not None


def readiness(session: Session, space: TaskSpace) -> dict[str, Any]:
    from app.tasks import importer

    sources = importer.sources(session, space.business_project_id)
    missing = sum(max(int(s["issues"]) - int(s["imported"]), 0) for s in sources)
    used = _uses_since(session, space.business_project_id, READY_DAYS)
    problems = []
    if missing:
        problems.append(f"Còn {missing} issue Jira đã đồng bộ chưa nhập thành task.")
    if used:
        problems.append(f"Mã Jira cũ vẫn được dùng {used} lần trong {READY_DAYS} ngày qua.")
    return {
        "sources": sources,
        "not_imported": missing,
        "days": READY_DAYS,
        "alias_uses": used,
        "ready": not problems,
        "problems": problems,
    }


def retire(session: Session, space: TaskSpace, user_id: int, reason: str | None) -> dict[str, Any]:
    """Switch the dự án off Jira. Not ready: 409 unless a reason (≥ MIN_REASON chars) is given;
    the caller writes the audit row with the readiness and the reason."""
    if space.jira_retired_at is not None:
        raise HTTPException(409, "dự án đã ngừng dùng Jira")
    ready = readiness(session, space)
    text = (reason or "").strip()
    if not ready["ready"] and len(text) < MIN_REASON:
        raise HTTPException(
            409,
            " ".join(ready["problems"])
            + f" Muốn vẫn ngừng dùng Jira thì ghi lý do (ít nhất {MIN_REASON} ký tự).",
        )
    space.jira_retired_at = _now()
    space.jira_retired_by = user_id
    session.flush()
    return {**ready, "override": not ready["ready"], "reason": text or None}


def restore(session: Session, space: TaskSpace) -> None:
    if space.jira_retired_at is None:
        raise HTTPException(409, "dự án vẫn đang dùng Jira")
    space.jira_retired_at = None
    space.jira_retired_by = None
    session.flush()


# -- the Lead checklist ---------------------------------------------------------------------------


def _members(session: Session, bp_id: int) -> tuple[int, int]:
    """(people granted the dự án, people who committed to its repos lately without a grant)."""
    granted = select(ProjectGrant.user_id).where(ProjectGrant.business_project_id == bp_id)
    have = session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.id.in_(granted), User.active.is_not(False))
    )
    since = _now() - timedelta(days=SUGGEST_DAYS)
    missing = session.scalar(
        select(func.count(func.distinct(User.id)))
        .select_from(User)
        .join(Account, Account.user_id == User.id)
        .join(Commit, Commit.author_account_id == Account.id)
        .join(Project, Project.id == Commit.project_id)
        .where(
            Project.business_project_id == bp_id,
            Commit.authored_at >= since,
            User.active.is_not(False),
            User.id.not_in(granted),
        )
    )
    return int(have or 0), int(missing or 0)


def checklist(session: Session, space: TaskSpace) -> dict[str, Any]:
    from app.tasks import automation, importer, quality

    bp_id = space.business_project_id
    doing = list(
        session.scalars(
            select(TaskStatus).where(
                TaskStatus.business_project_id == bp_id, TaskStatus.category == "doing"
            )
        )
    )
    guarded = [s.name for s in doing if s.wip_limit and s.age_warning_hours]
    dod = quality.dod_settings(space)["checks"]
    config = automation.config_of(space)
    on = [k for k, v in config.items() if v["on"]]
    recommended_on = all(config.get(k, {}).get("on") for k in RECOMMENDED_RULES)
    sprints = int(
        session.scalar(
            select(func.count())
            .select_from(Sprint)
            .where(Sprint.business_project_id == bp_id, Sprint.name != DEMO_SPRINT)
        )
        or 0
    )
    sources = importer.sources(session, bp_id)
    missing = sum(max(int(s["issues"]) - int(s["imported"]), 0) for s in sources)
    have, suggested = _members(session, bp_id)
    items = [
        {
            "key": "wip",
            "label": "Đặt WIP và cảnh báo tuổi cột",
            "done": bool(guarded),
            "applies": True,
            "detail": (
                f"Đã đặt ở {', '.join(guarded)}"
                if guarded
                else "Chưa trạng thái “Đang làm” nào có cả giới hạn WIP và cảnh báo tuổi."
            ),
            "fix": "statuses",
        },
        {
            "key": "dod",
            "label": "Bật Definition of Done",
            "done": bool(dod),
            "applies": True,
            "detail": f"{len(dod)} mục" if dod else "Chưa chọn mục DoD nào.",
            "fix": "dod",
        },
        {
            "key": "automation",
            "label": f"Bật {MIN_RULES} quy tắc tự động cơ bản",
            "done": len(on) >= MIN_RULES or recommended_on,
            "applies": True,
            "detail": f"Đang bật {len(on)}/{len(config)} quy tắc",
            "fix": "automation",
        },
        {
            "key": "sprint",
            "label": "Tạo sprint đầu tiên",
            "done": sprints > 0,
            "applies": space.board_mode == "scrum",
            "detail": f"{sprints} sprint"
            if sprints
            else "Chưa có sprint (không tính sprint demo).",
            "fix": "backlog",
        },
        {
            "key": "jira_import",
            "label": "Nhập issue từ Jira",
            "done": bool(sources) and missing == 0,
            "applies": bool(sources),
            "detail": (
                "Đã nhập hết issue Jira đã đồng bộ"
                if not missing
                else f"Còn {missing} issue Jira chưa nhập"
            ),
            "fix": "jira",
        },
        {
            "key": "members",
            "label": "Cấp quyền cho thành viên team",
            "done": have >= MIN_MEMBERS and suggested == 0,
            "applies": True,
            "detail": f"{have} người có quyền"
            + (f" · {suggested} người commit vào repo chưa có quyền" if suggested else ""),
            "fix": "members",
        },
    ]
    counted = [i for i in items if i["applies"]]
    done = sum(1 for i in counted if i["done"])
    return {
        "items": items,
        "done": done,
        "total": len(counted),
        "complete": done == len(counted),
        "recommended_rules": list(RECOMMENDED_RULES),
    }


def turn_on_recommended(space: TaskSpace) -> None:
    from app.tasks import automation

    automation.set_config(space, {k: {"on": True} for k in RECOMMENDED_RULES})


# -- demo data ------------------------------------------------------------------------------------


def _demo_ids(session: Session, bp_id: int) -> list[int]:
    return list(
        session.scalars(
            select(Task.id).where(
                Task.business_project_id == bp_id,
                Task.labels.contains([DEMO_LABEL]),
                # Both marks: a real task someone labelled mau-demo by hand is never demo data.
                Task.title.startswith(DEMO_PREFIX, autoescape=True),
            )
        )
    )


def has_demo(session: Session, bp_id: int) -> int:
    return len(_demo_ids(session, bp_id))


def create_demo(session: Session, space: TaskSpace, user_id: int) -> dict[str, Any]:
    """A small set for a training session: an epic with stories, a task, a sub-task, a bug, a
    sprint (not started), a version and a few comments. Assigned to who made it (so nobody else
    is notified). A second set while one exists: 409."""
    from app.tasks import service

    bp_id = space.business_project_id
    if _demo_ids(session, bp_id):
        raise HTTPException(409, "dự án đã có dữ liệu demo: xoá trước khi tạo lại")
    today = date.today()
    # A demo sprint kept by an earlier removal (a real task was in it) is reused, not doubled.
    sprint = session.scalars(
        select(Sprint).where(
            Sprint.business_project_id == bp_id,
            Sprint.name == DEMO_SPRINT,
            Sprint.state == "planned",
        )
    ).first()
    if sprint is None:
        sprint = Sprint(
            business_project_id=bp_id,
            name=DEMO_SPRINT,
            goal="Buổi hướng dẫn: thử kéo thả, bình luận, chuyển trạng thái",
            start_date=today,
            end_date=today + timedelta(days=14),
        )
        session.add(sprint)
    version = session.scalars(
        select(TaskVersion).where(
            TaskVersion.business_project_id == bp_id, TaskVersion.name == DEMO_VERSION
        )
    ).first()
    if version is None:
        version = TaskVersion(
            business_project_id=bp_id,
            name=DEMO_VERSION,
            description="Phiên bản mẫu cho buổi hướng dẫn",
            release_date=today + timedelta(days=30),
        )
        session.add(version)
    session.flush()
    statuses = service.statuses_of(session, bp_id)
    doing = next((s for s in statuses if s.category == "doing"), None)
    done = next((s for s in statuses if s.category == "done"), None)

    def kind(wanted: str) -> str:
        return wanted if wanted in space.types else "task"

    def make(title: str, wanted: str, **extra: Any) -> Task:
        data: dict[str, Any] = {
            "title": DEMO_PREFIX + title,
            "type": kind(wanted),
            "labels": [DEMO_LABEL],
            "assignee_id": user_id,
            **extra,
        }
        if data.get("parent_key") and data["type"] in ("epic",):
            data.pop("parent_key")
        return service.create_task(session, space, data, user_id)

    made: list[Task] = []
    epic = make(
        "Đăng nhập bằng OTP",
        "epic",
        description="Khách hàng đăng nhập bằng mã OTP gửi qua SMS thay cho mật khẩu.",
        version_id=version.id,
    )
    made.append(epic)
    parent = epic.key if epic.type == "epic" else None
    story_a = make(
        "Gửi OTP qua SMS",
        "story",
        parent_key=parent,
        description="Là khách hàng, tôi nhận mã OTP 6 số qua SMS trong vòng 30 giây.",
        story_points=3,
        sprint_id=sprint.id,
        version_id=version.id,
        due_date=today + timedelta(days=5),
        status_id=doing.id if doing else None,
    )
    story_b = make(
        "Nhập OTP và xác thực",
        "story",
        parent_key=parent,
        description="Là khách hàng, tôi nhập OTP để đăng nhập; sai 5 lần thì khoá 15 phút.",
        story_points=5,
        sprint_id=sprint.id,
        version_id=version.id,
        due_date=today + timedelta(days=9),
    )
    docs = make(
        "Viết tài liệu API OTP",
        "task",
        story_points=2,
        sprint_id=sprint.id,
        status_id=done.id if done else None,
    )
    bug = make(
        "OTP hết hạn sớm hơn 5 phút",
        "bug",
        description=(
            "Các bước: gửi OTP, chờ 2 phút, nhập mã.\n\nThực tế: báo hết hạn. Mong đợi: còn hạn."
        ),
        priority=service.default_priority(session, bp_id),
        story_points=1,
        sprint_id=sprint.id,
    )
    backlog = make("Cấu hình nhà cung cấp SMS dự phòng", "task", story_points=2)
    made += [story_a, story_b, docs, bug, backlog]
    if "subtask" in space.types:
        made.append(make("Giới hạn số lần gửi lại OTP", "subtask", parent_key=story_a.key))
    for task, body in (
        (story_a, "Đã nối nhà cung cấp SMS chính, đang thử trên staging."),
        (bug, "Tái hiện được trên staging, nghi lệch múi giờ giữa API và Redis."),
        (bug, "Đúng rồi, TTL đang tính theo UTC+7. Sửa trong nhánh bugfix."),
    ):
        session.add(TaskComment(task_id=task.id, author_id=user_id, body=body))
    session.flush()
    return {
        "created": len(made),
        "keys": [t.key for t in made],
        "sprint_id": sprint.id,
        "version_id": version.id,
    }


def delete_demo(session: Session, bp_id: int) -> dict[str, Any]:
    """Every task labelled `mau-demo` in the dự án, nothing else: a real sub-task under a demo
    task is detached first (the cascade would take it). Then the demo sprint and version when
    no task is left in them. Returns the attachment paths to remove after commit."""
    from app.api.routers.tasks import _files_of

    ids = _demo_ids(session, bp_id)
    # Through the ORM (not bulk statements): the DPI projection (app/tasks/dpi.py) follows the
    # session's changed and deleted tasks.
    if ids:
        for child in session.scalars(
            select(Task).where(Task.parent_id.in_(ids), Task.id.not_in(ids))
        ):
            child.parent_id = None
        session.flush()
    files = _files_of(session, ids) if ids else []
    left = {t.id: t for t in session.scalars(select(Task).where(Task.id.in_(ids)))}
    while left:  # leaves first, so the database cascade never removes a row the ORM deletes
        parents = {t.parent_id for t in left.values() if t.parent_id in left}
        for ident in [i for i in left if i not in parents]:
            session.delete(left.pop(ident))
        session.flush()
    sprints = 0
    for sprint in session.scalars(
        select(Sprint).where(Sprint.business_project_id == bp_id, Sprint.name == DEMO_SPRINT)
    ):
        if session.scalar(select(Task.id).where(Task.sprint_id == sprint.id).limit(1)) is None:
            session.delete(sprint)
            sprints += 1
    versions = 0
    for version in session.scalars(
        select(TaskVersion).where(
            TaskVersion.business_project_id == bp_id, TaskVersion.name == DEMO_VERSION
        )
    ):
        if session.scalar(select(Task.id).where(Task.version_id == version.id).limit(1)) is None:
            session.delete(version)
            versions += 1
    session.flush()
    return {"deleted": len(ids), "sprints": sprints, "versions": versions, "files": files}
