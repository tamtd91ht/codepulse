"""Task (docs/specs/31): access rules, task spaces, tasks and their history, what the API sends.

Independent of the Jira integration (docs/specs/27): nothing here touches `issues`."""

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.avatars import avatar_url
from app.models import (
    BusinessProject,
    Connection,
    Deployment,
    MergeRequest,
    Pipeline,
    Project,
    ProjectGrant,
    Review,
    ReviewFinding,
    Sprint,
    Task,
    TaskGitLink,
    TaskHistory,
    TaskImport,
    TaskPriority,
    TaskSpace,
    TaskStatus,
    TaskVersion,
    TaskWatcher,
    User,
)
from app.models.tasks import PRIORITIES, RESOLUTIONS, TASK_TYPES
from app.notifications.inbox import notify

# One language (docs/specs/31 §19.1): new spaces get Vietnamese names; "won't do" is a
# resolution now, not a status. Smart commits keep their English words as aliases (git.py).
DEFAULT_STATUSES: tuple[tuple[str, str], ...] = (
    ("Cần làm", "todo"),
    ("Đang làm", "doing"),
    ("Đang review", "doing"),
    ("Xong", "done"),
)
START_STATUS, REVIEW_STATUS, DONE_STATUS = "Đang làm", "Đang review", "Xong"
RESOLUTION_LABELS = {
    "done": "Đã xong",
    "wont_do": "Không làm",
    "duplicate": "Trùng",
    "cannot_reproduce": "Không tái hiện được",
}
# Types whose story points count for a sprint (epics hold their children's; sub-tasks are part
# of their parent's), docs/specs/31 §19.1.
POINTED_TYPES = ("story", "task", "bug")
RULE_FLAGS = (
    "start_on_commit",
    "review_on_mr",
    "done_on_merge",
    "reopen_on_mr_close",
    "block_done_on_critical",
    "mr_comment",
)
DEFAULT_FLAGS = {
    "start_on_commit": True,
    "review_on_mr": True,
    "done_on_merge": True,
    "reopen_on_mr_close": True,
    "block_done_on_critical": False,
    "mr_comment": False,
}
SEVERE = ("critical", "high")
RANK_STEP = 1024.0


def link_of(key: str) -> str:
    return f"/tasks?task={key}"


# -- access (docs/specs/31 §4) ------------------------------------------------------------------


def can_view_space(p: Principal, bp_id: int) -> bool:
    return p.can("tasks.view") and p.can_view_project(bp_id)


def can_manage_space(p: Principal, bp_id: int) -> bool:
    scope = p.scope("tasks.manage")
    if scope == "all":
        return p.can_view_project(bp_id)
    return scope == "project" and p.can_manage_project(bp_id)


# The task rights, as Jira's permission scheme splits them (docs/specs/31 §4.1). Scoped ones
# reach: own = tasks one reported or is assigned (delete: reported), project = every task of a
# dự án one manages, all = every task one sees. tasks.manage over a dự án holds all of them there.
TASK_RIGHTS = (
    "edit",
    "assign",
    "transition",
    "delete",
    "comment",
    "attach",
    "worklog",
    "link",
    "moderate",
)


def may(p: Principal, right: str, task: Task) -> bool:
    """Holds `tasks.<right>` over this task."""
    bp_id = task.business_project_id
    if not can_view_space(p, bp_id):
        return False
    if can_manage_space(p, bp_id):
        return True
    scope = p.scope(f"tasks.{right}")
    if scope is None:
        return False
    if scope in ("all", "flag"):
        return True
    if scope == "project" and p.can_manage_project(bp_id):
        return True
    # "project" also covers one's own tasks elsewhere, as tasks.edit always did; not for delete
    # and moderate, which needed managing the dự án before they were split out.
    if scope != "own" and not (scope == "project" and right not in ("delete", "moderate")):
        return False
    mine = (task.reporter_id,) if right == "delete" else (task.reporter_id, task.assignee_id)
    return p.user_id is not None and p.user_id in mine


def require(p: Principal, right: str, task: Task) -> None:
    if not may(p, right, task):
        raise HTTPException(403, f"bạn không có quyền {RIGHT_LABELS[right]} task {task.key}")


RIGHT_LABELS = {
    "edit": "sửa",
    "assign": "giao",
    "transition": "chuyển trạng thái",
    "delete": "xoá",
    "comment": "bình luận ở",
    "attach": "đính kèm file vào",
    "worklog": "ghi giờ cho",
    "link": "liên kết",
    "moderate": "sửa / xoá nội dung của người khác ở",
}


def rights(p: Principal, task: Task) -> dict[str, bool]:
    """What this person may do on this task (the detail's `can`)."""
    return {r: may(p, r, task) for r in TASK_RIGHTS}


def can_create(p: Principal, bp_id: int) -> bool:
    return can_view_space(p, bp_id) and (p.can("tasks.create") or can_manage_space(p, bp_id))


def can_assign_new(p: Principal, bp_id: int) -> bool:
    """May name someone else as the assignee of a task one creates (one is its reporter, so
    any scope of tasks.assign reaches it)."""
    return p.can("tasks.assign") or can_manage_space(p, bp_id)


def can_sprint(p: Principal, bp_id: int) -> bool:
    """tasks.sprint: plan, start, complete sprints of the dự án."""
    if not can_view_space(p, bp_id):
        return False
    if can_manage_space(p, bp_id):
        return True
    scope = p.scope("tasks.sprint")
    return scope == "all" or (scope == "project" and p.can_manage_project(bp_id))


def can_edit(p: Principal, task: Task) -> bool:
    return may(p, "edit", task)


def space_or_404(session: Session, p: Principal, bp_id: int) -> TaskSpace:
    space = session.get(TaskSpace, bp_id)
    if space is None or not can_view_space(p, bp_id):
        raise HTTPException(404, "dự án chưa bật Task")
    return space


def task_or_404(session: Session, p: Principal, key: str) -> Task:
    task = session.scalars(select(Task).where(Task.key == key.upper())).first()
    if task is None or not can_view_space(p, task.business_project_id):
        raise HTTPException(404, "không thấy task")
    return task


def require_edit(p: Principal, task: Task) -> None:
    if not can_edit(p, task):
        raise HTTPException(403, "bạn không sửa được task này")


def require_sprint(p: Principal, bp_id: int) -> None:
    if not can_sprint(p, bp_id):
        raise HTTPException(403, "cần quyền quản lý sprint của dự án (tasks.sprint)")


def require_manage(p: Principal, bp_id: int) -> None:
    if not can_manage_space(p, bp_id):
        raise HTTPException(403, "cần quyền quản lý Task của dự án")


# -- spaces ---------------------------------------------------------------------------------------

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def statuses_of(session: Session, bp_id: int) -> list[TaskStatus]:
    return list(
        session.scalars(
            select(TaskStatus)
            .where(TaskStatus.business_project_id == bp_id)
            .order_by(TaskStatus.position, TaskStatus.id)
        )
    )


def rules_of(space: TaskSpace) -> dict[str, Any]:
    return {**DEFAULT_FLAGS, **(space.rules or {})}


def enable_space(
    session: Session,
    bp_id: int,
    key: str,
    types: list[str] | None,
    rules: dict[str, Any] | None,
    user_id: int,
    template: str | None = None,
    board_mode: str | None = None,
) -> TaskSpace:
    """Turn Task on for a dự án (with a template's workflow, §19.6), or change its settings.
    `template` applies only when turning Task on; given for an existing space it is a 422."""
    from app.tasks.templates import check_board_mode, template_or_422

    key = key.strip().upper()
    tpl = template_or_422(template)
    if board_mode is not None:
        check_board_mode(board_mode)
    if not KEY_RE.match(key):
        raise HTTPException(422, "mã gồm 2-10 chữ in hoa / số, bắt đầu bằng chữ (ví dụ CRM)")
    if types is not None:
        bad = [t for t in types if t not in TASK_TYPES]
        if bad or not types:
            raise HTTPException(422, f"loại không hợp lệ: {bad}" if bad else "chọn ít nhất 1 loại")
    clash = session.scalars(
        select(TaskSpace).where(TaskSpace.key == key, TaskSpace.business_project_id != bp_id)
    ).first()
    if clash is not None:
        raise HTTPException(409, f"mã {key} đã dùng cho dự án khác")
    imported = session.scalar(
        select(TaskImport.id).where(TaskImport.external_key.like(f"{key}-%")).limit(1)
    )
    if imported is not None:
        raise HTTPException(
            409, f"mã {key} là mã của issue Jira đã nhập (ví dụ {key}-1): chọn mã khác"
        )
    space = session.get(TaskSpace, bp_id, with_for_update=True)
    if space is None:
        space = TaskSpace(
            business_project_id=bp_id,
            key=key,
            next_number=1,
            types=list(types or tpl.types),
            rules=dict(DEFAULT_FLAGS),
            board_mode=board_mode or tpl.board_mode,
            created_by=user_id,
        )
        session.add(space)
        session.flush()
        ids: dict[str, int] = {}
        for i, (name, category) in enumerate(tpl.statuses):
            status = TaskStatus(business_project_id=bp_id, name=name, category=category, position=i)
            session.add(status)
            session.flush()
            ids[name] = status.id
        seed_priorities(session, bp_id)
        space.rules = {
            **DEFAULT_FLAGS,
            **tpl.flags,
            "start": ids[tpl.start] if tpl.start else None,
            "review": ids[tpl.review] if tpl.review else None,
            "done": ids[tpl.done],
        }
    else:
        if template is not None:
            raise HTTPException(422, "mẫu chỉ áp dụng khi bật Task lần đầu")
        if board_mode is not None:
            space.board_mode = board_mode
        if key != space.key:
            used = session.scalar(
                select(func.count()).select_from(Task).where(Task.business_project_id == bp_id)
            )
            if used:
                raise HTTPException(409, "dự án đã có task: không đổi mã được nữa")
            space.key = key
        if types is not None:
            space.types = list(types)
    if rules is not None:
        valid = {s.id for s in statuses_of(session, bp_id)}
        merged = dict(space.rules or {})
        for name, value in rules.items():
            if name in RULE_FLAGS:
                merged[name] = bool(value)
            elif name in ("start", "review", "done"):
                if value is not None and value not in valid:
                    raise HTTPException(422, f"trạng thái {value} không thuộc dự án")
                merged[name] = value
            else:
                raise HTTPException(422, f"luật không biết: {name}")
        space.rules = merged
    session.flush()
    return space


def set_statuses(
    session: Session,
    bp_id: int,
    wanted: list[dict[str, Any]],
    move: dict[int, int],
) -> list[TaskStatus]:
    """Replace the workflow. A status with tasks may go only with `move` (old id -> new id)."""
    if not wanted:
        raise HTTPException(422, "cần ít nhất một trạng thái")
    if not any(s["category"] == "done" for s in wanted) or not any(
        s["category"] == "todo" for s in wanted
    ):
        raise HTTPException(422, "cần ít nhất một trạng thái nhóm Cần làm và một nhóm Xong")
    names = [s["name"].strip() for s in wanted]
    if len(set(n.lower() for n in names)) != len(names) or any(not n for n in names):
        raise HTTPException(422, "tên trạng thái trống hoặc trùng")
    current = {s.id: s for s in statuses_of(session, bp_id)}
    keep_ids = {int(s["id"]) for s in wanted if s.get("id") is not None}
    if unknown := keep_ids - set(current):
        raise HTTPException(422, f"trạng thái không thuộc dự án: {sorted(unknown)}")
    gone = set(current) - keep_ids
    names_before = {i: st.name for i, st in current.items()}
    # Temporary names so a rename swap (A<->B) never hits the unique constraint.
    for s in current.values():
        s.name = f"__{s.id}"
    session.flush()
    out: list[TaskStatus] = []
    for i, item in enumerate(wanted):
        status = current.get(int(item["id"])) if item.get("id") is not None else None
        if status is None:
            status = TaskStatus(business_project_id=bp_id, name=item["name"].strip())
            session.add(status)
        status.name = item["name"].strip()
        status.category = item["category"]
        status.position = i
        out.append(status)
    session.flush()
    valid = {s.id for s in out}
    for item, status in zip(wanted, out, strict=True):
        allowed = item.get("transitions")
        status.transitions = (
            None if allowed is None else [int(x) for x in allowed if int(x) in valid]
        )
    for old in gone:
        used = session.scalar(select(func.count()).select_from(Task).where(Task.status_id == old))
        if used:
            target = move.get(old)
            if target is None or target not in valid:
                raise HTTPException(
                    409,
                    f"trạng thái {names_before[old]} còn {used} task: chọn nơi chuyển",
                )
            session.execute(
                update(Task)
                .where(Task.status_id == old)
                .values(status_id=target, status_changed_at=datetime.now(UTC))
            )
        session.delete(current[old])
    # A task's resolved time follows its status's category, whatever moved it.
    done_ids = [st.id for st in out if st.category == "done"]
    now = datetime.now(UTC)
    session.execute(
        update(Task)
        .where(
            Task.business_project_id == bp_id,
            Task.status_id.in_(done_ids),
            Task.resolved_at.is_(None),
        )
        .values(resolved_at=now)
    )
    session.execute(
        update(Task)
        .where(
            Task.business_project_id == bp_id,
            Task.status_id.in_(done_ids),
            Task.resolution.is_(None),
        )
        .values(resolution="done")
    )
    session.execute(
        update(Task)
        .where(
            Task.business_project_id == bp_id,
            Task.status_id.not_in(done_ids),
            Task.resolution.is_not(None),
        )
        .values(resolution=None)
    )
    session.execute(
        update(Task)
        .where(
            Task.business_project_id == bp_id,
            Task.status_id.not_in(done_ids),
            Task.resolved_at.is_not(None),
        )
        .values(resolved_at=None)
    )
    space = session.get(TaskSpace, bp_id)
    if space is not None:
        rules = dict(space.rules or {})
        for name in ("start", "review", "done"):
            if rules.get(name) is not None and rules[name] not in valid:
                rules[name] = move.get(rules[name])
        space.rules = rules
    session.flush()
    return out


# -- priorities (docs/specs/31 §18) ----------------------------------------------------------------

# key, name, level, colour: what a new task space starts with (the five fixed ones from before).
DEFAULT_PRIORITIES: tuple[tuple[str, str, str, str], ...] = (
    ("highest", "Cao nhất", "highest", "red"),
    ("high", "Cao", "high", "orange"),
    ("medium", "Trung bình", "medium", "amber"),
    ("low", "Thấp", "low", "blue"),
    ("lowest", "Thấp nhất", "lowest", "gray"),
)
DEFAULT_PRIORITY = "medium"
MAX_PRIORITIES = 12


def seed_priorities(session: Session, bp_id: int) -> None:
    for i, (key, name, level, color) in enumerate(DEFAULT_PRIORITIES):
        session.add(
            TaskPriority(
                business_project_id=bp_id,
                key=key,
                name=name,
                level=level,
                color=color,
                position=i,
                is_default=key == DEFAULT_PRIORITY,
            )
        )
    session.flush()


def priorities_of(session: Session, bp_id: int) -> list[TaskPriority]:
    return list(
        session.scalars(
            select(TaskPriority)
            .where(TaskPriority.business_project_id == bp_id)
            .order_by(TaskPriority.position, TaskPriority.id)
        )
    )


def default_priority(session: Session, bp_id: int) -> str:
    held = priorities_of(session, bp_id)
    chosen = next((x for x in held if x.is_default), None) or next(
        (x for x in held if x.key == DEFAULT_PRIORITY), None
    )
    if chosen is None and held:
        chosen = held[len(held) // 2]
    return chosen.key if chosen else DEFAULT_PRIORITY


def check_priority(session: Session, bp_id: int, key: str) -> None:
    found = session.scalars(
        select(TaskPriority.id).where(
            TaskPriority.business_project_id == bp_id, TaskPriority.key == key
        )
    ).first()
    if found is None:
        raise HTTPException(422, f"dự án không có mức ưu tiên {key}")


def _priority_key(name: str, taken: set[str]) -> str:
    """ "Khẩn cấp" -> "khan-cap": ASCII, stable after renames, unique in the dự án."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    ascii_name = folded.encode("ascii", "ignore").decode()
    base = (re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "p")[:24]
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    return key


def set_priorities(
    session: Session, bp_id: int, wanted: list[dict[str, Any]], move: dict[int, int]
) -> list[TaskPriority]:
    """Replace the dự án's priorities, in order. One with tasks may go only with `move` (old id
    -> new id); exactly one is the default (the first one marked, else the middle one)."""
    if not wanted:
        raise HTTPException(422, "cần ít nhất một mức ưu tiên")
    if len(wanted) > MAX_PRIORITIES:
        raise HTTPException(422, f"tối đa {MAX_PRIORITIES} mức ưu tiên")
    # The space row lock: creating a task takes it too, so none lands on a priority being removed.
    session.get(TaskSpace, bp_id, with_for_update=True)
    names = [str(x["name"]).strip() for x in wanted]
    if any(not n for n in names) or len({n.lower() for n in names}) != len(names):
        raise HTTPException(422, "tên mức ưu tiên trống hoặc trùng")
    current = {x.id: x for x in priorities_of(session, bp_id)}
    keep = {int(x["id"]) for x in wanted if x.get("id") is not None}
    if unknown := keep - set(current):
        raise HTTPException(422, f"mức ưu tiên không thuộc dự án: {sorted(unknown)}")
    # Everything is checked before anything is written.
    gone = set(current) - keep
    used_by: dict[int, int] = {}
    for old_id in gone:
        used = session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.business_project_id == bp_id, Task.priority == current[old_id].key)
        )
        if used:
            if move.get(old_id) not in keep:
                raise HTTPException(
                    409, f"mức ưu tiên {current[old_id].name} còn {used} task: chọn mức chuyển sang"
                )
            used_by[old_id] = int(used)
    taken = {x.key for x in current.values()}
    out: list[TaskPriority] = []
    for i, (item, name) in enumerate(zip(wanted, names, strict=True)):
        row = current.get(int(item["id"])) if item.get("id") is not None else None
        if row is None:
            key = _priority_key(name, taken)
            taken.add(key)
            row = TaskPriority(business_project_id=bp_id, key=key)
            session.add(row)
        row.name = name[:40]
        row.level = item["level"]
        row.color = item["color"]
        row.position = i
        row.is_default = False
        out.append(row)
    marked = next((r for r, item in zip(out, wanted, strict=True) if item.get("is_default")), None)
    (marked or out[len(out) // 2]).is_default = True
    session.flush()
    valid = {r.id: r for r in out}
    for old_id in gone:
        old = current[old_id]
        if old_id in used_by:
            target = valid[move[old_id]]
            session.execute(
                update(Task)
                .where(Task.business_project_id == bp_id, Task.priority == old.key)
                .values(priority=target.key)
            )
        session.delete(old)
    session.flush()
    return out


def priority_out(x: TaskPriority) -> dict[str, Any]:
    return {
        "id": x.id,
        "key": x.key,
        "name": x.name,
        "level": x.level,
        "color": x.color,
        "position": x.position,
        "is_default": x.is_default,
    }


def priority_meta(session: Session, bp_ids: Iterable[int]) -> dict[tuple[int, str], TaskPriority]:
    ids = set(bp_ids)
    if not ids:
        return {}
    return {
        (x.business_project_id, x.key): x
        for x in session.scalars(
            select(TaskPriority).where(TaskPriority.business_project_id.in_(ids))
        )
    }


def status_out(s: TaskStatus) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "category": s.category,
        "position": s.position,
        "transitions": s.transitions,
    }


def space_out(
    session: Session, p: Principal, bp: BusinessProject, space: TaskSpace | None
) -> dict[str, Any]:
    return {
        "business_project_id": bp.id,
        "name": bp.name,
        "enabled": space is not None,
        "key": space.key if space else None,
        "types": list(space.types) if space else list(TASK_TYPES),
        "rules": rules_of(space) if space else dict(DEFAULT_FLAGS),
        "board_mode": space.board_mode if space else "scrum",
        "statuses": [status_out(s) for s in statuses_of(session, bp.id)] if space else [],
        "priorities": [priority_out(x) for x in priorities_of(session, bp.id)] if space else [],
        "can_manage": can_manage_space(p, bp.id),
        "can_create": space is not None and can_create(p, bp.id),
        "can_sprint": space is not None and can_sprint(p, bp.id),
        "can_assign": space is not None and can_assign_new(p, bp.id),
        # tasks.attach: files and pasted images, also in the create form (§17).
        "can_attach": space is not None
        and can_view_space(p, bp.id)
        and (p.can("tasks.attach") or can_manage_space(p, bp.id)),
    }


def spaces(session: Session, p: Principal) -> list[dict[str, Any]]:
    visible = p.visible_projects()
    query = select(BusinessProject).order_by(BusinessProject.name)
    if visible is not None:
        query = query.where(BusinessProject.id.in_(visible))
    rows = list(session.scalars(query))
    by_id = {
        s.business_project_id: s
        for s in session.scalars(
            select(TaskSpace).where(TaskSpace.business_project_id.in_([b.id for b in rows]))
        )
    }
    return [
        space_out(session, p, bp, by_id.get(bp.id))
        for bp in rows
        if by_id.get(bp.id) is not None or can_manage_space(p, bp.id)
    ]


def people(session: Session, bp_id: int) -> list[User]:
    """Who can be given work in the dự án: people granted it, active."""
    granted = select(ProjectGrant.user_id).where(ProjectGrant.business_project_id == bp_id)
    return list(
        session.scalars(
            select(User)
            .where(User.id.in_(granted), User.active.is_not(False))
            .order_by(User.full_name, User.id)
        )
    )


# -- people as the API sends them -----------------------------------------------------------------


def person(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "full_name": user.full_name,
        "primary_email": user.primary_email,
        "avatar_url": avatar_url(user.id, user.avatar_file, user.updated_at),
    }


def users_by_id(session: Session, ids: Iterable[int | None]) -> dict[int, User]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {u.id: u for u in session.scalars(select(User).where(User.id.in_(wanted)))}


# -- history and notifications --------------------------------------------------------------------


def log(
    session: Session,
    task: Task,
    user_id: int | None,
    field: str,
    old: Any,
    new: Any,
    via: str = "web",
) -> None:
    def text(value: Any) -> str | None:
        if value is None or value == "" or value == []:
            return None
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)[:2000]

    session.add(
        TaskHistory(
            task_id=task.id, user_id=user_id, field=field, old=text(old), new=text(new), via=via
        )
    )


def watchers(session: Session, task: Task) -> set[int]:
    return set(session.scalars(select(TaskWatcher.user_id).where(TaskWatcher.task_id == task.id)))


def watch(session: Session, task: Task, user_id: int | None) -> None:
    if user_id is None:
        return
    if session.get(TaskWatcher, (task.id, user_id)) is None:
        session.add(TaskWatcher(task_id=task.id, user_id=user_id))
        session.flush()


def tell(
    session: Session,
    users: Iterable[int | None],
    task: Task,
    kind: str,
    title: str,
    body: str | None = None,
    actor_id: int | None = None,
    tone: str = "info",
) -> None:
    notify(
        session,
        users,
        kind=kind,
        title=title,
        body=body,
        link=link_of(task.key),
        tone=tone,
        actor_id=actor_id,
        ref=f"task:{task.id}",
    )


MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9._-]{2,64})")


def mentioned(session: Session, bp_id: int, text: str | None) -> set[int]:
    """People named as @<email local part> (tamtd for tamtd@…), among those granted the dự án."""
    names = {m.lower() for m in MENTION.findall(text or "")}
    if not names:
        return set()
    return {
        u.id
        for u in people(session, bp_id)
        if u.primary_email and u.primary_email.split("@", 1)[0].lower() in names
    }


# -- tasks -----------------------------------------------------------------------------------------


def _next_rank(session: Session, bp_id: int) -> float:
    top = session.scalar(select(func.max(Task.rank)).where(Task.business_project_id == bp_id))
    return float(top or 0) + RANK_STEP


def _status(session: Session, bp_id: int, status_id: int) -> TaskStatus:
    status = session.get(TaskStatus, status_id)
    if status is None or status.business_project_id != bp_id:
        raise HTTPException(422, "trạng thái không thuộc dự án")
    return status


def _parent(session: Session, bp_id: int, key: str | None, child_type: str) -> Task | None:
    if not key:
        return None
    parent = session.scalars(select(Task).where(Task.key == key.upper())).first()
    if parent is None or parent.business_project_id != bp_id:
        raise HTTPException(422, f"task cha {key} không thuộc dự án")
    if child_type == "subtask" and parent.type == "subtask":
        raise HTTPException(422, "sub-task không làm cha được")
    if child_type == "epic":
        raise HTTPException(422, "epic không có task cha")
    return parent


def _check_refs(
    session: Session, bp_id: int, data: dict[str, Any], actor_id: int | None = None
) -> None:
    if data.get("sprint_id") is not None:
        sprint = session.get(Sprint, data["sprint_id"])
        if sprint is None or sprint.business_project_id != bp_id or sprint.state == "closed":
            raise HTTPException(422, "sprint không hợp lệ")
    if data.get("version_id") is not None:
        version = session.get(TaskVersion, data["version_id"])
        if version is None or version.business_project_id != bp_id:
            raise HTTPException(422, "phiên bản không thuộc dự án")
    if data.get("assignee_id") is not None and data["assignee_id"] != actor_id:
        allowed = {u.id for u in people(session, bp_id)}
        if data["assignee_id"] not in allowed:
            raise HTTPException(422, "người được giao không thuộc dự án")


def create_task(
    session: Session,
    space: TaskSpace,
    data: dict[str, Any],
    actor_id: int | None,
    via: str = "web",
) -> Task:
    task_type = data.get("type") or "task"
    if task_type not in space.types:
        raise HTTPException(422, f"dự án không dùng loại {task_type}")
    title = (data.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "tiêu đề trống")
    bp_id = space.business_project_id
    _check_refs(session, bp_id, data, actor_id)
    parent = _parent(session, bp_id, data.get("parent_key"), task_type)
    if task_type == "subtask" and parent is None:
        raise HTTPException(422, "sub-task cần task cha")
    if task_type == "subtask" and parent is not None:
        # A sub-task is part of its parent's work: same sprint, same version (§19.1).
        data = {**data, "sprint_id": parent.sprint_id, "version_id": parent.version_id}
    priority = data.get("priority") or default_priority(session, bp_id)
    check_priority(session, bp_id, priority)
    statuses = statuses_of(session, bp_id)
    if data.get("status_id") is not None:
        status = _status(session, bp_id, data["status_id"])
    else:
        status = next((s for s in statuses if s.category == "todo"), statuses[0])
    # The row lock hands out numbers one at a time.
    locked = session.get(TaskSpace, bp_id, with_for_update=True, populate_existing=True)
    if locked is None:
        raise HTTPException(404, "dự án chưa bật Task")
    number = locked.next_number
    locked.next_number = number + 1
    task = Task(
        business_project_id=bp_id,
        number=number,
        key=f"{locked.key}-{number}",
        type=task_type,
        title=title[:300],
        description=data.get("description"),
        status_id=status.id,
        priority=priority,
        assignee_id=data.get("assignee_id"),
        reporter_id=actor_id,
        parent_id=parent.id if parent else None,
        labels=_labels(data.get("labels")),
        components=_labels(data.get("components")),
        story_points=data.get("story_points"),
        estimate_min=data.get("estimate_min"),
        due_date=data.get("due_date"),
        sprint_id=data.get("sprint_id"),
        version_id=data.get("version_id"),
        custom=data.get("custom") or {},
        rank=_next_rank(session, bp_id),
        resolved_at=datetime.now(UTC) if status.category == "done" else None,
        resolution=(data.get("resolution") or "done") if status.category == "done" else None,
        started_at=datetime.now(UTC) if status.category == "doing" else None,
        status_changed_at=datetime.now(UTC),
    )
    session.add(task)
    session.flush()
    log(session, task, actor_id, "created", None, task.key, via)
    watch(session, task, actor_id)
    if task.assignee_id is not None:
        watch(session, task, task.assignee_id)
        tell(
            session,
            [task.assignee_id],
            task,
            "task.assigned",
            f"{task.key} giao cho bạn: {task.title}",
            actor_id=actor_id,
        )
    if who := mentioned(session, bp_id, task.description):
        tell(session, who, task, "task.mentioned", f"Bạn được nhắc ở {task.key}", actor_id=actor_id)
    return task


def _labels(value: Any) -> list[str]:
    out: list[str] = []
    for item in value or []:
        text = str(item).strip()[:60]
        if text and text not in out:
            out.append(text)
    return out[:30]


def open_critical(session: Session, task: Task) -> int:
    return int(git_summaries(session, [task.id])[task.id]["open_critical"])


def set_status(
    session: Session,
    task: Task,
    status: TaskStatus,
    actor_id: int | None,
    via: str = "web",
    *,
    check_transition: bool = True,
    resolution: str | None = None,
) -> bool:
    """Move a task; False when the dự án's critical gate kept it out of Done. Entering a "done"
    status sets the resolution (default "done"); within "done" a given one replaces it."""
    if resolution is not None and resolution not in RESOLUTIONS:
        raise HTTPException(422, f"lý do đóng không hợp lệ: {resolution}")
    if status.id == task.status_id:
        if resolution is not None and status.category == "done":
            set_resolution(session, task, resolution, actor_id, via)
        return True
    current = session.get(TaskStatus, task.status_id)
    if (
        check_transition
        and current is not None
        and current.transitions is not None
        and status.id not in current.transitions
    ):
        raise HTTPException(409, f"không chuyển được từ {current.name} sang {status.name}")
    space = session.get(TaskSpace, task.business_project_id)
    if (
        status.category == "done"
        and space is not None
        and rules_of(space)["block_done_on_critical"]
    ):
        n = open_critical(session, task)
        if n:
            if via == "web" or via == "mcp":
                raise HTTPException(409, f"còn {n} finding critical mở trên code của task")
            log(session, task, None, "blocked", None, f"chưa chuyển Done: còn {n} critical", via)
            tell(
                session,
                [task.assignee_id],
                task,
                "task.status",
                f"{task.key} chưa chuyển Done: còn {n} critical mở",
                tone="warn",
            )
            return False
    old_name = current.name if current else None
    now = datetime.now(UTC)
    task.status_id = status.id
    task.status_changed_at = now
    if status.category == "doing" and task.started_at is None:
        task.started_at = now
    if status.category == "done":
        task.resolved_at = task.resolved_at or now
        new_resolution = resolution or task.resolution or "done"
        if new_resolution != task.resolution:
            if task.resolution is not None:
                log(
                    session,
                    task,
                    actor_id,
                    "resolution",
                    RESOLUTION_LABELS.get(task.resolution),
                    RESOLUTION_LABELS[new_resolution],
                    via,
                )
            task.resolution = new_resolution
    else:
        task.resolved_at = None
        task.resolution = None
    task.updated_at = now
    log(session, task, actor_id, "status", old_name, status.name, via)
    recipients = watchers(session, task) | {task.reporter_id or 0}
    if via in ("git", "smart_commit"):
        recipients.add(task.assignee_id or 0)
    tell(
        session,
        recipients - {0},
        task,
        "task.status",
        f"{task.key}: {old_name} → {status.name}",
        task.title,
        actor_id=actor_id,
        tone="good" if status.category == "done" else "info",
    )
    session.flush()
    return True


def status_by_word(session: Session, space: TaskSpace, word: str) -> TaskStatus | None:
    """A status named by a smart-commit / MCP word: the dự án's own name ("dang-lam"), else an
    alias of a rule's status (#done → its "done", #in-progress → "start"), docs/specs/31 §19.1."""
    from app.tasks import keys as tk

    wanted = tk.slug(word)
    statuses = statuses_of(session, space.business_project_id)
    found = next((s for s in statuses if tk.slug(s.name) == wanted), None)
    if found is None and wanted in tk.STATUS_ALIASES:
        rule = rules_of(space).get(tk.STATUS_ALIASES[wanted])
        found = next((s for s in statuses if s.id == rule), None)
    if found is None and wanted in ("todo", "to-do", "can-lam"):
        found = next((s for s in statuses if s.category == "todo"), None)
    return found


def set_resolution(
    session: Session, task: Task, resolution: str, actor_id: int | None, via: str = "web"
) -> None:
    """Change why a closed task was closed (only while it sits in a "done" status)."""
    if resolution not in RESOLUTIONS:
        raise HTTPException(422, f"lý do đóng không hợp lệ: {resolution}")
    status = session.get(TaskStatus, task.status_id)
    if status is None or status.category != "done":
        raise HTTPException(409, "chỉ task đã đóng (nhóm Xong) mới có lý do đóng")
    if task.resolution == resolution:
        return
    log(
        session,
        task,
        actor_id,
        "resolution",
        RESOLUTION_LABELS.get(task.resolution or ""),
        RESOLUTION_LABELS[resolution],
        via,
    )
    task.resolution = resolution
    task.updated_at = datetime.now(UTC)
    session.flush()


EDITABLE = (
    "title",
    "description",
    "type",
    "priority",
    "assignee_id",
    "labels",
    "components",
    "story_points",
    "estimate_min",
    "spent_min",
    "due_date",
    "sprint_id",
    "version_id",
    "custom",
)


def update_task(
    session: Session,
    task: Task,
    changes: dict[str, Any],
    actor_id: int | None,
    via: str = "web",
) -> None:
    bp_id = task.business_project_id
    space = session.get(TaskSpace, bp_id)
    if space is None:
        raise HTTPException(404, "dự án chưa bật Task")
    _check_refs(session, bp_id, changes, actor_id)
    if "type" in changes and changes["type"] not in space.types:
        raise HTTPException(422, f"dự án không dùng loại {changes['type']}")
    if task.type == "subtask" or changes.get("type") == "subtask":
        for field in ("sprint_id", "version_id"):
            if field in changes and changes[field] != getattr(task, field):
                parent_now = session.get(Task, task.parent_id) if task.parent_id else None
                if parent_now is None or changes[field] != getattr(parent_now, field):
                    raise HTTPException(
                        422, "sub-task theo sprint và phiên bản của task cha: đổi ở task cha"
                    )
    if changes.get("resolution") is not None and changes.get("status_id") is None:
        set_resolution(session, task, changes["resolution"], actor_id, via)
    if "priority" in changes:
        if not changes["priority"]:
            raise HTTPException(422, "ưu tiên trống")
        check_priority(session, bp_id, changes["priority"])
    if "title" in changes and not (changes["title"] or "").strip():
        raise HTTPException(422, "tiêu đề trống")
    if "parent_key" in changes:
        parent = _parent(session, bp_id, changes["parent_key"], changes.get("type", task.type))
        if parent is not None and (parent.id == task.id or _is_descendant(session, parent, task)):
            raise HTTPException(422, "task cha tạo vòng lặp")
        old_parent = session.get(Task, task.parent_id) if task.parent_id else None
        if (parent.id if parent else None) != task.parent_id:
            log(
                session,
                task,
                actor_id,
                "parent",
                old_parent.key if old_parent else None,
                parent.key if parent else None,
                via,
            )
            task.parent_id = parent.id if parent else None
            if task.type == "subtask" and parent is not None:
                changes = {
                    **changes,
                    "sprint_id": parent.sprint_id,
                    "version_id": parent.version_id,
                }
    for field in EDITABLE:
        if field not in changes:
            continue
        value = changes[field]
        if field in ("labels", "components"):
            value = _labels(value)
        elif field == "title":
            value = value.strip()[:300]
        elif field == "custom":
            value = {**(task.custom or {}), **(value or {})}
        old = getattr(task, field)
        if old == value:
            continue
        setattr(task, field, value)
        shown_old, shown_new = old, value
        if field == "assignee_id":
            users = users_by_id(session, [old, value])
            shown_old = users[old].full_name if old in users else None
            shown_new = users[value].full_name if value in users else None
            if value is not None:
                watch(session, task, value)
                tell(
                    session,
                    [value],
                    task,
                    "task.assigned",
                    f"{task.key} giao cho bạn: {task.title}",
                    actor_id=actor_id,
                )
        elif field == "description":
            shown_old, shown_new = None, "(đã sửa)"
            if who := mentioned(session, bp_id, value) - mentioned(session, bp_id, old):
                tell(
                    session,
                    who,
                    task,
                    "task.mentioned",
                    f"Bạn được nhắc ở {task.key}",
                    actor_id=actor_id,
                )
        elif field in ("sprint_id", "version_id"):
            shown_old, shown_new = _named(session, field, old), _named(session, field, value)
        elif field == "priority":
            meta = priority_meta(session, [bp_id])
            shown_old = meta[(bp_id, old)].name if (bp_id, old) in meta else old
            shown_new = meta[(bp_id, value)].name if (bp_id, value) in meta else value
        log(session, task, actor_id, field.removesuffix("_id"), shown_old, shown_new, via)
    if task.type != "subtask" and ({"sprint_id", "version_id"} & set(changes)):
        # Its sub-tasks move with it (as objects: the DPI projection sees each change).
        for child in session.scalars(
            select(Task).where(Task.parent_id == task.id, Task.type == "subtask")
        ):
            child.sprint_id, child.version_id = task.sprint_id, task.version_id
    if changes.get("status_id") is not None:
        set_status(
            session,
            task,
            _status(session, bp_id, changes["status_id"]),
            actor_id,
            via,
            resolution=changes.get("resolution"),
        )
    task.updated_at = datetime.now(UTC)
    session.flush()


def _named(session: Session, field: str, ident: int | None) -> str | None:
    if ident is None:
        return None
    row: Sprint | TaskVersion | None = (
        session.get(Sprint, ident) if field == "sprint_id" else session.get(TaskVersion, ident)
    )
    return row.name if row is not None else None


def _is_descendant(session: Session, node: Task, ancestor: Task) -> bool:
    seen = 0
    while node.parent_id is not None and seen < 50:
        if node.parent_id == ancestor.id:
            return True
        parent = session.get(Task, node.parent_id)
        if parent is None:
            return False
        node = parent
        seen += 1
    return False


def move(
    session: Session,
    task: Task,
    *,
    before: Task | None,
    after: Task | None,
) -> None:
    """Place `task` between two others (board / backlog order): `before` is the card it goes
    in front of, `after` the one it follows."""
    lo = after.rank if after else None
    hi = before.rank if before else None
    if lo is not None and hi is not None:
        if hi - lo < 1e-6 and after is not None and before is not None:  # worn out
            _respread(session, task.business_project_id)
            session.refresh(after)
            session.refresh(before)
            lo, hi = after.rank, before.rank
        task.rank = (lo + hi) / 2
    elif hi is not None:
        task.rank = hi - RANK_STEP
    elif lo is not None:
        task.rank = lo + RANK_STEP
    session.flush()


def _respread(session: Session, bp_id: int) -> None:
    for i, t in enumerate(
        session.scalars(
            select(Task).where(Task.business_project_id == bp_id).order_by(Task.rank, Task.id)
        )
    ):
        t.rank = (i + 1) * RANK_STEP
    session.flush()


# -- what the API sends -------------------------------------------------------------------------


def git_summaries(session: Session, task_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Per task: linked commits / MRs and the critical / high findings still open on the latest
    review of each (docs/specs/31 §7)."""
    out: dict[int, dict[str, Any]] = {
        i: {"commits": 0, "mrs": 0, "open_critical": 0, "open_high": 0} for i in task_ids
    }
    if not task_ids:
        return out
    links = list(
        session.scalars(
            select(TaskGitLink).where(
                TaskGitLink.task_id.in_(task_ids), TaskGitLink.kind.in_(("commit", "mr"))
            )
        )
    )
    review_of = latest_reviews(session, links)
    open_counts = _open_findings(session, set(review_of.values()))
    for link in links:
        row = out[link.task_id]
        row["commits" if link.kind == "commit" else "mrs"] += 1
        review_id = review_of.get(link.id)
        if review_id is not None:
            counts = open_counts.get(review_id, {})
            row["open_critical"] += counts.get("critical", 0)
            row["open_high"] += counts.get("high", 0)
    return out


def latest_reviews(session: Session, links: list[TaskGitLink]) -> dict[int, int]:
    """link id -> its latest finished review (a commit's, or an MR's)."""
    out: dict[int, int] = {}
    shas = {link.sha for link in links if link.kind == "commit" and link.sha}
    by_sha: dict[str, int] = {}
    if shas:
        for review_id, sha in session.execute(
            select(Review.id, Review.commit_sha)
            .where(Review.commit_sha.in_(shas), Review.status == "done")
            .order_by(Review.finished_at.asc().nulls_first(), Review.id)
        ).all():
            if sha:
                by_sha[sha] = int(review_id)
    mr_keys = {(link.project_id, link.mr_iid) for link in links if link.kind == "mr"}
    by_mr: dict[tuple[int | None, int | None], int] = {}
    if mr_keys:
        mrs = {
            (p, int(i)): int(mid)
            for mid, p, i in session.execute(
                select(MergeRequest.id, MergeRequest.project_id, MergeRequest.iid).where(
                    tuple_(MergeRequest.project_id, MergeRequest.iid).in_(list(mr_keys))
                )
            ).all()
        }
        if mrs:
            latest: dict[int, int] = {}
            for review_id, mr_id in session.execute(
                select(Review.id, Review.mr_id)
                .where(Review.mr_id.in_(list(mrs.values())), Review.status == "done")
                .order_by(Review.finished_at.asc().nulls_first(), Review.id)
            ).all():
                if mr_id is not None:
                    latest[int(mr_id)] = int(review_id)
            by_mr = {k: latest[v] for k, v in mrs.items() if v in latest}
    for link in links:
        if link.kind == "commit" and link.sha in by_sha:
            out[link.id] = by_sha[link.sha]
        elif link.kind == "mr" and (link.project_id, link.mr_iid) in by_mr:
            out[link.id] = by_mr[(link.project_id, link.mr_iid)]
    return out


def _open_findings(session: Session, review_ids: set[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = defaultdict(dict)
    if not review_ids:
        return out
    for review_id, severity, n in session.execute(
        select(ReviewFinding.review_id, ReviewFinding.severity, func.count())
        .where(
            ReviewFinding.review_id.in_(review_ids),
            ReviewFinding.status == "open",
            ReviewFinding.severity.in_(SEVERE),
        )
        .group_by(ReviewFinding.review_id, ReviewFinding.severity)
    ).all():
        if review_id is not None:
            out[int(review_id)][str(severity)] = int(n)
    return out


def _level_of(key: str) -> str:
    return key if key in PRIORITIES else "medium"


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def rows(session: Session, tasks: list[Task]) -> list[dict[str, Any]]:
    """TaskRow for each task (docs/specs/31 §8), in the order given."""
    if not tasks:
        return []
    ids = [t.id for t in tasks]
    statuses = {
        s.id: s
        for s in session.scalars(
            select(TaskStatus).where(TaskStatus.id.in_({t.status_id for t in tasks}))
        )
    }
    users = users_by_id(session, [u for t in tasks for u in (t.assignee_id, t.reporter_id)])
    parent_ids = {t.parent_id for t in tasks if t.parent_id}
    parents = (
        {
            int(i): str(k)
            for i, k in session.execute(
                select(Task.id, Task.key).where(Task.id.in_(parent_ids))
            ).all()
        }
        if parent_ids
        else {}
    )
    subtasks: dict[int, dict[str, int]] = defaultdict(lambda: {"done": 0, "total": 0})
    for parent_id, category, n in session.execute(
        select(Task.parent_id, TaskStatus.category, func.count())
        .join(TaskStatus, TaskStatus.id == Task.status_id)
        .where(Task.parent_id.in_(ids))
        .group_by(Task.parent_id, TaskStatus.category)
    ).all():
        if parent_id is None:
            continue
        subtasks[int(parent_id)]["total"] += int(n)
        if category == "done":
            subtasks[int(parent_id)]["done"] += int(n)
    git = git_summaries(session, ids)
    prio = priority_meta(session, {t.business_project_id for t in tasks})
    out = []
    for t in tasks:
        status = statuses.get(t.status_id)
        level = prio.get((t.business_project_id, t.priority))
        out.append(
            {
                "id": t.id,
                "key": t.key,
                "space": t.business_project_id,
                "type": t.type,
                "title": t.title,
                "status": (
                    {"id": status.id, "name": status.name, "category": status.category}
                    if status
                    else None
                ),
                "priority": t.priority,
                "priority_name": level.name if level else t.priority,
                "priority_level": level.level if level else _level_of(t.priority),
                "priority_color": level.color if level else "gray",
                "assignee": person(users.get(t.assignee_id)) if t.assignee_id else None,
                "reporter": person(users.get(t.reporter_id)) if t.reporter_id else None,
                "labels": list(t.labels or []),
                "story_points": _num(t.story_points),
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "sprint_id": t.sprint_id,
                "version_id": t.version_id,
                "parent_key": parents.get(t.parent_id) if t.parent_id else None,
                "subtasks": dict(subtasks.get(t.id, {"done": 0, "total": 0})),
                "rank": t.rank,
                "git": git[t.id],
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
                "resolution": t.resolution,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "status_changed_at": (
                    t.status_changed_at.isoformat() if t.status_changed_at else None
                ),
            }
        )
    return out


def git_detail(session: Session, task: Task) -> dict[str, Any]:
    """The Code section of a task (docs/specs/31 §7)."""
    links = list(
        session.scalars(
            select(TaskGitLink)
            .where(TaskGitLink.task_id == task.id)
            .order_by(TaskGitLink.created_at, TaskGitLink.id)
        )
    )
    repo_ids = {link.project_id for link in links if link.project_id}
    repos = (
        {p.id: p for p in session.scalars(select(Project).where(Project.id.in_(repo_ids)))}
        if repo_ids
        else {}
    )
    connections = {
        c.id: c
        for c in session.scalars(
            select(Connection).where(
                Connection.id.in_({p.connection_id for p in repos.values() if p.connection_id})
            )
        )
    }

    def base(project: Project | None) -> str | None:
        if project is None or project.connection_id not in connections:
            return None
        root = connections[project.connection_id].base_url.rstrip("/")
        return f"{root}/{project.path_with_namespace}"

    review_of = latest_reviews(session, links)
    reviews = (
        {
            r.id: r
            for r in session.scalars(select(Review).where(Review.id.in_(set(review_of.values()))))
        }
        if review_of
        else {}
    )
    open_counts = _open_findings(session, set(reviews))

    def review_out(link: TaskGitLink) -> dict[str, Any] | None:
        review = reviews.get(review_of.get(link.id, -1))
        if review is None:
            return None
        counts = open_counts.get(review.id, {})
        return {
            "id": review.id,
            "score": _num(review.score),
            "verdict": review.verdict,
            "open_critical": counts.get("critical", 0),
            "open_high": counts.get("high", 0),
        }

    mr_keys = [(link.project_id, link.mr_iid) for link in links if link.kind == "mr"]
    mr_rows = (
        {
            (mr.project_id, mr.iid): mr
            for mr in session.scalars(
                select(MergeRequest).where(
                    tuple_(MergeRequest.project_id, MergeRequest.iid).in_(mr_keys)
                )
            )
        }
        if mr_keys
        else {}
    )
    heads = {mr.head_sha for mr in mr_rows.values() if mr.head_sha}
    pipelines: dict[tuple[int | None, str], str | None] = {}
    if heads:
        for project_id, sha, status in session.execute(
            select(Pipeline.project_id, Pipeline.sha, Pipeline.status)
            .where(Pipeline.sha.in_(heads))
            .order_by(Pipeline.created_at_src.asc().nulls_first(), Pipeline.id)
        ).all():
            if sha:
                pipelines[(project_id, sha)] = status

    branches, commits, mrs = [], [], []
    scores: list[float] = []
    summary = {"commits": 0, "mrs": 0, "open_critical": 0, "open_high": 0, "pipelines_failed": 0}
    deploy_shas: set[str] = set()
    for link in links:
        project = repos.get(link.project_id or -1)
        url_base = base(project)
        common = {
            "id": link.id,
            "repo": project.path_with_namespace if project else None,
            "project_id": link.project_id,
            "relation": link.relation,
            "manual": link.manual,
            "created_at": link.created_at.isoformat() if link.created_at else None,
        }
        if link.kind == "branch":
            branches.append(
                {
                    **common,
                    "branch": link.branch,
                    "web_url": f"{url_base}/-/tree/{link.branch}" if url_base else None,
                }
            )
            continue
        review = review_out(link)
        if review:
            summary["open_critical"] += review["open_critical"]
            summary["open_high"] += review["open_high"]
            if review["score"] is not None:
                scores.append(review["score"])
        if link.kind == "commit":
            summary["commits"] += 1
            deploy_shas.add(link.sha or "")
            commits.append(
                {
                    **common,
                    "sha": link.sha,
                    "title": link.title,
                    "web_url": f"{url_base}/-/commit/{link.sha}" if url_base else None,
                    "review": review,
                }
            )
        else:
            summary["mrs"] += 1
            mr = mr_rows.get((link.project_id, link.mr_iid or 0))
            pipeline = pipelines.get((link.project_id, mr.head_sha)) if mr and mr.head_sha else None
            if pipeline == "failed":
                summary["pipelines_failed"] += 1
            if mr is not None:
                deploy_shas.update(s for s in (mr.merge_commit_sha, mr.squash_commit_sha) if s)
            mrs.append(
                {
                    **common,
                    "iid": link.mr_iid,
                    "mr_iid": link.mr_iid,
                    "title": (mr.title if mr else None) or link.title,
                    "state": (mr.state if mr else None) or link.state,
                    "web_url": f"{url_base}/-/merge_requests/{link.mr_iid}" if url_base else None,
                    "pipeline": {"status": pipeline} if pipeline else None,
                    "review": review,
                }
            )
    deployed = _deployed(session, repos, deploy_shas - {""})
    return {
        "branches": branches,
        "commits": commits,
        "mrs": mrs,
        "summary": {
            **summary,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "deployed": deployed,
        },
    }


def _deployed(session: Session, repos: dict[int, Project], shas: set[str]) -> dict[str, Any] | None:
    """The first successful production deployment of one of these commits."""
    if not shas or not repos:
        return None
    from app.insights.dora import is_production

    for dep in session.scalars(
        select(Deployment)
        .where(
            Deployment.sha.in_(shas),
            Deployment.project_id.in_(list(repos)),
            Deployment.status == "success",
        )
        .order_by(Deployment.finished_at_src.asc().nulls_last())
    ):
        repo = repos.get(dep.project_id)
        if is_production(
            dep.environment, dep.environment_tier, repo.production_environment if repo else None
        ):
            when = dep.finished_at_src
            return {"environment": dep.environment, "at": when.isoformat() if when else None}
    return None


def as_day(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
