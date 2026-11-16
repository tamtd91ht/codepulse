"""Nhân rộng Task (docs/specs/31 §20.4): saved space templates, applying a template to a dự án
already on Task, the members step of the enable wizard and the adoption table.

Included before the tasks router so /tasks/templates and /tasks/adoption are never task keys."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal, load_principal
from app.config import Settings, get_settings
from app.models import (
    Account,
    BusinessProject,
    Commit,
    Project,
    ProjectGrant,
    Task,
    TaskComment,
    TaskSpace,
    TaskStatus,
    User,
)
from app.models.tasks import TaskSpaceTemplate
from app.tasks import service as ts
from app.tasks import templates as tpl

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

SUGGEST_DAYS = 90
MAX_SUGGESTIONS = 50


def viewer(principal: CurrentPrincipal) -> Principal:
    if not principal.can("tasks.view"):
        raise HTTPException(403, "thiếu quyền xem task")
    return principal


Viewer = Annotated[Principal, Depends(viewer)]


class SaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)


class ApplyIn(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    # The enable wizard's own picks win over the template's (as with PUT /tasks/spaces, §19.6).
    types: list[str] | None = None
    board_mode: str | None = None


def _bp_or_404(session: Session, principal: Principal, bp_id: int) -> BusinessProject:
    bp = session.get(BusinessProject, bp_id)
    if bp is None or not principal.can_view_project(bp_id):
        raise HTTPException(404, "dự án not found")
    return bp


# -- templates ------------------------------------------------------------------------------------


@router.get("/tasks/templates")
def list_templates(principal: Viewer, session: SessionDep) -> list[dict[str, Any]]:
    return tpl.listing(session, principal.user_id, principal.is_admin)


@router.post("/tasks/spaces/{bp_id}/save-template", status_code=201)
def save_template(
    bp_id: int, body: SaveIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    space = ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    row = tpl.save(session, space, body.name, body.description, principal.user_id)
    audit(
        session,
        principal.user_id,
        "task.template.save",
        f"business_project:{bp_id}",
        template=row.key,
    )
    session.commit()
    return tpl.summary(row.key, row.name, row.description, row.payload, row=row, can_delete=True)


@router.delete("/tasks/templates/{template_id}", status_code=204)
def delete_template(template_id: int, principal: Viewer, session: SessionDep) -> None:
    row = session.get(TaskSpaceTemplate, template_id)
    if row is None or row.builtin:
        raise HTTPException(404, "không thấy mẫu")
    if not principal.is_admin and row.created_by != principal.user_id:
        raise HTTPException(403, "chỉ người tạo mẫu hoặc admin xoá được")
    audit(
        session, principal.user_id, "task.template.delete", f"task_template:{row.id}", key=row.key
    )
    session.delete(row)
    session.commit()


@router.post("/tasks/spaces/{bp_id}/apply-template")
def apply_template(
    bp_id: int, body: ApplyIn, principal: Viewer, session: SessionDep
) -> dict[str, Any]:
    bp = _bp_or_404(session, principal, bp_id)
    space = ts.space_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    result = tpl.apply(session, space, body.key, types=body.types, board_mode=body.board_mode)
    audit(
        session,
        principal.user_id,
        "task.template.apply",
        f"business_project:{bp_id}",
        template=body.key,
        mode=result["mode"],
    )
    # Full mode runs only on a dự án with no task, so DPI has nothing to rebuild (§15).
    session.commit()
    return {**result, "space": ts.space_out(session, principal, bp, space)}


# -- members step of the enable wizard ------------------------------------------------------------


@router.get("/tasks/spaces/{bp_id}/members")
def space_members(bp_id: int, principal: Viewer, session: SessionDep) -> dict[str, Any]:
    """Who has access to the dự án (project grants), and who committed to its repos in the last
    90 days without access: the people a manager may want to grant `view` before Task starts.
    Granting itself goes through PUT /business-projects/{id}/grants (docs/specs/21 §10)."""
    _bp_or_404(session, principal, bp_id)
    ts.require_manage(principal, bp_id)
    members = [
        {
            "user_id": user.id,
            "full_name": user.full_name,
            "email": user.primary_email,
            "role": grant.role,
            "admin_role": grant.admin_role,
        }
        for grant, user in session.execute(
            select(ProjectGrant, User)
            .join(User, User.id == ProjectGrant.user_id)
            .where(ProjectGrant.business_project_id == bp_id, User.active.is_not(False))
            .order_by(User.full_name, User.id)
        ).tuples()
    ]
    granted = select(ProjectGrant.user_id).where(ProjectGrant.business_project_id == bp_id)
    since = datetime.now(UTC) - timedelta(days=SUGGEST_DAYS)
    rows = session.execute(
        select(User, func.count(Commit.sha), func.max(Commit.authored_at))
        .join(Account, Account.user_id == User.id)
        .join(Commit, Commit.author_account_id == Account.id)
        .join(Project, Project.id == Commit.project_id)
        .where(
            Project.business_project_id == bp_id,
            Commit.authored_at >= since,
            User.active.is_not(False),
            User.id.not_in(granted),
        )
        .group_by(User.id)
        .order_by(func.count(Commit.sha).desc(), User.id)
        .limit(MAX_SUGGESTIONS)
    ).tuples()
    suggestions = [
        {
            "user_id": user.id,
            "full_name": user.full_name,
            "email": user.primary_email,
            "commits": int(n),
            "last_commit_at": last.isoformat() if last else None,
        }
        for user, n, last in rows
    ]
    return {
        "members": members,
        "suggestions": suggestions,
        "can_grant": principal.can_staff_project(bp_id),
    }


# -- adoption -------------------------------------------------------------------------------------


@router.get("/tasks/adoption")
def adoption(
    principal: CurrentPrincipal, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """Tình trạng bật Task (§20.4): one row per dự án the person can see. Admins (and holders of
    admin.projects) see every dự án, Task on or not; others need tasks.manage and see the dự án
    they can view that have Task on."""
    full = principal.is_admin or principal.can("admin.projects")
    if not full and not principal.can("tasks.manage"):
        raise HTTPException(403, "cần quyền quản lý Task hoặc quản trị dự án")
    query = select(BusinessProject).order_by(BusinessProject.name)
    if not full:
        visible = principal.visible_projects()
        if visible is not None:
            query = query.where(BusinessProject.id.in_(visible))
    projects = list(session.scalars(query))
    ids = [b.id for b in projects]
    spaces = {
        s.business_project_id: s
        for s in session.scalars(select(TaskSpace).where(TaskSpace.business_project_id.in_(ids)))
    }
    if not full:
        projects = [b for b in projects if b.id in spaces]
        ids = [b.id for b in projects]
    counts: dict[int, tuple[int, int, datetime | None]] = {}
    for bp_id, total, open_, last in session.execute(
        select(
            Task.business_project_id,
            func.count(Task.id),
            func.count(Task.id).filter(TaskStatus.category != "done"),
            func.max(Task.updated_at),
        )
        .join(TaskStatus, TaskStatus.id == Task.status_id)
        .where(Task.business_project_id.in_(ids))
        .group_by(Task.business_project_id)
    ).tuples():
        counts[int(bp_id)] = (int(total), int(open_), last)
    commented: dict[int, datetime | None] = dict(
        list(
            session.execute(
                select(Task.business_project_id, func.max(TaskComment.created_at))
                .join(Task, Task.id == TaskComment.task_id)
                .where(Task.business_project_id.in_(ids))
                .group_by(Task.business_project_id)
            ).tuples()
        )
    )
    viewers = _task_viewers(session, settings, ids)
    rows = []
    for bp in projects:
        space = spaces.get(bp.id)
        total, open_, last_task = counts.get(bp.id, (0, 0, None))
        last_comment = commented.get(bp.id)
        stamps = [x for x in (last_task, last_comment) if x is not None]
        activity = max(stamps) if stamps else None
        rows.append(
            {
                "business_project_id": bp.id,
                "name": bp.name,
                "enabled": space is not None,
                "key": space.key if space else None,
                "template_key": space.template_key if space else None,
                "template_name": tpl.name_of(session, space.template_key) if space else None,
                "board_mode": space.board_mode if space else None,
                "tasks": total,
                "open": open_,
                "last_activity": activity.isoformat() if activity else None,
                "viewers": viewers.get(bp.id, 0),
                "can_manage": ts.can_manage_space(principal, bp.id),
            }
        )
    return {"rows": rows, "all_projects": full}


def _task_viewers(session: Session, settings: Settings, bp_ids: list[int]) -> dict[int, int]:
    """Active people granted each dự án (or managing every dự án) who hold tasks.view. Admins
    by ADMIN_EMAILS see everything without a grant and are not counted unless granted."""
    if not bp_ids:
        return {}
    grants = session.execute(
        select(ProjectGrant.business_project_id, ProjectGrant.user_id)
        .join(User, User.id == ProjectGrant.user_id)
        .where(ProjectGrant.business_project_id.in_(bp_ids), User.active.is_not(False))
    ).tuples()
    people: dict[int, set[int]] = {bp: set() for bp in bp_ids}
    for bp, uid in grants:
        people[int(bp)].add(int(uid))
    everywhere = set(
        session.scalars(select(User.id).where(User.manage_all.is_(True), User.active.is_not(False)))
    )
    holds: dict[int, bool] = {}
    out: dict[int, int] = {}
    for bp, users in people.items():
        n = 0
        for uid in users | everywhere:
            if uid not in holds:
                try:
                    holds[uid] = load_principal(session, uid, settings).can("tasks.view")
                except HTTPException:
                    holds[uid] = False
            n += holds[uid]
        out[bp] = n
    return out
