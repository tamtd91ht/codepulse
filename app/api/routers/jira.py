"""Admin > Jira and DPI > Jira (docs/specs/27 §12-13): the connection (saved on the web, else
JIRA_*), the task source, the registered Jira projects, the field mapping, and "Đồng bộ ngay".
Everything needs `jira.admin` (an admin has it); every change is audited. The secret (a PAT, or
the service account's password with basic auth) never leaves the server: it is write-only here,
and never logged or audited."""

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_secret_box, get_session
from app.api.security import CurrentPrincipal, Principal
from app.collector.sync import SYNC_JOB_TIMEOUT, Enqueuer
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.jira.client import JiraAuthError, JiraClient, JiraError
from app.jira.mapping import resolve_fields
from app.jira.settings import (
    DPI_TYPES,
    AuthType,
    JiraConnection,
    auth_failure,
    check_auth_type,
    check_ca_pem,
    check_secret,
    check_username,
    clear_auth_failure,
    clear_connection,
    clear_token,
    connection,
    mapping,
    normalize_base_url,
    parse_connection,
    parse_mapping,
    pat_supported,
    save_connection,
    save_token,
    set_mapping,
    set_task_source,
    task_source,
)
from app.models import Account, BusinessProject, JiraProject, Project

router = APIRouter(prefix="/dpi/jira", tags=["dpi"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]
KEY_PATTERN = r"^[A-Z][A-Z0-9_]{1,9}$"


class JiraApi(Protocol):
    def server_info(self) -> dict[str, Any]: ...

    def myself(self) -> dict[str, Any]: ...

    def fields(self) -> list[dict[str, Any]]: ...

    def projects(self) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


JiraFactory = Callable[[JiraConnection], JiraApi]


def get_jira_factory() -> JiraFactory:
    return JiraClient.from_connection


JiraFactoryDep = Annotated[JiraFactory, Depends(get_jira_factory)]


def _admin(principal: Principal) -> None:
    if not principal.can("jira.admin"):  # an admin has every permission
        raise HTTPException(403, "cần quyền Cấu hình tích hợp Jira (jira.admin)")


NOT_CONFIGURED = (
    "chưa cấu hình Jira: nhập URL và token (hoặc tài khoản + mật khẩu) ở Admin > Jira > Kết nối"
)


def _configured(conn: JiraConnection) -> None:
    if not conn.configured:
        detail = NOT_CONFIGURED if conn.enabled else "tích hợp Jira đang tắt (Admin > Jira)"
        raise HTTPException(409, detail)


def _call[T](
    conn: JiraConnection,
    factory: JiraFactory,
    work: Callable[[JiraApi], T],
    *,
    need_enabled: bool = True,
) -> T:
    if need_enabled:
        _configured(conn)
    elif not conn.reachable:
        raise HTTPException(409, NOT_CONFIGURED)
    try:
        client = factory(conn)
    except Exception as exc:  # e.g. a CA ssl refuses; never anything of the token
        raise HTTPException(502, f"không gọi được Jira: {exc.__class__.__name__}") from None
    try:
        return work(client)
    except JiraError as exc:
        raise HTTPException(502, f"không gọi được Jira: {exc.message}") from None
    finally:
        client.close()


def _projects(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(JiraProject, BusinessProject)
        .outerjoin(BusinessProject, BusinessProject.id == JiraProject.business_project_id)
        .order_by(JiraProject.key)
    ).all()
    return [
        {
            "id": p.id,
            "key": p.key,
            "name": p.name,
            "enabled": p.enabled,
            "business_project": {"id": bp.id, "name": bp.name} if bp is not None else None,
            "last_synced_at": p.last_synced_at,
            "synced_until": p.synced_until,
            "last_error": p.last_error,
            "issues_count": p.issues_count,
        }
        for p, bp in rows
    ]


@router.get("")
def overview(
    principal: CurrentPrincipal, session: SessionDep, settings: SettingsDep, box: BoxDep
) -> dict[str, Any]:
    _admin(principal)
    total, linked = session.execute(
        select(func.count(), func.count(Account.user_id)).where(Account.kind == "jira_user")
    ).one()
    conn = connection(session, settings, box)
    return {
        "configured": conn.configured,
        "base_url": conn.base_url or None,
        "connection": conn.as_json(),
        "task_source": task_source(session, settings),
        "mapping": mapping(session).as_json(),
        "dpi_types": list(DPI_TYPES),
        "projects": _projects(session),
        "accounts": {"total": int(total), "linked": int(linked)},
        "interval_min": conn.interval_min,
        # The last sync's login was refused: scheduled syncs wait (docs/specs/27 §3).
        "auth_failure": auth_failure(session, conn, box),
    }


MOVED_SERVER = (
    "token hiện có thuộc máy chủ Jira khác URL này và không được gửi sang đây: nhập token cho"
    " máy chủ này (hoặc Dùng lại biến môi trường)"
)
NEED_USERNAME = "username: cần tên đăng nhập của tài khoản dịch vụ khi dùng Tài khoản + mật khẩu"
NOT_A_PAT = (
    "token: mật khẩu đang lưu không dùng được làm Personal Access Token: nhập PAT cho máy chủ này"
)
# A change to one of these is a new login: the scheduled syncs paused by a refused one resume.
CREDENTIALS = {"base_url", "auth_type", "username", "token"}


def _check_login(conn: JiraConnection) -> None:
    """Basic auth needs a username; a PAT must be one (a password kept from basic auth may
    not be: spaces, non-ASCII)."""
    if conn.auth_type == "basic" and conn.token and not conn.username:
        raise ValueError(NEED_USERNAME)
    if conn.auth_type == "pat" and conn.token:
        try:
            check_secret(conn.token, "pat")
        except ValueError:
            raise ValueError(NOT_A_PAT) from None


class ConnectionIn(BaseModel):
    """PUT /connection: a key left out stays as it is; "" (base_url, token, ca_pem) or null
    (the numbers, enabled) drops the value saved on the web and the environment applies."""

    base_url: str | None = Field(default=None, max_length=2000)
    auth_type: Literal["pat", "basic"] | None = None
    username: str | None = Field(default=None, max_length=2000)
    token: str | None = Field(default=None, max_length=2000)  # write-only: the PAT or password
    ca_pem: str | None = Field(default=None, max_length=200_000)
    interval_min: int | None = None
    backfill_days: int | None = None
    timeout_s: int | None = None
    enabled: bool | None = None


@router.put("/connection")
def put_connection(
    body: ConnectionIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
) -> dict[str, Any]:
    _admin(principal)
    given = body.model_dump(include=body.model_fields_set)
    token = given.pop("token", None)
    try:
        values = parse_connection(given)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    before = connection(session, settings, box)
    changed = sorted(values)
    # Written in a savepoint: a request refused below leaves nothing behind.
    savepoint = session.begin_nested()
    try:
        save_connection(session, values, principal.user_id)
        if token:
            # Checked as the secret of the auth type in effect after this request (a PAT or a
            # password), and bound to the server of the URL in effect after it (§2).
            target = connection(session, settings, box)
            save_token(session, box, token, principal.user_id, target.base_url, target.auth_type)
            changed.append("token")
        elif token == "" and clear_token(session):
            changed.append("token")
        after = connection(session, settings, box)
        # The resolver never sends a secret to another server than its own; a request that
        # leaves Jira without one for that reason (a new URL without its token or password, or
        # the web secret cleared while JIRA_TOKEN belongs to another server) is refused, not
        # half done. A username or auth type changed on the same server keeps the secret.
        if before.token and not after.token and after.token_other_server:
            raise ValueError(MOVED_SERVER)
        if CREDENTIALS & set(changed):  # e.g. switching the integration off is always allowed
            _check_login(after)
    except ValueError as exc:
        savepoint.rollback()
        raise HTTPException(422, str(exc)) from None
    savepoint.commit()
    if CREDENTIALS & set(changed):
        clear_auth_failure(session)  # a new login: the scheduled syncs try again
    audit(
        session,
        principal.user_id,
        "dpi.jira.connection",
        "jira",
        changed=sorted(changed),
        base_url=after.base_url or None,
        auth_type=after.auth_type,
        username=after.username or None,
        enabled=after.enabled,
    )
    session.commit()
    return {"connection": after.as_json()}


@router.delete("/connection")
def delete_connection(
    principal: CurrentPrincipal, session: SessionDep, settings: SettingsDep, box: BoxDep
) -> dict[str, Any]:
    """ "Dùng lại biến môi trường": every value saved on the web goes."""
    _admin(principal)
    cleared = clear_connection(session)
    audit(session, principal.user_id, "dpi.jira.connection.reset", "jira", cleared=cleared)
    session.commit()
    return {"connection": connection(session, settings, box).as_json()}


class TestIn(BaseModel):
    """Values typed on the screen and not saved yet; a missing one is the one in effect."""

    base_url: str | None = Field(default=None, max_length=2000)
    auth_type: Literal["pat", "basic"] | None = None
    username: str | None = Field(default=None, max_length=2000)
    token: str | None = Field(default=None, max_length=2000)  # the PAT or the password
    ca_pem: str | None = Field(default=None, max_length=200_000)


def _with_unsaved(
    session: Session, settings: Settings, box: SecretBox, body: TestIn | None
) -> JiraConnection:
    """The connection to test: the values typed, else the ones in effect. A URL typed without a
    token (or password) gets only one of its own server (the resolver's rule, §2)."""
    if body is None:
        conn = connection(session, settings, box)
    else:
        try:
            base_url = normalize_base_url(body.base_url) if body.base_url else None
            ca_pem = check_ca_pem(body.ca_pem) if body.ca_pem and body.ca_pem.strip() else None
            conn = connection(session, settings, box, base_url=base_url)
            auth_type: AuthType = (
                check_auth_type(body.auth_type) if body.auth_type else conn.auth_type
            )
            username = check_username(body.username) if body.username else conn.username
            token = check_secret(body.token, auth_type) if body.token else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if token is None and not conn.token and conn.token_other_server:
            raise HTTPException(422, MOVED_SERVER)
        conn = dataclasses.replace(
            conn,
            token=token or conn.token,
            ca_pem=ca_pem if ca_pem is not None else conn.ca_pem,
            auth_type=auth_type,
            username=username,
        )
    try:
        _check_login(conn)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return conn


def _unsaved(body: TestIn | None) -> bool:
    """Values typed and not saved were tested (not the connection in effect)."""
    return body is not None and any(
        v not in (None, "") for v in body.model_dump(include=body.model_fields_set).values()
    )


@router.post("/test")
def test_connection(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    factory: JiraFactoryDep,
    body: Annotated[TestIn | None, Body()] = None,
) -> dict[str, Any]:
    """serverInfo + /myself + /field: the Jira version, the account the credentials log in as
    (serverInfo alone answers anonymous callers) and the custom fields the mapping names. With
    a body, the values typed on the screen and not saved yet (tested before "Lưu"); nothing is
    saved. Works with the integration switched off. A pass of the connection in effect resumes
    the scheduled syncs a refused login paused."""
    _admin(principal)
    rules = mapping(session)
    conn = _with_unsaved(session, settings, box, body)

    def work(client: JiraApi) -> dict[str, Any]:
        info = client.server_info()
        version = info.get("version")
        has_pat = pat_supported(
            str(version) if version is not None else None, info.get("versionNumbers")
        )
        try:
            me = client.myself()
        except JiraAuthError as exc:
            if conn.auth_type == "pat" and has_pat is False:
                raise JiraError(
                    exc.status_code,
                    f"Jira {version} chưa có Personal Access Token (có từ 8.14): chọn Tài"
                    " khoản + mật khẩu của tài khoản dịch vụ",
                ) from None
            raise
        ids = resolve_fields(client.fields(), rules)
        return {
            "ok": True,
            "version": version,
            "server_title": info.get("serverTitle"),
            "deployment_type": info.get("deploymentType"),
            "pat_supported": has_pat,
            "auth_type": conn.auth_type,
            "user": me.get("name") or me.get("key"),
            "fields": ids.as_json(),
        }

    result = _call(conn, factory, work, need_enabled=False)
    if not _unsaved(body) and clear_auth_failure(session):
        session.commit()
    return result


@router.get("/remote-projects")
def remote_projects(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    factory: JiraFactoryDep,
) -> dict[str, Any]:
    _admin(principal)
    conn = connection(session, settings, box)
    found = _call(conn, factory, lambda client: client.projects())
    items = [
        {"key": str(p["key"]), "name": p.get("name")}
        for p in found
        if isinstance(p.get("key"), str)
    ]
    return {"items": sorted(items, key=lambda p: p["key"])}


class ProjectIn(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    name: str | None = Field(default=None, max_length=200)
    enabled: bool
    business_project_id: int | None = None


class ProjectsIn(BaseModel):
    items: list[ProjectIn] = Field(max_length=200)


@router.put("/projects")
def put_projects(
    body: ProjectsIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    """Register or update Jira projects by key (the ones not listed stay as they are)."""
    _admin(principal)
    wanted = {i.business_project_id for i in body.items if i.business_project_id is not None}
    if wanted:
        known = set(
            session.scalars(select(BusinessProject.id).where(BusinessProject.id.in_(wanted)))
        )
        if missing := sorted(wanted - known):
            raise HTTPException(422, f"không có dự án CodePulse id {missing[0]}")
    existing = {
        p.key: p
        for p in session.scalars(
            select(JiraProject).where(JiraProject.key.in_([i.key for i in body.items]))
        )
    }
    for item in body.items:
        row = existing.get(item.key)
        if row is None:
            row = JiraProject(key=item.key)
            session.add(row)
            existing[item.key] = row
        if item.name is not None:
            row.name = item.name.strip() or None
        row.enabled = item.enabled
        row.business_project_id = item.business_project_id
    session.flush()
    audit(
        session,
        principal.user_id,
        "dpi.jira.projects",
        "jira",
        items=[i.model_dump() for i in body.items],
    )
    session.commit()
    return {"projects": _projects(session)}


@router.put("/mapping")
def put_mapping(
    body: dict[str, Any], principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    _admin(principal)
    try:
        value = parse_mapping(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    set_mapping(session, value, principal.user_id)
    audit(session, principal.user_id, "dpi.jira.mapping", "jira", mapping=value.as_json())
    session.commit()
    return {"mapping": value.as_json()}


class SourceIn(BaseModel):
    source: Literal["gitlab", "jira"]


@router.put("/source")
def put_source(
    body: SourceIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, Any]:
    """Back from Jira to GitLab (§9): GitLab issues were not fetched meanwhile and the repos'
    sync windows moved on, so a full sync of every tracked repo is queued."""
    _admin(principal)
    before = task_source(session, settings)
    set_task_source(session, body.source, principal.user_id)
    refetch: list[int] = []
    if before == "jira" and body.source == "gitlab":
        refetch = list(
            session.scalars(select(Project.id).where(Project.tracked).order_by(Project.id))
        )
    audit(
        session,
        principal.user_id,
        "dpi.jira.source",
        "jira",
        source=body.source,
        full_syncs=refetch,
    )
    session.commit()
    for project_id in refetch:
        queue.enqueue(
            "app.collector.sync.sync_project_job",
            project_id,
            full=True,
            job_id=f"sync-project-{project_id}-full",
            job_timeout=SYNC_JOB_TIMEOUT,
        )
    return {"task_source": body.source, "full_syncs_queued": len(refetch)}


@router.post("/sync", status_code=202)
def sync_now(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, Any]:
    """ "Đồng bộ ngay": the sync of every enabled project, on a worker."""
    _admin(principal)
    _configured(connection(session, settings, box))
    from app.jira.sync import JOB_ID, JOB_TIMEOUT

    job_id = f"{JOB_ID}-now-{int(datetime.now(UTC).timestamp())}"
    # manual: runs even while a refused login pauses the scheduled syncs (§3).
    queue.enqueue("app.jira.sync.sync_all_job", manual=True, job_id=job_id, job_timeout=JOB_TIMEOUT)
    audit(session, principal.user_id, "dpi.jira.sync", "jira", job_id=job_id)
    session.commit()
    return {"queued": True, "job_id": job_id}
