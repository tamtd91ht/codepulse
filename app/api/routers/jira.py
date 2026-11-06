"""Admin > Jira and DPI > Jira (docs/specs/27 §12-13, §15-16): the connection (saved on the
web, else JIRA_*), the task source, the registered Jira projects and the CodePulse dự án their
issues count for (several per project, by component / label), the field mapping, and the sync
("Đồng bộ ngay", one project, or from the start).
Everything needs `jira.admin` (an admin has it); every change is audited. The secret (a PAT, or
the service account's password with basic auth) never leaves the server: it is write-only here,
and never logged or audited."""

import dataclasses
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_secret_box, get_session
from app.api.security import CurrentPrincipal, Principal
from app.collector.sync import SYNC_JOB_TIMEOUT, Enqueuer
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.jira import scopes as jira_scopes
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
from app.models import Account, BusinessProject, Issue, JiraProject, Project

router = APIRouter(prefix="/dpi/jira", tags=["dpi"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]
KEY_PATTERN = r"^[A-Z][A-Z0-9_]{1,9}$"
PathKey = PathParam(pattern=KEY_PATTERN)


class JiraApi(Protocol):
    def server_info(self) -> dict[str, Any]: ...

    def myself(self) -> dict[str, Any]: ...

    def fields(self) -> list[dict[str, Any]]: ...

    def projects(self) -> list[dict[str, Any]]: ...

    def components(self, key: str) -> list[dict[str, Any]]: ...

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
    """Registered projects with their scopes (§15): each scope's issues as stored, and
    `unmatched`: issues no rule matched that went to every dự án of the project."""
    rows = list(session.scalars(select(JiraProject).order_by(JiraProject.key)))
    ids = [p.id for p in rows]
    scopes = jira_scopes.scopes_json(session, ids)
    counts = jira_scopes.counts(session, ids)
    out = []
    for p in rows:
        per = counts[p.id]["per"]
        out.append(
            {
                "id": p.id,
                "key": p.key,
                "name": p.name,
                "enabled": p.enabled,
                "scopes": [
                    {**s, "issues": int(per.get(s["business_project"]["id"], 0))}
                    for s in scopes[p.id]
                ],
                "unmatched": int(counts[p.id]["fallback"]),
                "last_synced_at": p.last_synced_at,
                "synced_until": p.synced_until,
                "last_error": p.last_error,
                "issues_count": p.issues_count,
            }
        )
    return out


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
        "projects": (projects := _projects(session)),
        "accounts": {"total": int(total), "linked": int(linked)},
        # The status strip (§16): the last time any project synced, and how many failed.
        "sync": {
            "last_at": max(
                (p["last_synced_at"] for p in projects if p["last_synced_at"]), default=None
            ),
            "failed": sum(1 for p in projects if p["enabled"] and p["last_error"]),
            "enabled": sum(1 for p in projects if p["enabled"]),
            "never": sum(1 for p in projects if p["enabled"] and not p["synced_until"]),
        },
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


class ScopeIn(BaseModel):
    """One CodePulse dự án of a Jira project (§15): its issues with one of `components` /
    `labels`; neither: every other issue (the ones no rule matches)."""

    business_project_id: int
    components: list[str] = Field(default_factory=list, max_length=200)
    labels: list[str] = Field(default_factory=list, max_length=200)


class ProjectIn(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    name: str | None = Field(default=None, max_length=200)
    enabled: bool
    # The dự án its issues count for (§15); left out: kept as they are.
    scopes: list[ScopeIn] | None = Field(default=None, max_length=jira_scopes.MAX_SCOPES)
    # Before §15, one dự án per project: still read, as one catch-all scope (null: none).
    business_project_id: int | None = None


class ProjectsIn(BaseModel):
    items: list[ProjectIn] = Field(max_length=200)


def _rules(item: ProjectIn) -> list[jira_scopes.Rule] | None:
    """The scopes asked for, checked: no dự án twice; None: keep the project's."""
    if item.scopes is None:
        if "business_project_id" not in item.model_fields_set:
            return None
        bp = item.business_project_id
        return [jira_scopes.Rule(bp)] if bp is not None else []
    rules: list[jira_scopes.Rule] = []
    seen: set[int] = set()
    for n, scope in enumerate(item.scopes, 1):
        if scope.business_project_id in seen:
            raise ValueError(f"{item.key}: dự án CodePulse lặp lại ở dòng {n}")
        seen.add(scope.business_project_id)
        rules.append(
            jira_scopes.Rule(
                scope.business_project_id,
                tuple(jira_scopes.clean_values(scope.components, f"{item.key} dòng {n} component")),
                tuple(jira_scopes.clean_values(scope.labels, f"{item.key} dòng {n} label")),
            )
        )
    return rules


@router.put("/projects")
def put_projects(
    body: ProjectsIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, Any]:
    """Register or update Jira projects by key (the ones not listed stay as they are), and the
    dự án their issues count for (§15). Stored issues are routed again at once; a rule on
    components for a project whose issues were stored without them sends it back to a full
    sync (queued when Jira is configured)."""
    _admin(principal)
    try:
        wanted = {item.key: _rules(item) for item in body.items}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    bps = {r.business_project_id for rules in wanted.values() for r in rules or []}
    if bps:
        known = set(session.scalars(select(BusinessProject.id).where(BusinessProject.id.in_(bps))))
        if missing := sorted(bps - known):
            raise HTTPException(422, f"không có dự án CodePulse id {missing[0]}")
    existing = {
        p.key: p
        for p in session.scalars(
            select(JiraProject).where(JiraProject.key.in_([i.key for i in body.items]))
        )
    }
    rerouted: dict[str, int] = {}
    refetch: list[str] = []
    for item in body.items:
        row = existing.get(item.key)
        if row is None:
            row = JiraProject(key=item.key)
            session.add(row)
            existing[item.key] = row
        if item.name is not None:
            row.name = item.name.strip() or None
        row.enabled = item.enabled
        session.flush()
        rules = wanted[item.key]
        if rules is None or not jira_scopes.replace_scopes(session, row, rules):
            continue
        rerouted[item.key] = jira_scopes.recompute(session, row.id)
        if any(r.components for r in rules) and jira_scopes.components_missing(session, row.id):
            row.synced_until = None  # fetched again from the backfill, components included
            refetch.append(item.key)
    session.flush()
    audit(
        session,
        principal.user_id,
        "dpi.jira.projects",
        "jira",
        items=[i.model_dump(exclude_unset=True) for i in body.items],
        rerouted=rerouted,
        refetch=refetch,
    )
    session.commit()
    queued = False
    if refetch and connection(session, settings, box).configured:
        _enqueue_sync(queue, keys=refetch, full=True)
        queued = True
    return {"projects": _projects(session), "refetch": refetch, "refetch_queued": queued}


@router.get("/projects/{key}/options")
def project_options(
    key: Annotated[str, PathKey],
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    factory: JiraFactoryDep,
) -> dict[str, Any]:
    """Suggestions for the rules of one project (§15): its components on Jira (else the ones
    seen on its stored issues) and the labels of its stored issues, most used first."""
    _admin(principal)
    project = session.scalars(select(JiraProject).where(JiraProject.key == key)).first()
    local_components: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    if project is not None:
        for comps, tags in session.execute(
            select(Issue.components, Issue.labels).where(Issue.jira_project_id == project.id)
        ):
            local_components.update(str(c) for c in comps or [])
            labels.update(str(t) for t in tags or [] if not str(t).startswith("blocked::"))
    components: list[str]
    source = "jira"
    conn = connection(session, settings, box)
    try:
        if not conn.configured:
            raise HTTPException(409, NOT_CONFIGURED)
        found = _call(conn, factory, lambda client: client.components(key))
        components = sorted({str(c["name"]) for c in found if c.get("name")}, key=str.lower)
    except HTTPException:
        components = [c for c, _ in local_components.most_common(200)]
        source = "local"
    return {
        "components": components,
        "components_source": source,
        "labels": [t for t, _ in labels.most_common(200)],
    }


class PreviewIn(BaseModel):
    scopes: list[ScopeIn] = Field(max_length=jira_scopes.MAX_SCOPES)


@router.post("/projects/{key}/preview")
def project_preview(
    key: Annotated[str, PathKey],
    body: PreviewIn,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> dict[str, Any]:
    """How the stored issues of a project would split between these scopes, not saved (§15)."""
    _admin(principal)
    try:
        rules = _rules(ProjectIn(key=key, enabled=True, scopes=body.scopes)) or []
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    project = session.scalars(select(JiraProject).where(JiraProject.key == key)).first()
    if project is None:
        return {"total": 0, "per_business_project": {}, "unmatched": 0, "components_missing": False}
    return jira_scopes.preview(session, project.id, rules)


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


def _enqueue_sync(queue: Enqueuer, keys: list[str] | None = None, full: bool = False) -> str:
    from app.jira.sync import JOB_ID, JOB_TIMEOUT

    job_id = f"{JOB_ID}-now-{int(datetime.now(UTC).timestamp() * 1000)}"
    # manual: runs even while a refused login pauses the scheduled syncs (§3).
    queue.enqueue(
        "app.jira.sync.sync_all_job",
        manual=True,
        keys=keys,
        full=full,
        job_id=job_id,
        job_timeout=JOB_TIMEOUT,
    )
    return job_id


class SyncIn(BaseModel):
    """Which projects (default: every enabled one), and `full`: from the start (the backfill
    window again) instead of since the last sync (§16)."""

    keys: list[Annotated[str, Field(pattern=KEY_PATTERN)]] | None = Field(
        default=None, max_length=200
    )
    full: bool = False


@router.post("/sync", status_code=202)
def sync_now(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
    body: Annotated[SyncIn | None, Body()] = None,
) -> dict[str, Any]:
    """ "Đồng bộ ngay": every enabled project, or the ones named, on a worker; `full` first
    drops their sync marks so the backfill window is fetched again."""
    _admin(principal)
    _configured(connection(session, settings, box))
    wanted = body or SyncIn()
    query = select(JiraProject).where(JiraProject.enabled.is_(True))
    if wanted.keys is not None:
        query = query.where(JiraProject.key.in_(wanted.keys))
    projects = list(session.scalars(query))
    if wanted.keys is not None and not projects:
        raise HTTPException(409, "không có dự án Jira nào đang bật trong số này để đồng bộ")
    if wanted.full:
        for project in projects:
            project.synced_until = None
    keys = sorted(p.key for p in projects) if wanted.keys is not None else None
    audit(session, principal.user_id, "dpi.jira.sync", "jira", keys=keys, full=wanted.full)
    session.commit()
    job_id = _enqueue_sync(queue, keys=keys if keys is not None else None, full=wanted.full)
    return {"queued": True, "job_id": job_id, "keys": keys, "full": wanted.full}
