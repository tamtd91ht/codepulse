"""Admin > Phân quyền (docs/specs/21 phase C): the permission catalog, the permission sets, who
holds them, and one person's effective permissions with the set each comes from.

Every endpoint needs `admin.permissions` (by default: admins only). Rules (docs/specs/21 §7):
- a built-in set follows its role: its items and description may change, never its role, its
  name or its existence, and nobody is given it by hand;
- granting a sensitive permission needs `confirm_sensitive`;
- a holder of `admin.permissions` who is not an admin grants nothing they do not hold
  themselves, never `admin.permissions`, and never anything to themselves;
- no request makes anyone an admin: that stays ADMIN_EMAILS, and the bodies take no `builtin`
  or `auto_role`.
"""

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import Principal, load_principal, require
from app.config import Settings, get_settings
from app.models import (
    PermissionSet,
    PermissionSetItem,
    ProjectGrant,
    TeamMember,
    User,
    UserPermissionSet,
)
from app.permissions.catalog import (
    AUTO_ROLES,
    BY_KEY,
    CATALOG,
    RANK,
    SCOPES,
    everything,
    merge,
    valid_item,
)

router = APIRouter(tags=["admin"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
PermissionsAdmin = Annotated[Principal, require("admin.permissions")]

SCOPE_LABELS: dict[str, str] = {
    "own": "Của mình",
    "team": "Team",
    "project": "Dự án",
    "all": "Tất cả",
}
ROLE_LABELS: dict[str, str] = {
    "member": "Mọi người",
    "lead": "Lead",
    "manager": "Manager",
    "project_manager": "Quản lý dự án",
    "manage_all": "Quản lý tổng",
}
META = "admin.permissions"  # who may hand out permissions: only admins grant it


# -- bodies and answers -------------------------------------------------------------------------


class ItemIO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission: str
    scope: str  # own|team|project|all, or "flag"


class SetCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    # Absent with from_set_id: the source's items (a clone).
    items: list[ItemIO] | None = None
    from_set_id: int | None = None
    confirm_sensitive: bool = False


class SetPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    items: list[ItemIO] | None = None  # replaces the whole list
    confirm_sensitive: bool = False


class SetUsersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[int]
    confirm_sensitive: bool = False


class PermissionOut(BaseModel):
    key: str
    label: str
    scopes: list[str]
    flag: bool
    sensitive: bool


class GroupOut(BaseModel):
    name: str
    permissions: list[PermissionOut]


class LabelOut(BaseModel):
    key: str
    label: str


class CatalogOut(BaseModel):
    groups: list[GroupOut]
    scopes: list[LabelOut]
    roles: list[LabelOut]


class PersonOut(BaseModel):
    id: int
    full_name: str | None
    email: str | None


class SetOut(BaseModel):
    id: int
    name: str
    description: str | None
    builtin: bool
    auto_role: str | None
    items: list[ItemIO]
    member_count: int
    # The people given a custom set (a built-in one follows its role: empty).
    members: list[PersonOut]


class SourceOut(BaseModel):
    set_id: int | None  # None: admin
    name: str
    scope: str


class EffectiveOut(BaseModel):
    key: str
    scope: str
    sources: list[SourceOut]  # widest first


class HeldSetOut(BaseModel):
    id: int
    name: str
    auto_role: str | None
    via: str  # "role" | "given"


class UserPermissionsOut(BaseModel):
    user: PersonOut
    is_admin: bool
    active: bool
    roles: list[str]
    sets: list[HeldSetOut]
    permissions: list[EffectiveOut]


# -- helpers ------------------------------------------------------------------------------------


def _admin_emails(settings: Settings) -> set[str]:
    return {e.lower() for e in settings.admin_emails}


def _is_admin(user: User, settings: Settings) -> bool:
    return bool(user.primary_email and user.primary_email.lower() in _admin_emails(settings))


def _person(user: User) -> PersonOut:
    return PersonOut(id=user.id, full_name=user.full_name, email=user.primary_email)


def _role_holders(session: Session, settings: Settings, today: date) -> dict[str, set[int]]:
    """Who holds each built-in set through their role (as load_principal decides it), admins
    left out: they hold everything without any set."""
    admins = _admin_emails(settings)
    users = session.execute(select(User.id, User.primary_email, User.active, User.manage_all))
    active: set[int] = set()
    manage_all: set[int] = set()
    for uid, email, is_active, all_projects in users:
        if is_active is False or (email and email.lower() in admins):
            continue
        active.add(uid)
        if all_projects:
            manage_all.add(uid)
    lead: set[int] = set()
    manager: set[int] = set()
    for m in session.scalars(select(TeamMember).where(TeamMember.joined_at <= today)):
        if m.left_at is not None and m.left_at <= today:
            continue
        role = (m.role or "").lower()
        if role == "lead":
            lead.add(m.user_id)
        elif role == "manager":
            manager.add(m.user_id)
    pm = set(session.scalars(select(ProjectGrant.user_id).where(ProjectGrant.role == "manage")))
    return {
        "member": active,
        "lead": lead & active,
        "manager": manager & active,
        "project_manager": (pm & active) - manage_all,
        "manage_all": manage_all,
    }


def _auto_roles(principal: Principal) -> list[str]:
    """The built-in sets a person holds through their role (mirrors load_principal)."""
    roles = ["member"]
    if principal.lead_teams:
        roles.append("lead")
    if principal.managed_teams:
        roles.append("manager")
    if not principal.manages_all and "manage" in principal.project_roles.values():
        roles.append("project_manager")
    if principal.manages_all:
        roles.append("manage_all")
    return roles


def _items(session: Session, set_id: int) -> dict[str, str]:
    return {
        str(k): str(v)
        for k, v in session.execute(
            select(PermissionSetItem.permission, PermissionSetItem.scope)
            .where(PermissionSetItem.set_id == set_id)
            .order_by(PermissionSetItem.permission)
        )
    }


def _given(session: Session, set_id: int) -> set[int]:
    return set(
        session.scalars(select(UserPermissionSet.user_id).where(UserPermissionSet.set_id == set_id))
    )


def _get_set(session: Session, set_id: int) -> PermissionSet:
    row = session.get(PermissionSet, set_id)
    if row is None:
        raise HTTPException(404, "permission set not found")
    return row


def _validate(items: list[ItemIO]) -> dict[str, str]:
    out: dict[str, str] = {}
    bad: list[str] = []
    for item in items:
        if item.permission in out:
            bad.append(f"{item.permission}: trùng")
        elif not valid_item(item.permission, item.scope):
            bad.append(f"{item.permission}={item.scope}")
        else:
            out[item.permission] = item.scope
    if bad:
        raise HTTPException(422, f"quyền hoặc phạm vi không hợp lệ: {', '.join(bad)}")
    return out


def _widened(old: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """What `new` grants beyond `old`: added permissions, or wider scopes."""
    return {k: v for k, v in new.items() if k not in old or RANK[v] > RANK[old[k]]}


def _confirm_sensitive(granted: dict[str, str], confirmed: bool) -> None:
    sensitive = sorted(k for k in granted if BY_KEY[k].sensitive)
    if sensitive and not confirmed:
        raise HTTPException(
            422,
            {
                "message": "Cấp quyền nhạy cảm cần xác nhận (confirm_sensitive)",
                "sensitive": sensitive,
            },
        )


def _within_own(principal: Principal, granted: dict[str, str]) -> None:
    """A non-admin hands out only what they hold, at most as wide, and never the right to hand
    out rights."""
    if principal.is_admin:
        return
    if META in granted:
        raise HTTPException(403, "chỉ admin cấp được quyền phân quyền")
    beyond = sorted(
        k
        for k, v in granted.items()
        if (held := principal.perms.get(k)) is None or RANK[v] > RANK[held]
    )
    if beyond:
        raise HTTPException(403, f"không cấp được quyền mình không có: {', '.join(beyond)}")


def _holds(principal: Principal, row: PermissionSet, session: Session) -> bool:
    if row.auto_role is not None:
        return row.auto_role in _auto_roles(principal)
    return principal.user_id in _given(session, row.id)


def _set_out(
    session: Session, row: PermissionSet, holders: dict[str, set[int]], people: dict[int, User]
) -> SetOut:
    items = _items(session, row.id)
    if row.auto_role is not None:
        count, members = len(holders.get(row.auto_role, set())), []
    else:
        given = sorted(uid for uid in _given(session, row.id) if uid in people)
        members = [_person(people[uid]) for uid in given]
        count = sum(1 for uid in given if people[uid].active is not False)
    return SetOut(
        id=row.id,
        name=row.name,
        description=row.description,
        builtin=row.builtin,
        auto_role=row.auto_role,
        items=[ItemIO(permission=k, scope=v) for k, v in items.items()],
        member_count=count,
        members=members,
    )


def _one_out(session: Session, settings: Settings, row: PermissionSet) -> SetOut:
    people = {u.id: u for u in session.scalars(select(User))}
    holders = _role_holders(session, settings, datetime.now(UTC).date())
    return _set_out(session, row, holders, people)


def _write_items(session: Session, set_id: int, items: dict[str, str]) -> None:
    session.execute(delete(PermissionSetItem).where(PermissionSetItem.set_id == set_id))
    session.add_all(
        PermissionSetItem(set_id=set_id, permission=k, scope=v) for k, v in items.items()
    )


def _name_free(session: Session, name: str, except_id: int | None = None) -> None:
    query = select(PermissionSet.id).where(func.lower(PermissionSet.name) == name.lower())
    if except_id is not None:
        query = query.where(PermissionSet.id != except_id)
    if session.scalar(query) is not None:
        raise HTTPException(409, f'đã có bộ quyền tên "{name}"')


# -- endpoints ----------------------------------------------------------------------------------


@router.get("/admin/permissions/catalog")
def permission_catalog(principal: PermissionsAdmin) -> CatalogOut:
    """Every permission, grouped by feature as the matrix shows them, and the scope labels."""
    groups: dict[str, list[PermissionOut]] = {}
    for p in CATALOG:
        groups.setdefault(p.group, []).append(
            PermissionOut(
                key=p.key,
                label=p.label,
                scopes=list(p.scopes),
                flag=p.is_flag,
                sensitive=p.sensitive,
            )
        )
    return CatalogOut(
        groups=[GroupOut(name=name, permissions=perms) for name, perms in groups.items()],
        scopes=[LabelOut(key=s, label=SCOPE_LABELS[s]) for s in SCOPES],
        roles=[LabelOut(key=r, label=ROLE_LABELS[r]) for r in AUTO_ROLES],
    )


@router.get("/admin/permission-sets")
def list_permission_sets(
    principal: PermissionsAdmin, session: SessionDep, settings: SettingsDep
) -> list[SetOut]:
    """Built-in sets first (in role order), then the custom ones by name."""
    people = {u.id: u for u in session.scalars(select(User))}
    holders = _role_holders(session, settings, datetime.now(UTC).date())
    order: dict[str, int] = {r: i for i, r in enumerate(AUTO_ROLES)}
    rows = sorted(
        session.scalars(select(PermissionSet)),
        key=lambda s: (not s.builtin, order.get(s.auto_role or "", 99), s.name.lower()),
    )
    return [_set_out(session, row, holders, people) for row in rows]


@router.post("/admin/permission-sets", status_code=201)
def create_permission_set(
    body: SetCreateIn, principal: PermissionsAdmin, session: SessionDep, settings: SettingsDep
) -> SetOut:
    """A custom set, given by hand. With `from_set_id`: a copy of that set (its items unless
    `items` is given), which grants nobody anything yet."""
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "tên bộ quyền trống")
    _name_free(session, name)
    description = body.description
    if body.from_set_id is not None:
        source = _get_set(session, body.from_set_id)
        source_items = _items(session, source.id)
        items = (
            _validate(body.items) if body.items is not None else merge(list(source_items.items()))
        )
        if description is None:
            description = source.description
        # Copied items were confirmed when they were granted; only additions need it again.
        new = _widened(source_items, items)
    else:
        items = _validate(body.items or [])
        new = items
    _within_own(principal, items)
    _confirm_sensitive(new, body.confirm_sensitive)
    row = PermissionSet(name=name, description=description, builtin=False, auto_role=None)
    session.add(row)
    session.flush()
    _write_items(session, row.id, items)
    audit(
        session,
        principal.user_id,
        "permission_set.create",
        f"permission_set:{row.id}",
        name=name,
        items=items,
        from_set_id=body.from_set_id,
    )
    session.commit()
    return _one_out(session, settings, row)


@router.patch("/admin/permission-sets/{set_id}")
def update_permission_set(
    set_id: int,
    body: SetPatchIn,
    principal: PermissionsAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> SetOut:
    """Name, description, items (the whole list). A built-in set keeps its name and role."""
    row = _get_set(session, set_id)
    changes: dict[str, Any] = {}
    if body.name is not None and body.name.strip() != row.name:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "tên bộ quyền trống")
        if row.builtin:
            raise HTTPException(409, "bộ có sẵn không đổi tên được")
        _name_free(session, name, row.id)
        changes["renamed"] = {"from": row.name, "to": name}
        row.name = name
    if "description" in body.model_fields_set and body.description != row.description:
        changes["description"] = True
        row.description = body.description
    if body.items is not None:
        old = _items(session, row.id)
        new = _validate(body.items)
        granted = _widened(old, new)
        if granted and not principal.is_admin and _holds(principal, row, session):
            raise HTTPException(403, "không tự cấp thêm quyền cho mình")
        _within_own(principal, granted)
        _confirm_sensitive(granted, body.confirm_sensitive)
        if new != old:
            _write_items(session, row.id, new)
            changes["added"] = granted
            changes["removed"] = sorted(set(old) - set(new))
            changes["narrowed"] = {
                k: v for k, v in new.items() if k in old and RANK[v] < RANK[old[k]]
            }
    if changes:
        audit(
            session,
            principal.user_id,
            "permission_set.update",
            f"permission_set:{row.id}",
            name=row.name,
            **changes,
        )
    session.commit()
    return _one_out(session, settings, row)


@router.delete("/admin/permission-sets/{set_id}", status_code=204)
def delete_permission_set(
    set_id: int, principal: PermissionsAdmin, session: SessionDep
) -> Response:
    """A custom set (its people lose what it gave them). Built-in sets cannot go."""
    row = _get_set(session, set_id)
    if row.builtin or row.auto_role is not None:
        raise HTTPException(409, "bộ có sẵn không xoá được")
    given = sorted(_given(session, row.id))
    audit(
        session,
        principal.user_id,
        "permission_set.delete",
        f"permission_set:{row.id}",
        name=row.name,
        items=_items(session, row.id),
        users=given,
    )
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.put("/admin/permission-sets/{set_id}/users")
def set_permission_set_users(
    set_id: int,
    body: SetUsersIn,
    principal: PermissionsAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> SetOut:
    """Replace who is given a custom set. A built-in set follows its role instead."""
    row = _get_set(session, set_id)
    if row.builtin or row.auto_role is not None:
        raise HTTPException(409, "bộ có sẵn tự gán theo vai trò, không gán tay")
    wanted = set(body.user_ids)
    known = set(session.scalars(select(User.id).where(User.id.in_(wanted)))) if wanted else set()
    if missing := sorted(wanted - known):
        raise HTTPException(422, f"không có người: {', '.join(map(str, missing))}")
    current = _given(session, row.id)
    added, removed = wanted - current, current - wanted
    items = _items(session, row.id)
    if added:
        if not principal.is_admin:
            if principal.user_id in added:
                raise HTTPException(403, "không tự gán bộ quyền cho mình")
            _within_own(principal, items)
        _confirm_sensitive(items, body.confirm_sensitive)
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
            name=row.name,
            added=sorted(added),
            removed=sorted(removed),
        )
    session.commit()
    return _one_out(session, settings, row)


@router.get("/admin/users/{user_id}/permissions")
def user_permissions(
    user_id: int, principal: PermissionsAdmin, session: SessionDep, settings: SettingsDep
) -> UserPermissionsOut:
    """Xem trước: what one person holds now, and which sets give each permission."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    person = _person(user)
    if _is_admin(user, settings):
        return UserPermissionsOut(
            user=person,
            is_admin=True,
            active=user.active is not False,
            roles=["admin"],
            sets=[],
            permissions=[
                EffectiveOut(
                    key=k, scope=v, sources=[SourceOut(set_id=None, name="Admin", scope=v)]
                )
                for k, v in everything().items()
            ],
        )
    if user.active is False:
        return UserPermissionsOut(
            user=person, is_admin=False, active=False, roles=[], sets=[], permissions=[]
        )
    held = load_principal(session, user.id, settings)
    roles = _auto_roles(held)
    given = select(UserPermissionSet.set_id).where(UserPermissionSet.user_id == user.id)
    by_role: dict[str, int] = {r: i for i, r in enumerate(AUTO_ROLES)}
    sets = sorted(
        session.scalars(
            select(PermissionSet).where(
                PermissionSet.auto_role.in_(roles) | PermissionSet.id.in_(given)
            )
        ),
        key=lambda s: (s.auto_role is None, by_role.get(s.auto_role or "", 99), s.name.lower()),
    )
    sources: dict[str, list[SourceOut]] = {}
    for s in sets:
        for key, scope in _items(session, s.id).items():
            if valid_item(key, scope):
                sources.setdefault(key, []).append(SourceOut(set_id=s.id, name=s.name, scope=scope))
    order = {p.key: i for i, p in enumerate(CATALOG)}
    return UserPermissionsOut(
        user=person,
        is_admin=False,
        active=True,
        roles=roles,
        sets=[
            HeldSetOut(
                id=s.id,
                name=s.name,
                auto_role=s.auto_role,
                via="role" if s.auto_role is not None else "given",
            )
            for s in sets
        ],
        permissions=[
            EffectiveOut(
                key=k,
                scope=v,
                sources=sorted(sources.get(k, []), key=lambda src: -RANK[src.scope]),
            )
            for k, v in sorted(held.perms.items(), key=lambda kv: order.get(kv[0], 999))
        ],
    )
