"""Phân quyền trong một dự án (docs/specs/21 §13): the dự án's own permission sets and the rights
ticked for one person there, handed out by its Manager dự án (`project.permissions`), an admin or
a holder of admin.permissions.

Rules, on top of those of every grant (docs/specs/21 §7, §8.8, §9):
- a dự án's set holds only rights over work done in a dự án (catalog `PROJECT_ASSIGNABLE`), each
  over the whole of that dự án, and counts only there;
- one hands out (and takes back) only what one holds over the whole of that dự án
  (`Principal.whole_project`), never `project.permissions` itself, never to oneself;
- only to people who have a grant on the dự án; never to an admin, a Quản lý tổng, someone who
  holds an `admin.*` right, or another Manager dự án of it (those stay the admins' to change);
- sensitive rights are confirmed; every change is audited with the dự án.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal, load_principal
from app.config import Settings, get_settings
from app.models import (
    BusinessProject,
    PermissionSet,
    PermissionSetItem,
    ProjectGrant,
    User,
    UserPermissionSet,
)
from app.permissions.catalog import BY_KEY, CATALOG, PROJECT_ASSIGNABLE, PROJECT_SCOPE

router = APIRouter(tags=["project-permissions"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# -- bodies and answers -------------------------------------------------------------------------


class PermissionOut(BaseModel):
    key: str
    label: str
    sensitive: bool
    # The caller holds it over the whole dự án, so may hand it out (or take it back) here.
    grantable: bool


class GroupOut(BaseModel):
    name: str
    permissions: list[PermissionOut]


class CatalogOut(BaseModel):
    groups: list[GroupOut]


class PersonOut(BaseModel):
    id: int
    full_name: str | None
    email: str | None


class SetOut(BaseModel):
    id: int
    name: str
    description: str | None
    permissions: list[str]
    members: list[PersonOut]


class SetCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    # Absent with from_set_id: the source's rights (a copy of another set of this dự án).
    permissions: list[str] | None = None
    from_set_id: int | None = None
    confirm_sensitive: bool = False


class SetPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = None  # replaces the whole list
    confirm_sensitive: bool = False


class SetUsersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[int]
    confirm_sensitive: bool = False


class PersonRightsIn(BaseModel):
    """One person's rights in the dự án: the dự án's sets given to them, and rights ticked for
    them alone (kept in their own set of this dự án)."""

    model_config = ConfigDict(extra="forbid")

    set_ids: list[int]
    permissions: list[str]
    confirm_sensitive: bool = False


class PersonRightsOut(BaseModel):
    user: PersonOut
    role: str  # the grant: "view" | "manage"
    admin_role: str | None  # "lead" | "manager" of the dự án
    set_ids: list[int]
    permissions: list[str]  # ticked for them alone
    effective: list[str]  # everything the dự án's sets give them there
    # None: the caller may change their rights here; else why not.
    locked: str | None


# -- helpers ------------------------------------------------------------------------------------


def _person(user: User) -> PersonOut:
    return PersonOut(id=user.id, full_name=user.full_name, email=user.primary_email)


def _project(session: Session, principal: Principal, bp_id: int) -> BusinessProject:
    bp = session.get(BusinessProject, bp_id)
    if bp is None or not principal.can_view_project(bp_id):
        raise HTTPException(404, "không có dự án này")
    if not principal.can_permit_project(bp_id):
        raise HTTPException(403, "chỉ Manager của dự án (hoặc admin) phân quyền trong dự án")
    return bp


def grantable(principal: Principal, bp_id: int) -> set[str]:
    """What `principal` may hand out in dự án `bp_id`: the rights a dự án's set may hold that
    they hold over the whole of it."""
    return {k for k in PROJECT_ASSIGNABLE if principal.whole_project(k, bp_id)}


def _keys(raw: list[str]) -> set[str]:
    keys = set(raw)
    if len(keys) != len(raw):
        raise HTTPException(422, "một quyền bị chọn hai lần")
    if bad := sorted(k for k in keys if k not in PROJECT_ASSIGNABLE):
        raise HTTPException(422, f"quyền không cấp được trong phạm vi một dự án: {', '.join(bad)}")
    return keys


def _within(principal: Principal, bp_id: int, keys: set[str]) -> None:
    """Hand out, or take back, only what one holds over the whole dự án."""
    if principal.is_admin:
        return
    if beyond := sorted(keys - grantable(principal, bp_id)):
        raise HTTPException(
            403, f"không cấp / thu được quyền mình không có ở dự án này: {', '.join(beyond)}"
        )


def _confirm(keys: set[str], confirmed: bool) -> None:
    sensitive = sorted(k for k in keys if BY_KEY[k].sensitive)
    if sensitive and not confirmed:
        raise HTTPException(
            422,
            {
                "message": "Cấp quyền nhạy cảm cần xác nhận (confirm_sensitive)",
                "sensitive": sensitive,
            },
        )


def _items(session: Session, set_id: int) -> set[str]:
    return set(
        session.scalars(
            select(PermissionSetItem.permission).where(PermissionSetItem.set_id == set_id)
        )
    )


def _write_items(session: Session, set_id: int, keys: set[str]) -> None:
    session.execute(delete(PermissionSetItem).where(PermissionSetItem.set_id == set_id))
    session.add_all(
        PermissionSetItem(set_id=set_id, permission=k, scope=PROJECT_SCOPE) for k in sorted(keys)
    )


def _given(session: Session, set_id: int) -> set[int]:
    return set(
        session.scalars(select(UserPermissionSet.user_id).where(UserPermissionSet.set_id == set_id))
    )


def _get_set(session: Session, bp_id: int, set_id: int) -> PermissionSet:
    row = session.get(PermissionSet, set_id)
    if row is None or row.business_project_id != bp_id or row.personal_user_id is not None:
        raise HTTPException(404, "không có bộ quyền này trong dự án")
    return row


def _name_free(session: Session, bp_id: int, name: str, except_id: int | None = None) -> None:
    query = select(PermissionSet.id).where(
        PermissionSet.business_project_id == bp_id,
        func.lower(PermissionSet.name) == name.lower(),
    )
    if except_id is not None:
        query = query.where(PermissionSet.id != except_id)
    if session.scalar(query) is not None:
        raise HTTPException(409, f'dự án đã có bộ quyền tên "{name}"')


def _is_admin(user: User, settings: Settings) -> bool:
    admins = {e.lower() for e in settings.admin_emails}
    return bool(user.primary_email and user.primary_email.lower() in admins)


def _lock(
    session: Session,
    settings: Settings,
    principal: Principal,
    bp_id: int,
    user: User,
    grant: ProjectGrant | None,
) -> str | None:
    """Why `principal` may not change `user`'s rights in dự án `bp_id`; None: they may."""
    if _is_admin(user, settings):
        return "Admin đã có mọi quyền."
    if user.active is False:
        return "Người này đã ngừng hoạt động."
    if grant is None:
        return "Người này chưa có quyền vào dự án: thêm ở tab Người & quyền trước."
    if principal.is_admin:
        return None
    if user.id == principal.user_id:
        return "Không tự đặt quyền cho mình."
    if user.manage_all:
        return "Quản lý tổng: chỉ admin đổi quyền."
    if grant.admin_role == "manager":
        return "Manager dự án: chỉ admin đổi quyền."
    if any(k.startswith("admin.") for k in load_principal(session, user.id, settings).perms):
        return "Người giữ quyền quản trị: chỉ admin đổi quyền."
    return None


def _target(
    session: Session, settings: Settings, principal: Principal, bp_id: int, user_id: int
) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "không có người này")
    grant = session.get(ProjectGrant, (bp_id, user.id))
    if (why := _lock(session, settings, principal, bp_id, user, grant)) is not None:
        raise HTTPException(409 if _is_admin(user, settings) else 403, why)
    return user


def _holders_unlocked(
    session: Session, settings: Settings, principal: Principal, bp_id: int, set_id: int
) -> None:
    """Changing or deleting a set changes the rights of everyone holding it: each of them must
    be one the caller may change (an admin may have given it to a Manager dự án, say)."""
    if principal.is_admin:
        return
    for uid in sorted(_given(session, set_id)):
        user = session.get(User, uid)
        if user is None:
            continue
        grant = session.get(ProjectGrant, (bp_id, uid))
        if grant is None:
            continue  # holds nothing here while without a grant
        if (why := _lock(session, settings, principal, bp_id, user, grant)) is not None:
            name = user.full_name or user.primary_email or f"#{uid}"
            raise HTTPException(403, f"bộ quyền đang gán cho {name}: {why}")


def _commit(session: Session) -> None:
    """A race lost to a concurrent save (same name, same first tick): 409, not 500."""
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            409, "xung đột với một thay đổi khác cùng lúc; tải lại rồi thử lại"
        ) from exc


def _set_out(session: Session, row: PermissionSet) -> SetOut:
    people = {
        u.id: u
        for u in session.scalars(
            select(User).where(User.id.in_(sorted(_given(session, row.id)))).order_by(User.id)
        )
    }
    return SetOut(
        id=row.id,
        name=row.name,
        description=row.description,
        permissions=sorted(_items(session, row.id)),
        members=[_person(u) for u in people.values()],
    )


def _personal(session: Session, bp_id: int, user_id: int) -> PermissionSet | None:
    return session.scalars(
        select(PermissionSet)
        .where(
            PermissionSet.personal_user_id == user_id,
            PermissionSet.business_project_id == bp_id,
        )
        .with_for_update()
    ).first()


# -- endpoints ----------------------------------------------------------------------------------


@router.get("/business-projects/{bp_id}/permissions/catalog")
def project_catalog(bp_id: int, principal: CurrentPrincipal, session: SessionDep) -> CatalogOut:
    """The rights a set of this dự án may hold, grouped as Admin > Phân quyền shows them, and
    whether the caller may hand each out here."""
    _project(session, principal, bp_id)
    mine = grantable(principal, bp_id)
    groups: dict[str, list[PermissionOut]] = {}
    for p in CATALOG:
        if p.key in PROJECT_ASSIGNABLE:
            groups.setdefault(p.group, []).append(
                PermissionOut(
                    key=p.key,
                    label=p.label,
                    sensitive=p.sensitive,
                    grantable=principal.is_admin or p.key in mine,
                )
            )
    return CatalogOut(groups=[GroupOut(name=n, permissions=ps) for n, ps in groups.items()])


@router.get("/business-projects/{bp_id}/permission-sets")
def list_sets(bp_id: int, principal: CurrentPrincipal, session: SessionDep) -> list[SetOut]:
    _project(session, principal, bp_id)
    rows = session.scalars(
        select(PermissionSet)
        .where(PermissionSet.business_project_id == bp_id, PermissionSet.personal_user_id.is_(None))
        .order_by(func.lower(PermissionSet.name))
    )
    return [_set_out(session, row) for row in rows]


@router.post("/business-projects/{bp_id}/permission-sets", status_code=201)
def create_set(
    bp_id: int, body: SetCreateIn, principal: CurrentPrincipal, session: SessionDep
) -> SetOut:
    """A set of this dự án. With `from_set_id`: a copy of another set of it (nobody holds the
    copy yet, so only rights added to it need confirming)."""
    _project(session, principal, bp_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "tên bộ quyền trống")
    _name_free(session, bp_id, name)
    description = body.description
    source: set[str] = set()
    if body.from_set_id is not None:
        src = _get_set(session, bp_id, body.from_set_id)
        source = _items(session, src.id)
        if description is None:
            description = src.description
    keys = _keys(body.permissions) if body.permissions is not None else set(source)
    _within(principal, bp_id, keys)
    _confirm(keys - source, body.confirm_sensitive)
    row = PermissionSet(
        name=name,
        description=(description or "").strip() or None,
        builtin=False,
        auto_role=None,
        business_project_id=bp_id,
    )
    session.add(row)
    session.flush()
    _write_items(session, row.id, keys)
    audit(
        session,
        principal.user_id,
        "permission_set.create",
        f"permission_set:{row.id}",
        business_project_id=bp_id,
        name=name,
        permissions=sorted(keys),
        from_set_id=body.from_set_id,
    )
    _commit(session)
    return _set_out(session, row)


@router.patch("/business-projects/{bp_id}/permission-sets/{set_id}")
def update_set(
    bp_id: int,
    set_id: int,
    body: SetPatchIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> SetOut:
    _project(session, principal, bp_id)
    row = _get_set(session, bp_id, set_id)
    changes: dict[str, object] = {}
    if body.name is not None and body.name.strip() != row.name:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "tên bộ quyền trống")
        _name_free(session, bp_id, name, row.id)
        changes["renamed"] = {"from": row.name, "to": name}
        row.name = name
    if "description" in body.model_fields_set and body.description != row.description:
        changes["description"] = True
        row.description = (body.description or "").strip() or None
    if body.permissions is not None:
        old, new = _items(session, row.id), _keys(body.permissions)
        added, removed = new - old, old - new
        if added and not principal.is_admin and principal.user_id in _given(session, row.id):
            raise HTTPException(403, "không tự cấp thêm quyền cho mình")
        _within(principal, bp_id, added | removed)
        if added or removed:
            _holders_unlocked(session, settings, principal, bp_id, row.id)
        _confirm(added, body.confirm_sensitive)
        if added or removed:
            _write_items(session, row.id, new)
            changes["added"] = sorted(added)
            changes["removed"] = sorted(removed)
    if changes:
        audit(
            session,
            principal.user_id,
            "permission_set.update",
            f"permission_set:{row.id}",
            business_project_id=bp_id,
            name=row.name,
            **changes,
        )
    _commit(session)
    return _set_out(session, row)


@router.delete("/business-projects/{bp_id}/permission-sets/{set_id}", status_code=204)
def delete_set(
    bp_id: int,
    set_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Its people lose what it gave them: taking rights back, so only within what one holds."""
    _project(session, principal, bp_id)
    row = _get_set(session, bp_id, set_id)
    keys = _items(session, row.id)
    _within(principal, bp_id, keys)
    _holders_unlocked(session, settings, principal, bp_id, row.id)
    audit(
        session,
        principal.user_id,
        "permission_set.delete",
        f"permission_set:{row.id}",
        business_project_id=bp_id,
        name=row.name,
        permissions=sorted(keys),
        users=sorted(_given(session, row.id)),
    )
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.put("/business-projects/{bp_id}/permission-sets/{set_id}/users")
def set_users(
    bp_id: int,
    set_id: int,
    body: SetUsersIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> SetOut:
    """Replace who holds this set of the dự án; everyone added or removed must be one whose
    rights the caller may change here."""
    _project(session, principal, bp_id)
    row = _get_set(session, bp_id, set_id)
    wanted, current = set(body.user_ids), _given(session, row.id)
    added, removed = wanted - current, current - wanted
    for uid in sorted(added | removed):
        _target(session, settings, principal, bp_id, uid)
    keys = _items(session, row.id)
    if added or removed:
        _within(principal, bp_id, keys)
    if added:
        _confirm(keys, body.confirm_sensitive)
    if removed:
        session.execute(
            delete(UserPermissionSet).where(
                UserPermissionSet.set_id == row.id, UserPermissionSet.user_id.in_(removed)
            )
        )
    session.add_all(
        UserPermissionSet(user_id=uid, set_id=row.id, granted_by=principal.user_id)
        for uid in sorted(added)
    )
    if added or removed:
        audit(
            session,
            principal.user_id,
            "permission_set.assign",
            f"permission_set:{row.id}",
            business_project_id=bp_id,
            name=row.name,
            added=sorted(added),
            removed=sorted(removed),
        )
    session.commit()
    return _set_out(session, row)


def _rights(
    session: Session,
    settings: Settings,
    principal: Principal,
    bp_id: int,
    user: User,
    grant: ProjectGrant | None,
) -> PersonRightsOut:
    held = {
        s.id: s
        for s in session.scalars(
            select(PermissionSet)
            .join(UserPermissionSet, UserPermissionSet.set_id == PermissionSet.id)
            .where(
                UserPermissionSet.user_id == user.id,
                PermissionSet.business_project_id == bp_id,
            )
        )
    }
    own = next((s for s in held.values() if s.personal_user_id is not None), None)
    effective: set[str] = set()
    for s in held.values():
        effective |= _items(session, s.id) & PROJECT_ASSIGNABLE
    return PersonRightsOut(
        user=_person(user),
        role=grant.role if grant else "",
        admin_role=grant.admin_role if grant else None,
        set_ids=sorted(i for i, s in held.items() if s.personal_user_id is None),
        permissions=sorted(_items(session, own.id)) if own else [],
        effective=sorted(effective),
        locked=_lock(session, settings, principal, bp_id, user, grant),
    )


@router.get("/business-projects/{bp_id}/permissions/people")
def list_people(
    bp_id: int, principal: CurrentPrincipal, session: SessionDep, settings: SettingsDep
) -> list[PersonRightsOut]:
    """Everyone with a grant on the dự án, with the rights its sets give them there."""
    _project(session, principal, bp_id)
    rows = session.execute(
        select(User, ProjectGrant)
        .join(ProjectGrant, ProjectGrant.user_id == User.id)
        .where(ProjectGrant.business_project_id == bp_id)
        .order_by(User.full_name, User.id)
    ).tuples()
    return [_rights(session, settings, principal, bp_id, u, g) for u, g in rows]


@router.put("/business-projects/{bp_id}/users/{user_id}/permissions")
def set_person_rights(
    bp_id: int,
    user_id: int,
    body: PersonRightsIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> PersonRightsOut:
    """One person's rights in the dự án in one save: the dự án's sets they hold, and the rights
    ticked for them alone (their own set of the dự án: made on the first tick, gone when
    emptied). Every right given or taken, through a set or a tick, is within what the caller
    holds there."""
    _project(session, principal, bp_id)
    user = _target(session, settings, principal, bp_id, user_id)
    # One save per person and dự án at a time: the first tick makes their own set.
    session.execute(
        select(ProjectGrant.user_id)
        .where(ProjectGrant.business_project_id == bp_id, ProjectGrant.user_id == user.id)
        .with_for_update()
    )
    wanted_sets = set(body.set_ids)
    rows = {
        r.id: r
        for r in session.scalars(select(PermissionSet).where(PermissionSet.id.in_(wanted_sets)))
    }
    if bad := sorted(
        i
        for i in wanted_sets
        if i not in rows
        or rows[i].business_project_id != bp_id
        or rows[i].personal_user_id is not None
    ):
        raise HTTPException(422, f"không có bộ quyền này trong dự án: {', '.join(map(str, bad))}")
    current_sets = set(
        session.scalars(
            select(UserPermissionSet.set_id)
            .join(PermissionSet, PermissionSet.id == UserPermissionSet.set_id)
            .where(
                UserPermissionSet.user_id == user.id,
                PermissionSet.business_project_id == bp_id,
                PermissionSet.personal_user_id.is_(None),
            )
        )
    )
    added_sets, removed_sets = wanted_sets - current_sets, current_sets - wanted_sets
    touched: set[str] = set()
    newly: set[str] = set()
    for sid in added_sets | removed_sets:
        keys = _items(session, sid)
        touched |= keys
        if sid in added_sets:
            newly |= keys
    wanted = _keys(body.permissions)
    own = _personal(session, bp_id, user.id)
    before = _items(session, own.id) if own else set()
    touched |= wanted ^ before
    newly |= wanted - before
    _within(principal, bp_id, touched)
    _confirm(newly, body.confirm_sensitive)
    if removed_sets:
        session.execute(
            delete(UserPermissionSet).where(
                UserPermissionSet.user_id == user.id, UserPermissionSet.set_id.in_(removed_sets)
            )
        )
    session.add_all(
        UserPermissionSet(user_id=user.id, set_id=sid, granted_by=principal.user_id)
        for sid in sorted(added_sets)
    )
    if own is None and wanted:
        own = PermissionSet(
            # A name no set made by hand takes (the person's own set is never listed).
            name=f"~ Quyền riêng của người #{user.id} ~",
            description=f"Quyền tick riêng cho {user.full_name or user.primary_email or user.id}",
            builtin=False,
            personal_user_id=user.id,
            business_project_id=bp_id,
        )
        session.add(own)
        session.flush()
        session.add(UserPermissionSet(user_id=user.id, set_id=own.id, granted_by=principal.user_id))
    if own is not None:
        if wanted:
            _write_items(session, own.id, wanted)
        else:
            session.delete(own)  # nothing ticked: no empty set left behind
    if added_sets or removed_sets or wanted != before:
        audit(
            session,
            principal.user_id,
            "permission_set.personal",
            f"user:{user.id}",
            business_project_id=bp_id,
            added_sets=sorted(added_sets),
            removed_sets=sorted(removed_sets),
            before=sorted(before),
            after=sorted(wanted),
        )
    _commit(session)
    grant = session.get(ProjectGrant, (bp_id, user.id))
    return _rights(session, settings, principal, bp_id, user, grant)
