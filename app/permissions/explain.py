"""Why one may or may not do something (docs/specs/31 §21.5, "Quyền của tôi"): the meaning of a
held scope, and for one permission over one task or one dự án a plain Vietnamese answer.

The answer is taken from the very checks the endpoints use (`app/tasks/service.py`,
`app/testcases`), so it never says "allowed" where the action is refused; only the wording
retraces their steps.
"""

from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.models import BusinessProject, ProjectGrant, Task, User
from app.permissions.catalog import BY_KEY, FLAG, PROJECT_TEAM_KEYS, Permission
from app.tasks import service as tasks
from app.testcases import approval
from app.testcases import service as tc

SCOPE_WORDS = {"own": "own (của mình)", "team": "team", "project": "project (dự án)", "all": "all"}

# The task rights checked on one task (tasks.<right>), docs/specs/31 §4.1.
TASK_RIGHT_KEYS = {f"tasks.{r}": r for r in tasks.TASK_RIGHTS}


@dataclass
class Check:
    key: str
    label: str
    scope: str | None
    allowed: bool | None  # None: depends on the task (a task right asked of a whole dự án)
    reason: str
    target: str | None = None
    lines: list[str] = field(default_factory=list)


def _team_word(perm: Permission) -> str:
    return "lead hoặc manage" if perm.managed_team else "lead"


def scope_meaning(key: str, scope: str) -> str:
    """What holding `key` at `scope` reaches, in a sentence."""
    perm = BY_KEY.get(key)
    if perm is None:
        return ""
    if scope == FLAG:
        return "có hoặc không: bạn có quyền này ở mọi dự án bạn xem được"
    team = _team_word(perm)
    if key in TASK_RIGHT_KEYS:
        mine = "task bạn tạo" if key == "tasks.delete" else "task bạn tạo hoặc được giao"
        theirs = "tạo" if key == "tasks.delete" else "tạo hoặc được giao"
        return {
            "own": f"chỉ {mine}",
            "team": f"{mine}, và task do người trong team bạn {team} {theirs}",
            "project": f"mọi task ở dự án bạn có quyền manage, và task của team bạn {team}",
            "all": "mọi task ở mọi dự án bạn xem được",
        }[scope]
    if key in PROJECT_TEAM_KEYS:
        return {
            "team": f"dự án có người trong team bạn {team} được cấp quyền "
            "(trong dự án bạn xem được)",
            "project": "dự án bạn có quyền manage (thêm dự án của team nếu một bộ khác "
            "cho mức team)",
            "all": "mọi dự án bạn xem được",
        }[scope]
    if key == "tests.run":
        return {
            "team": f"test giao cho bạn hoặc người trong team bạn {team}",
            "project": "mọi test ở dự án bạn có quyền manage",
            "all": "mọi test ở dự án bạn xem được",
        }[scope]
    return {
        "own": "việc của chính bạn",
        "team": f"việc của bạn và của người trong team bạn {team}",
        "project": "việc của mọi người ở dự án bạn có quyền manage",
        "all": "việc của mọi người ở dự án bạn xem được",
    }[scope]


def _name(session: Session, user_id: int | None, me: int) -> str:
    if user_id is None:
        return "chưa ai"
    if user_id == me:
        return "bạn"
    user = session.get(User, user_id)
    return (user.full_name or user.primary_email or f"#{user_id}") if user else f"#{user_id}"


def _project_name(session: Session, bp_id: int) -> str:
    bp = session.get(BusinessProject, bp_id)
    return bp.name if bp is not None else f"#{bp_id}"


def _held_line(key: str, perm: Permission, scope: str | None, sources: list[str]) -> str:
    if scope is None:
        return (
            f"Bạn không có quyền {key} ({perm.label}): không bộ quyền nào của bạn có quyền này. "
            "Hỏi admin (hoặc Lead / Manager dự án) để được gán bộ quyền có nó."
        )
    src = f" (từ bộ {', '.join(sources)})" if sources else ""
    if scope == FLAG:
        return f"Bạn có quyền {key}{src}."
    return f"Bạn có {key} ở mức {SCOPE_WORDS[scope]}{src}: {scope_meaning(key, scope)}."


def _team_holders(session: Session, p: Principal, key: str, bp_id: int) -> list[str]:
    """The people of one's team(s) holding a grant on the dự án (why "team" reaches it)."""
    people = p.lead_members | p.managed_members if BY_KEY[key].managed_team else p.lead_members
    people = people - {p.user_id}
    if not people:
        return []
    rows = session.scalars(
        select(User)
        .join(ProjectGrant, ProjectGrant.user_id == User.id)
        .where(
            ProjectGrant.business_project_id == bp_id,
            ProjectGrant.user_id.in_(sorted(people)),
            User.active.is_not(False),
        )
        .order_by(User.full_name)
    )
    return [u.full_name or u.primary_email or f"#{u.id}" for u in rows]


def _project_reason(session: Session, p: Principal, key: str, bp_id: int, allowed: bool) -> str:
    """Why a dự án-wide permission does or does not reach dự án `bp_id`."""
    name = _project_name(session, bp_id)
    scope = p.scope(key)
    if key.startswith("tasks.") and not p.can("tasks.view"):
        return "Bạn thiếu tasks.view nên không vào được Task của dự án nào."
    if key.startswith("tasks.") and key != "tasks.manage" and tasks.can_manage_space(p, bp_id):
        return f"Bạn giữ tasks.manage ở dự án {name}: gồm mọi quyền Task ở đó."
    if key.startswith("tests.") and not p.can("tests.view"):
        return "Bạn thiếu tests.view nên không vào được Testcase của dự án nào."
    if scope == "all":
        return f"Mức all áp cho mọi dự án bạn xem được, có {name}."
    manages = p.can_manage_project(bp_id)
    holders = _team_holders(session, p, key, bp_id) if key in PROJECT_TEAM_KEYS else []
    if allowed:
        if scope == "project" and manages:
            return f"Bạn có quyền manage ở dự án {name}."
        if holders:
            return (
                f"Dự án {name} có {', '.join(holders)} (người trong team bạn "
                f"{_team_word(BY_KEY[key])}) được cấp quyền."
            )
        return f"Bạn được {key} ở dự án {name}."
    if scope == "project":
        tail = (
            f" và không ai trong team bạn {_team_word(BY_KEY[key])} được cấp quyền ở đó"
            if key in p.team_granted
            else ""
        )
        return f"Mức project chỉ áp cho dự án bạn có quyền manage; ở {name} bạn chỉ xem{tail}."
    if scope == "team":
        if key not in PROJECT_TEAM_KEYS:
            return f"Mức team của {key} tính theo từng việc, không cho cả dự án {name}."
        return (
            f"Mức team chỉ áp cho dự án có người trong team bạn {_team_word(BY_KEY[key])} "
            f"được cấp quyền; dự án {name} không có ai như vậy."
        )
    return f"Bạn không có {key} ở dự án {name}."


def _task_reason(session: Session, p: Principal, right: str, task: Task, allowed: bool) -> str:
    key = f"tasks.{right}"
    bp_id = task.business_project_id
    name = _project_name(session, bp_id)
    reporter = _name(session, task.reporter_id, p.user_id)
    assignee = _name(session, task.assignee_id, p.user_id)
    who = f"Task {task.key} do {reporter} tạo, giao cho {assignee}."
    scope = p.scope(key)
    if tasks.can_manage_space(p, bp_id):
        return f"{who} Bạn giữ tasks.manage ở dự án {name}: gồm mọi quyền Task ở đó."
    if scope is None:
        return who
    if scope in ("all", FLAG):
        return f"{who} Mức {scope} áp cho mọi task của dự án bạn xem được."
    if scope == "project" and p.can_manage_project(bp_id):
        return f"{who} Bạn có quyền manage ở dự án {name}."
    team = _team_word(BY_KEY[key])
    people = p.team_people(key) - {p.user_id}
    involved = [task.reporter_id] if right == "delete" else [task.reporter_id, task.assignee_id]
    if allowed:
        mates = [u for u in involved if u is not None and u in people]
        if mates and scope in ("team", "project"):
            names = ", ".join(sorted({_name(session, u, p.user_id) for u in mates}))
            return f"{who} {names} là người trong team bạn {team}."
        return f"{who} Đây là task của bạn."
    if scope == "own":
        mine = "bạn tạo" if right == "delete" else "bạn tạo hoặc được giao"
        return f"{who} Mức own chỉ gồm task {mine}."
    if scope == "team":
        return f"{who} Người tạo / được giao không thuộc team bạn {team}."
    if right in ("delete", "moderate") and p.user_id in involved:
        return f"{who} {key} ở mức {scope} không gồm task của chính bạn."
    return (
        f"{who} Bạn không có quyền manage ở dự án {name} và task không thuộc người trong team "
        f"bạn {team}."
    )


def _project_allowed(p: Principal, key: str, bp_id: int) -> bool:
    if key == "tasks.manage":
        return tasks.can_manage_space(p, bp_id)
    if key == "tasks.sprint":
        return tasks.can_sprint(p, bp_id)
    if key == "tasks.view":
        return tasks.can_view_space(p, bp_id)
    if key == "tasks.create":
        return tasks.can_create(p, bp_id)
    if key == "tests.view":
        return tc.may_view(p, bp_id)
    if key == "tests.edit":
        return tc.may_edit(p, bp_id)
    if key == "tests.manage":
        return tc.may_manage(p, bp_id)
    if key == "tests.run":
        return tc.may_run(p, bp_id)
    if key == "tests.delete":
        return approval.may_delete(p, bp_id)
    if key == "tests.approve":
        return approval.may_approve(p, bp_id)
    return p.covers_project(key, bp_id) if key in PROJECT_TEAM_KEYS else p.can(key)


def explain(
    session: Session,
    p: Principal,
    key: str,
    sources: list[str],
    *,
    task_key: str | None = None,
    project_id: int | None = None,
) -> Check:
    """May `p` use permission `key` (on task `task_key`, or in dự án `project_id`), and why."""
    perm = BY_KEY.get(key)
    if perm is None:
        raise HTTPException(422, f"không có quyền {key!r} trong danh mục")
    scope = p.scope(key)
    head = (
        "Bạn là admin: có mọi quyền ở mọi dự án."
        if p.is_admin
        else _held_line(key, perm, scope, sources)
    )
    if task_key:
        task = tasks.task_or_404(session, p, task_key)  # one cannot see it: 404, as everywhere
        target = f"task {task.key}"
        right = TASK_RIGHT_KEYS.get(key)
        if right is not None:
            allowed: bool | None = tasks.may(p, right, task)
            why = _task_reason(session, p, right, task, bool(allowed))
        else:
            allowed = _project_allowed(p, key, task.business_project_id)
            why = _project_reason(session, p, key, task.business_project_id, allowed)
    elif project_id is not None:
        if not p.can_view_project(project_id):
            raise HTTPException(404, "không thấy dự án")
        target = f"dự án {_project_name(session, project_id)}"
        if key in TASK_RIGHT_KEYS:
            if not tasks.can_view_space(p, project_id) or scope is None:
                allowed, why = False, ""
            elif tasks.can_manage_space(p, project_id) or scope in ("all", FLAG):
                allowed, why = True, _project_reason(session, p, key, project_id, True)
            elif scope == "project" and p.can_manage_project(project_id):
                allowed, why = True, "Bạn có quyền manage ở dự án này: mọi task ở đó."
            else:
                allowed = None
                why = "Còn tuỳ task: chọn một task để biết chắc (nhập mã task)."
        else:
            allowed = _project_allowed(p, key, project_id)
            why = _project_reason(session, p, key, project_id, allowed)
    else:
        target = None
        if p.is_admin or scope in ("all", FLAG) or scope is None:
            allowed = p.can(key)
            why = ""
        else:
            # A narrower scope depends on the task / dự án: never "được" where every one is refused.
            allowed = None
            why = "Còn tuỳ task hoặc dự án: chọn một task hoặc một dự án để biết chắc."
    if p.is_admin:
        why = ""
    lines = [line for line in (head, why) if line]
    verdict = (
        "Kết luận: được."
        if allowed
        else "Kết luận: không được."
        if allowed is False
        else "Kết luận: tuỳ task."
        if target
        else "Kết luận: tuỳ task / dự án."
    )
    lines.append(verdict)
    return Check(
        key=key,
        label=perm.label,
        scope=scope,
        allowed=allowed,
        reason=" ".join(lines),
        target=target,
        lines=lines,
    )
