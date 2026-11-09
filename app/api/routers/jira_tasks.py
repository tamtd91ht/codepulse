"""Task Jira (docs/specs/27 §18, user 2026-11-09): Jira issues with their MRs and reviews, an
issue's detail, and the MRs that name no issue. Read only; app/jira/tasks.py says who sees what.
"""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import CurrentPrincipal
from app.config import Settings, get_settings
from app.jira import tasks
from app.jira.client import JiraClient, JiraError
from app.jira.settings import connection, effective_base_url, task_source
from app.models import Commit, Issue, IssueCommitLink, Project, Review, User

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
    linked: Literal["any", "with_mr", "without_mr", "bug_unfixed"] = "any",
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


@router.get("/jira/tasks/unlinked-commits")
def list_unlinked_commits(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int | None = None,
    start: Day = None,
    end: Day = None,
    page: Page = 1,
    page_size: PageSize = 25,
) -> dict[str, Any]:
    """Commits authored in the period whose message names no Jira issue (§19), and how many of
    the period's commits do."""
    _span(start, end)
    _project(principal, business_project_id)
    out = tasks.unlinked_commits(
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


# -- §20: commits linked by hand, the issue moved on Jira (user 2026-11-10) ---------------------

KeyPath = Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9_]{1,19}-\d{1,9}$")]
ShaPath = Annotated[str, Path(pattern=r"^[0-9a-fA-F]{7,40}$")]


def _issue(session: Session, principal: CurrentPrincipal, key: str) -> Issue:
    issue = tasks.find_issue(session, principal, key.upper())
    if issue is None:
        raise HTTPException(404, "issue not found")
    return issue


@router.get("/jira/tasks/{key}/commit-candidates")
def commit_candidates(
    key: KeyPath,
    principal: CurrentPrincipal,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    business_project_id: int | None = None,
    user_id: int | None = None,
    filters: bool = False,
) -> dict[str, Any]:
    """Reviewed commits of the issue's dự án one may link: by dự án, author, commit id (sha
    prefix) or message text. `filters=true`: only the dự án and authors to choose from."""
    issue = _issue(session, principal, key)
    if filters:
        return {"filters": tasks.candidate_filters(session, principal, issue)}
    return {"items": tasks.candidates(session, principal, issue, q, business_project_id, user_id)}


class LinkIn(BaseModel):
    sha: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-fA-F]{7,40}$")]
    # fixes: this commit fixes the issue (counts in "Bug đã có bản sửa"); refs: only related.
    kind: Literal["fixes", "refs"] = "fixes"


@router.post("/jira/tasks/{key}/commits", status_code=201)
def link_commit(
    key: KeyPath, body: LinkIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    """Link a reviewed commit to the issue by hand (several per issue). Managers of the issue's
    dự án, jira.admin and admins link any; anyone else only their own commits."""
    issue = _issue(session, principal, key)
    sha = body.sha.lower()
    found = session.scalars(
        select(Commit).where(Commit.sha.startswith(sha), tasks.commit_scope(principal)).limit(2)
    ).all()
    if not found:
        raise HTTPException(404, "không thấy commit này trong các repo bạn xem được")
    if len(found) > 1:
        raise HTTPException(409, "nhiều commit trùng đoạn mã này: nhập SHA dài hơn")
    commit = found[0]
    reviewed = session.scalar(
        select(Review.id).where(Review.commit_sha == commit.sha, Review.status == "done").limit(1)
    )
    if reviewed is None:
        raise HTTPException(409, "commit này chưa được review: yêu cầu review trước khi liên kết")
    bps = tasks.issue_projects(session, issue)
    if not tasks.may_link(principal, bps, tasks.commit_author(session, commit.sha)):
        raise HTTPException(403, "chỉ quản lý dự án hoặc tác giả commit mới liên kết được")
    if bps:  # only a commit of the issue's dự án (as the search offers)
        commit_bp = session.scalar(
            select(Project.business_project_id).where(Project.id == commit.project_id)
        )
        if commit_bp not in bps:
            raise HTTPException(409, "commit này không thuộc dự án của issue")
    stmt = insert(IssueCommitLink).values(
        issue_id=issue.id, sha=commit.sha, kind=body.kind, manual=True, linked_by=principal.user_id
    )
    # A link read from the message stays the message's (not turned into a manual one).
    done = session.execute(
        stmt.on_conflict_do_update(
            index_elements=["issue_id", "sha"],
            set_={"kind": body.kind, "linked_by": principal.user_id},
            where=IssueCommitLink.manual.is_(True),
        ).returning(IssueCommitLink.sha)
    ).first()
    if done is None:
        raise HTTPException(409, "commit đã được liên kết từ message của nó")
    audit(
        session,
        principal.user_id,
        "jira.commit.link",
        f"issue:{issue.key}",
        sha=commit.sha,
        kind=body.kind,
    )
    session.commit()
    return {"key": issue.key, "sha": commit.sha, "kind": body.kind}


@router.delete("/jira/tasks/{key}/commits/{sha}", status_code=204)
def unlink_commit(
    key: KeyPath, sha: ShaPath, principal: CurrentPrincipal, session: SessionDep
) -> None:
    """Remove a link made by hand (a link read from the message goes with the message)."""
    issue = _issue(session, principal, key)
    links = session.scalars(
        select(IssueCommitLink)
        .where(IssueCommitLink.issue_id == issue.id, IssueCommitLink.sha.startswith(sha.lower()))
        .limit(2)
    ).all()
    if not links:
        raise HTTPException(404, "link not found")
    if len(links) > 1:
        raise HTTPException(409, "nhiều commit trùng đoạn mã này: dùng SHA đầy đủ")
    link = links[0]
    if not link.manual:
        raise HTTPException(409, "liên kết này đọc từ message của commit, không gỡ tay được")
    bps = tasks.issue_projects(session, issue)
    mine = link.linked_by == principal.user_id
    if not (mine or tasks.may_move(principal, bps)):
        raise HTTPException(403, "chỉ người đã liên kết hoặc quản lý dự án mới gỡ được")
    session.delete(link)
    audit(session, principal.user_id, "jira.commit.unlink", f"issue:{issue.key}", sha=link.sha)
    session.commit()


def _jira(session: Session, settings: Settings) -> JiraClient:
    if not effective_base_url(session, settings):
        raise HTTPException(409, "Jira chưa được kết nối")
    return JiraClient.from_connection(connection(session, settings))


@router.get("/jira/tasks/{key}/transitions")
def transitions(
    key: KeyPath,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """The statuses the issue can move to now (Jira's workflow for the service account)."""
    issue = _issue(session, principal, key)
    if not tasks.may_move(principal, tasks.issue_projects(session, issue)):
        raise HTTPException(403, "chỉ manager của dự án hoặc admin chuyển trạng thái được")
    try:
        with _jira(session, settings) as client:
            moves = client.transitions(str(issue.key))
    except JiraError as exc:
        raise HTTPException(502, f"Jira: {exc}") from None
    return {
        "items": [
            {
                "id": str(t.get("id")),
                "name": t.get("name"),
                "to": (t.get("to") or {}).get("name"),
                "done": ((t.get("to") or {}).get("statusCategory") or {}).get("key") == "done",
            }
            for t in moves
        ]
    }


class MoveIn(BaseModel):
    transition_id: Annotated[str, StringConstraints(pattern=r"^\d{1,10}$")]
    comment: Annotated[str, StringConstraints(max_length=2000)] | None = None


@router.post("/jira/tasks/{key}/transition")
def move_issue(
    key: KeyPath,
    body: MoveIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Move the issue on Jira (with a comment saying who did it from CodePulse), then read its
    status back so Task Jira shows it at once."""
    issue = _issue(session, principal, key)
    if not tasks.may_move(principal, tasks.issue_projects(session, issue)):
        raise HTTPException(403, "chỉ manager của dự án hoặc admin chuyển trạng thái được")
    who = session.get(User, principal.user_id)
    name = (who.full_name or who.primary_email) if who else f"#{principal.user_id}"
    note = (body.comment or "").strip()
    comment = (
        f"[CodePulse] {name}: {note}" if note else f"[CodePulse] Chuyển trạng thái bởi {name}."
    )
    try:
        with _jira(session, settings) as client:
            client.transition(str(issue.key), body.transition_id, comment)
            fields = client.issue_status(str(issue.key))
    except JiraError as exc:
        hint = (
            " (tài khoản dịch vụ Jira cần quyền Transition issues trên dự án này)"
            if exc.status_code in (400, 401, 403)
            else ""
        )
        raise HTTPException(502, f"Jira không chuyển được: {exc}{hint}") from None
    status = fields.get("status") or {}
    done = (status.get("statusCategory") or {}).get("key") == "done"
    issue.state = "closed" if done else "opened"
    if done:
        resolved = fields.get("resolutiondate")
        try:
            issue.closed_at_src = datetime.fromisoformat(str(resolved).replace("Z", "+00:00"))
        except ValueError:
            issue.closed_at_src = datetime.now(UTC)
    else:
        issue.closed_at_src = None
    audit(
        session,
        principal.user_id,
        "jira.issue.transition",
        f"issue:{issue.key}",
        transition=body.transition_id,
        status=status.get("name"),
    )
    session.commit()
    return {"key": issue.key, "status": status.get("name"), "state": issue.state}
