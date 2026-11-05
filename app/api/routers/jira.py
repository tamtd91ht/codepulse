"""Admin > DPI > Jira (docs/specs/27 §12-13): the connection, the task source, the registered
Jira projects, the field mapping, and "Đồng bộ ngay". Everything needs `dpi.admin`; every
change is audited. The token never leaves the server."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.security import CurrentPrincipal, Principal
from app.collector.sync import SYNC_JOB_TIMEOUT, Enqueuer
from app.config import Settings, get_settings
from app.jira.client import JiraClient, JiraError
from app.jira.mapping import resolve_fields
from app.jira.settings import (
    DPI_TYPES,
    jira_configured,
    mapping,
    parse_mapping,
    set_mapping,
    set_task_source,
    task_source,
)
from app.models import Account, BusinessProject, JiraProject, Project

router = APIRouter(prefix="/dpi/jira", tags=["dpi"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
KEY_PATTERN = r"^[A-Z][A-Z0-9_]{1,9}$"


class JiraApi(Protocol):
    def server_info(self) -> dict[str, Any]: ...

    def fields(self) -> list[dict[str, Any]]: ...

    def projects(self) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


JiraFactory = Callable[[Settings], JiraApi]


def get_jira_factory() -> JiraFactory:
    return JiraClient.from_settings


JiraFactoryDep = Annotated[JiraFactory, Depends(get_jira_factory)]


def _admin(principal: Principal) -> None:
    if not principal.can("dpi.admin"):
        raise HTTPException(403, "chỉ admin DPI")


def _configured(settings: Settings) -> None:
    if not jira_configured(settings):
        raise HTTPException(409, "chưa cấu hình Jira (JIRA_BASE_URL, JIRA_TOKEN)")


def _call[T](settings: Settings, factory: JiraFactory, work: Callable[[JiraApi], T]) -> T:
    _configured(settings)
    client = factory(settings)
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
    principal: CurrentPrincipal, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    _admin(principal)
    total, linked = session.execute(
        select(func.count(), func.count(Account.user_id)).where(Account.kind == "jira_user")
    ).one()
    base_url = settings.jira_base_url.strip().rstrip("/")
    return {
        "configured": jira_configured(settings),
        "base_url": base_url or None,
        "task_source": task_source(session, settings),
        "mapping": mapping(session).as_json(),
        "dpi_types": list(DPI_TYPES),
        "projects": _projects(session),
        "accounts": {"total": int(total), "linked": int(linked)},
        "interval_min": settings.jira_sync_interval_min,
    }


@router.post("/test")
def test_connection(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    factory: JiraFactoryDep,
) -> dict[str, Any]:
    """serverInfo + /field: the Jira version and the custom fields the mapping names."""
    _admin(principal)
    rules = mapping(session)

    def work(client: JiraApi) -> dict[str, Any]:
        info = client.server_info()
        ids = resolve_fields(client.fields(), rules)
        return {
            "ok": True,
            "version": info.get("version"),
            "server_title": info.get("serverTitle"),
            "fields": ids.as_json(),
        }

    return _call(settings, factory, work)


@router.get("/remote-projects")
def remote_projects(
    principal: CurrentPrincipal, settings: SettingsDep, factory: JiraFactoryDep
) -> dict[str, Any]:
    _admin(principal)
    found = _call(settings, factory, lambda client: client.projects())
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
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, Any]:
    """ "Đồng bộ ngay": the sync of every enabled project, on a worker."""
    _admin(principal)
    _configured(settings)
    from app.jira.sync import JOB_ID, JOB_TIMEOUT

    job_id = f"{JOB_ID}-now-{int(datetime.now(UTC).timestamp())}"
    queue.enqueue("app.jira.sync.sync_all_job", job_id=job_id, job_timeout=JOB_TIMEOUT)
    audit(session, principal.user_id, "dpi.jira.sync", "jira", job_id=job_id)
    session.commit()
    return {"queued": True, "job_id": job_id}
