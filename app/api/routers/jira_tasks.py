"""Task Jira (docs/specs/27 §18, user 2026-11-09): Jira issues with their MRs and reviews, an
issue's detail, and the MRs that name no issue. Read only; app/jira/tasks.py says who sees what.
"""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal
from app.config import Settings, get_settings
from app.jira import tasks
from app.jira.settings import effective_base_url, task_source

router = APIRouter(tags=["jira"])

SessionDep = Annotated[Session, Depends(get_session)]
Page = Annotated[int, Query(ge=1, le=10_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Day = Annotated[date | None, Query()]
MAX_SPAN_DAYS = 400


def _span(start: date | None, end: date | None) -> None:
    if start and end:
        if start > end:
            raise HTTPException(422, "start must not be after end")
        if (end - start).days > MAX_SPAN_DAYS:
            raise HTTPException(422, f"at most {MAX_SPAN_DAYS} days")


def _project(principal: CurrentPrincipal, bp: int | None) -> None:
    seen = tasks.seen_projects(principal)
    if bp is not None and seen is not None and bp not in seen:
        raise HTTPException(404, "dự án not found")


@router.get("/jira/tasks/options")
def task_options(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """What the filters offer, whether Jira is connected and is the task source."""
    return {
        **tasks.options(session, principal),
        "configured": bool(effective_base_url(session, settings)),
        "task_source": task_source(session, settings),
        "can_admin": principal.is_admin or principal.can("jira.admin"),
    }


@router.get("/jira/tasks")
def list_tasks(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int | None = None,
    jira_project: Annotated[
        str | None, Query(max_length=20, pattern=r"^[A-Z][A-Z0-9_]{1,19}$")
    ] = None,
    state: Literal["open", "closed", "all"] = "all",
    type: Annotated[str | None, Query(max_length=40)] = None,
    assignee: int | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    linked: Literal["any", "with_mr", "without_mr"] = "any",
    start: Day = None,
    end: Day = None,
    page: Page = 1,
    page_size: PageSize = 25,
) -> dict[str, Any]:
    """Jira issues worked on in the period (created before its end, not closed before its
    start), each with its MRs' current reviews; and the period's counts."""
    _span(start, end)
    _project(principal, business_project_id)
    f = tasks.IssueFilter(
        business_project_id=business_project_id,
        jira_project=jira_project,
        state=state,
        type=type,
        assignee=assignee,
        q=q,
        linked=linked,
        start=start,
        end=end,
    )
    out = tasks.list_issues(session, principal, f, (page - 1) * page_size, page_size)
    return {**out, "page": page, "page_size": page_size}


@router.get("/jira/tasks/unlinked-mrs")
def list_unlinked_mrs(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int | None = None,
    start: Day = None,
    end: Day = None,
    page: Page = 1,
    page_size: PageSize = 25,
) -> dict[str, Any]:
    """MRs opened in the period that name no Jira issue."""
    _span(start, end)
    _project(principal, business_project_id)
    out = tasks.unlinked_mrs(
        session, principal, business_project_id, start, end, (page - 1) * page_size, page_size
    )
    return {**out, "page": page, "page_size": page_size}


@router.get("/jira/tasks/{key}")
def get_task(
    key: Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9_]{1,19}-\d{1,9}$")],
    principal: CurrentPrincipal,
    session: SessionDep,
) -> dict[str, Any]:
    """One issue: its fields, parent and sub-tasks, and every MR (with its review)."""
    detail = tasks.issue_detail(session, principal, key.upper())
    if detail is None:
        raise HTTPException(404, "issue not found")
    return detail
