"""Closing critical/high findings a later change fixed (outside the spec, requested by the user
2026-10-31; app/review/resolve.py, app/models/review.FindingClosure).

The manual flow:
1. the author of the change that brought the finding (only them, as for a dispute) asks to close
   it, naming the commits that fix it (hashes or GitLab links): `pending`, nothing is paid yet;
2. a manager of a team the author is in, or an admin, refuses it (`rejected`, with a note) or
   presses "Chạy review": `checking`, a worker checks the commits (app/workers/closure_jobs) and
   closes the finding or says why not. A failed check can be run again.
Admins may also close or reopen a finding by hand, a note being mandatory. Automatic closings
after a review are recorded in the same table.

Closing never changes a score: the finding keeps its severity and still counts. That is the
difference with a dispute, which a lead accepts to take a wrong finding out of the score; a
finding is in one of the two flows at a time.

GET /close-requests is the "Đóng finding" page: requests to decide, and the whole history with
filters (status, kind, severity, dự án, repo, member, period, text).
"""

import base64
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from rq import Retry
from sqlalchemy import ColumnElement, String, cast, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.routers.reviews import _visible_review
from app.api.schemas import ORM
from app.api.security import AdminPrincipal, CurrentPrincipal, Principal
from app.models import (
    BusinessProject,
    Commit,
    FindingClosure,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    TeamMember,
    User,
)
from app.models.review import CLOSURE_OPEN
from app.review.resolve import close_finding
from app.review.status import SEVERE

router = APIRouter(tags=["closures"])
SessionDep = Annotated[Session, Depends(get_session)]
SHA = re.compile(r"^[0-9a-f]{7,40}$")
# A GitLab commit link: …/-/commit/<sha>, …/commit/<sha>, also with a #fragment or ?query.
COMMIT_LINK = re.compile(r"/commits?/([0-9a-fA-F]{7,40})(?:[/?#].*)?$")
MAX_COMMITS = 5
MAX_REQUESTS_PER_DAY = 20  # per author: each one may cost a check
CHECK_STALE = timedelta(minutes=30)  # a check longer than this was lost (worker killed)
PAGE = 50


def parse_commit(raw: str) -> str:
    """A hash, or the hash out of a GitLab commit link; ValueError otherwise."""
    text = raw.strip()
    if match := COMMIT_LINK.search(text):
        return match.group(1).lower()
    if SHA.match(text.lower()):
        return text.lower()
    raise ValueError(f"không phải mã hay link commit: {text[:80]}")


class CloseRequestIn(BaseModel):
    commits: list[str] = Field(min_length=1, max_length=MAX_COMMITS)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("commits")
    @classmethod
    def _commits(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for raw in value:
            if raw.strip() and (sha := parse_commit(raw)) not in out:
                out.append(sha)
        if not out:
            raise ValueError("cần ít nhất một commit")
        return out


class NoteIn(BaseModel):
    note: str = Field(min_length=5, max_length=1000)


class ClosureOut(ORM):
    id: int
    finding_id: int
    kind: str
    status: str
    shas: list[str]
    review_id: int | None
    requested_by: int | None
    decided_by: int | None
    note: str | None
    reason: str | None
    evidence: str | None
    cost_usd: float | None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None


# -- who -------------------------------------------------------------------------------------------


def _managed_members(session: Session, principal: Principal) -> set[int]:
    """People currently in a team this person manages (sub-teams included)."""
    if not principal.managed_teams:
        return set()
    return set(
        session.scalars(
            select(TeamMember.user_id).where(
                TeamMember.team_id.in_(principal.managed_teams), TeamMember.left_at.is_(None)
            )
        )
    )


def _may_decide(session: Session, principal: Principal, author_id: int | None) -> bool:
    """An admin, or a manager of a team the author is in; never the author themselves."""
    if author_id is None or author_id == principal.user_id:
        return principal.is_admin and author_id != principal.user_id
    return principal.is_admin or author_id in _managed_members(session, principal)


def _finding(
    session: Session, principal: Principal, finding_id: int
) -> tuple[ReviewFinding, Review]:
    finding = session.get(ReviewFinding, finding_id)
    if finding is None or finding.review_id is None:
        raise HTTPException(404, "finding not found")
    review = _visible_review(session, principal, finding.review_id)  # 404/403 as on its page
    return finding, review


def _in_flight(session: Session, finding_id: int) -> FindingClosure | None:
    """The request being decided or checked; a check lost by the worker is failed first."""
    row = session.scalars(
        select(FindingClosure).where(
            FindingClosure.finding_id == finding_id, FindingClosure.status.in_(CLOSURE_OPEN)
        )
    ).first()
    if (
        row is not None
        and row.status == "checking"
        and row.updated_at < datetime.now(UTC) - CHECK_STALE
    ):
        row.status = "failed"
        row.reason = "Lần kiểm bị gián đoạn (worker dừng); chạy lại."
        session.flush()
        return None
    return row


def _request(session: Session, closure_id: int) -> FindingClosure:
    closure = session.get(FindingClosure, closure_id)
    if closure is None or closure.kind != "request":
        raise HTTPException(404, "close request not found")
    return closure


def _author_of(session: Session, closure: FindingClosure) -> int | None:
    return session.scalar(
        select(Review.author_user_id)
        .join(ReviewFinding, ReviewFinding.review_id == Review.id)
        .where(ReviewFinding.id == closure.finding_id)
    )


# -- the author ------------------------------------------------------------------------------------


@router.post("/findings/{finding_id}/close-requests", response_model=ClosureOut, status_code=201)
def request_close(
    finding_id: int, body: CloseRequestIn, principal: CurrentPrincipal, session: SessionDep
) -> FindingClosure:
    finding, review = _finding(session, principal, finding_id)
    if review.author_user_id != principal.user_id:
        raise HTTPException(403, "chỉ tác giả của thay đổi mới được yêu cầu đóng finding của mình")
    if finding.severity not in SEVERE:
        raise HTTPException(422, "chỉ yêu cầu đóng được finding critical/high")
    if finding.status != "open":
        raise HTTPException(409, f"finding đang ở trạng thái {finding.status}")
    if _in_flight(session, finding.id) is not None:
        raise HTTPException(409, "finding này đã có một yêu cầu đóng đang chờ")
    today = session.scalar(
        select(func.count()).where(
            FindingClosure.requested_by == principal.user_id,
            FindingClosure.kind == "request",
            FindingClosure.created_at >= datetime.now(UTC) - timedelta(days=1),
        )
    )
    if (today or 0) >= MAX_REQUESTS_PER_DAY:
        raise HTTPException(429, f"tối đa {MAX_REQUESTS_PER_DAY} yêu cầu đóng mỗi ngày")
    closure = FindingClosure(
        finding_id=finding.id,
        kind="request",
        status="pending",
        shas=body.commits,
        requested_by=principal.user_id,
        note=(body.note or "").strip() or None,
    )
    session.add(closure)
    try:
        session.flush()
    except IntegrityError:
        raise HTTPException(409, "finding này đã có một yêu cầu đóng đang chờ") from None
    audit(
        session,
        principal.user_id,
        "finding.close_request",
        f"finding:{finding.id}",
        closure_id=closure.id,
        shas=body.commits,
    )
    session.commit()
    return closure


@router.post("/close-requests/{closure_id}/cancel", response_model=ClosureOut)
def cancel_request(
    closure_id: int, principal: CurrentPrincipal, session: SessionDep
) -> FindingClosure:
    closure = _request(session, closure_id)
    if closure.requested_by != principal.user_id:
        raise HTTPException(403, "chỉ người gửi mới rút được yêu cầu")
    if closure.status != "pending":
        raise HTTPException(409, f"yêu cầu đang ở trạng thái {closure.status}")
    closure.status = "cancelled"
    closure.decided_at = datetime.now(UTC)
    audit(
        session, principal.user_id, "finding.close_request.cancel", f"finding:{closure.finding_id}"
    )
    session.commit()
    return closure


# -- the manager / admin ---------------------------------------------------------------------------


@router.post("/close-requests/{closure_id}/run", response_model=ClosureOut, status_code=202)
def run_request(
    closure_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    queue: Annotated[Any, Depends(get_queue)],
) -> FindingClosure:
    """ "Chạy review": check the commits now (a failed check may be run again)."""
    closure = _request(session, closure_id)
    if not _may_decide(session, principal, _author_of(session, closure)):
        raise HTTPException(403, "chỉ manager của team tác giả hoặc admin được duyệt yêu cầu này")
    if closure.status not in ("pending", "failed"):
        raise HTTPException(409, f"yêu cầu đang ở trạng thái {closure.status}")
    if closure.status == "failed":
        other = _in_flight(session, closure.finding_id)
        if other is not None and other.id != closure.id:
            raise HTTPException(409, "finding này đang có yêu cầu khác")
    finding = session.get(ReviewFinding, closure.finding_id)
    if finding is None or finding.status != "open":
        raise HTTPException(409, "finding không còn mở")
    previous = closure.status
    closure.status = "checking"
    closure.decided_by = principal.user_id
    closure.decided_at = None
    closure.reason = None
    try:
        session.flush()
    except IntegrityError:
        raise HTTPException(409, "finding này đang có yêu cầu khác") from None
    audit(session, principal.user_id, "finding.close_request.run", f"finding:{closure.finding_id}")
    session.commit()
    try:
        queue.enqueue(
            "app.workers.closure_jobs.close_request_job",
            closure.id,
            job_id=f"close-request-{closure.id}-{int(datetime.now(UTC).timestamp())}",
            job_timeout=600,
            retry=Retry(max=2, interval=[30, 120]),
        )
    except Exception:
        # Not queued: back to where it was, or it would wait for a check that never comes.
        closure.status = previous
        session.commit()
        raise HTTPException(503, "không đưa được vào hàng đợi kiểm; thử lại") from None
    return closure


@router.post("/close-requests/{closure_id}/reject", response_model=ClosureOut)
def reject_request(
    closure_id: int, body: NoteIn, principal: CurrentPrincipal, session: SessionDep
) -> FindingClosure:
    closure = _request(session, closure_id)
    if not _may_decide(session, principal, _author_of(session, closure)):
        raise HTTPException(403, "chỉ manager của team tác giả hoặc admin được duyệt yêu cầu này")
    if closure.status not in ("pending", "failed"):
        raise HTTPException(409, f"yêu cầu đang ở trạng thái {closure.status}")
    closure.status = "rejected"
    closure.decided_by = principal.user_id
    closure.decided_at = datetime.now(UTC)
    closure.reason = f"Từ chối: {body.note.strip()}"
    audit(
        session, principal.user_id, "finding.close_request.reject", f"finding:{closure.finding_id}"
    )
    session.commit()
    return closure


@router.post("/findings/{finding_id}/close", response_model=ClosureOut)
def close_by_admin(
    finding_id: int, body: NoteIn, principal: AdminPrincipal, session: SessionDep
) -> FindingClosure:
    finding, _ = _finding(session, principal, finding_id)
    if finding.status != "open":
        raise HTTPException(409, f"finding đang ở trạng thái {finding.status}")
    waiting = _in_flight(session, finding.id)
    if waiting is not None and waiting.status == "checking":
        raise HTTPException(409, "finding đang được kiểm; chờ kết quả")
    if waiting is not None:  # a pending request: the admin's decision answers it
        waiting.status = "rejected"
        waiting.decided_by = principal.user_id
        waiting.decided_at = datetime.now(UTC)
        waiting.reason = "Admin đã đóng thủ công finding này."
    closure = close_finding(
        session,
        finding,
        kind="admin",
        now=datetime.now(UTC),
        requested_by=principal.user_id,
        decided_by=principal.user_id,
        note=body.note.strip(),
    )
    if closure is None:
        raise HTTPException(409, "finding vừa đổi trạng thái; tải lại trang")
    audit(session, principal.user_id, "finding.close", f"finding:{finding.id}", note=body.note)
    session.commit()
    return closure


@router.post("/findings/{finding_id}/reopen", response_model=ClosureOut)
def reopen_by_admin(
    finding_id: int, body: NoteIn, principal: AdminPrincipal, session: SessionDep
) -> FindingClosure:
    finding, _ = _finding(session, principal, finding_id)
    if finding.status != "fixed":
        raise HTTPException(409, f"chỉ mở lại được finding đã đóng (đang {finding.status})")
    finding.status = "open"
    closure = FindingClosure(
        finding_id=finding.id,
        kind="reopen",
        status="reopened",
        requested_by=principal.user_id,
        decided_by=principal.user_id,
        note=body.note.strip(),
        decided_at=datetime.now(UTC),
    )
    session.add(closure)
    session.flush()
    audit(session, principal.user_id, "finding.reopen", f"finding:{finding.id}", note=body.note)
    session.commit()
    return closure


# -- reading ---------------------------------------------------------------------------------------


@router.get("/reviews/{review_id}/closures", response_model=list[ClosureOut])
def review_closures(
    review_id: int, principal: CurrentPrincipal, session: SessionDep
) -> list[FindingClosure]:
    """Every closing of this review's findings, newest first."""
    review = _visible_review(session, principal, review_id)
    rows = list(
        session.scalars(
            select(FindingClosure)
            .join(ReviewFinding, ReviewFinding.id == FindingClosure.finding_id)
            .where(ReviewFinding.review_id == review.id)
            .order_by(FindingClosure.id.desc())
        )
    )
    for row in rows:
        if row.status == "checking":
            _in_flight(session, row.finding_id)  # fails a lost check
    session.commit()
    return rows


def _repo_of_review() -> Any:
    """review id -> repo id, for MR and commit reviews alike."""
    mr = select(Review.id.label("review_id"), MergeRequest.project_id.label("project_id")).join(
        MergeRequest, MergeRequest.id == Review.mr_id
    )
    commit = select(Review.id.label("review_id"), Commit.project_id.label("project_id")).join(
        Commit, Commit.sha == Review.commit_sha
    )
    return mr.union_all(commit).subquery("review_repo")


def _visible(session: Session, principal: Principal) -> ColumnElement[bool]:
    """Whose closings this person sees: everyone's (admin), their own work, and that of the
    teams they manage (they decide those requests)."""
    if principal.is_admin:
        return true()
    people = {principal.user_id} | _managed_members(session, principal)
    return Review.author_user_id.in_(people)


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _cursor(closure_id: int) -> str:
    return base64.urlsafe_b64encode(str(closure_id).encode()).decode()


def _after(cursor: str | None) -> int | None:
    if not cursor:
        return None
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "invalid cursor") from None


@router.get("/close-requests")
def list_closures(
    principal: CurrentPrincipal,
    session: SessionDep,
    view: Literal["todo", "history"] = "history",
    status: Annotated[list[str] | None, Query()] = None,
    kind: Annotated[list[str] | None, Query()] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
    author_id: int | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """The "Đóng finding" page. `view=todo`: requests waiting for this person's decision (a
    manager's team, or every one for an admin). `history`: every closing they may see, newest
    first, with the filters."""
    repo = _repo_of_review()
    author = aliased(User)
    requester = aliased(User)
    decider = aliased(User)
    query = (
        select(
            FindingClosure,
            ReviewFinding,
            Review,
            Project,
            BusinessProject,
            author,
            requester,
            decider,
        )
        .join(ReviewFinding, ReviewFinding.id == FindingClosure.finding_id)
        .join(Review, Review.id == ReviewFinding.review_id)
        .outerjoin(repo, repo.c.review_id == Review.id)
        .outerjoin(Project, Project.id == repo.c.project_id)
        .outerjoin(BusinessProject, BusinessProject.id == Project.business_project_id)
        .outerjoin(author, author.id == Review.author_user_id)
        .outerjoin(requester, requester.id == FindingClosure.requested_by)
        .outerjoin(decider, decider.id == FindingClosure.decided_by)
        .where(_visible(session, principal))
    )
    if view == "todo":
        query = query.where(
            FindingClosure.kind == "request",
            FindingClosure.status.in_(("pending", "failed")),
            Review.author_user_id != principal.user_id,
        )
        if not principal.is_admin:
            query = query.where(Review.author_user_id.in_(_managed_members(session, principal)))
    if status:
        query = query.where(FindingClosure.status.in_(status))
    if kind:
        query = query.where(FindingClosure.kind.in_(kind))
    if severity:
        query = query.where(ReviewFinding.severity.in_(severity))
    if business_project_id is not None:
        query = query.where(Project.business_project_id == business_project_id)
    if project_id is not None:
        query = query.where(Project.id == project_id)
    if author_id is not None:
        query = query.where(Review.author_user_id == author_id)
    if date_from is not None:
        query = query.where(FindingClosure.created_at >= _day_start(date_from))
    if date_to is not None:
        query = query.where(FindingClosure.created_at < _day_start(date_to + timedelta(days=1)))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(ReviewFinding.title).like(like),
                func.lower(ReviewFinding.file_path).like(like),
                func.lower(cast(FindingClosure.shas, String)).like(like),
            )
        )
    if (after := _after(cursor)) is not None:
        query = query.where(FindingClosure.id < after)
    rows = session.execute(query.order_by(FindingClosure.id.desc()).limit(PAGE + 1)).tuples().all()
    managed = _managed_members(session, principal)
    items = []
    for closure, finding, review, project, bp, who, asker, judge in rows[:PAGE]:
        decides = (
            closure.kind == "request"
            and (principal.is_admin or review.author_user_id in managed)
            and review.author_user_id != principal.user_id
        )
        items.append(
            {
                **ClosureOut.model_validate(closure).model_dump(),
                "finding": {
                    "id": finding.id,
                    "title": finding.title,
                    "severity": finding.severity,
                    "category": finding.category,
                    "file_path": finding.file_path,
                    "line_start": finding.line_start,
                    "message": (finding.message or "")[:400] or None,
                    "status": finding.status,
                },
                "finding_review_id": review.id,
                "repo": {"id": project.id, "path": project.path_with_namespace}
                if project
                else None,
                "business_project": {"id": bp.id, "name": bp.name} if bp else None,
                "author": {"id": who.id, "name": who.full_name or who.primary_email}
                if who
                else None,
                "requested_by_name": (asker.full_name or asker.primary_email) if asker else None,
                "decided_by_name": (judge.full_name or judge.primary_email) if judge else None,
                "can_decide": decides,
                "can_cancel": closure.status == "pending"
                and closure.requested_by == principal.user_id,
                "can_open_review": principal.can_see_work(
                    review.author_user_id, project.business_project_id if project else None
                ),
            }
        )
    return {
        "items": items,
        "next_cursor": _cursor(rows[PAGE - 1][0].id) if len(rows) > PAGE else None,
    }


@router.get("/close-requests/facets")
def closure_facets(principal: CurrentPrincipal, session: SessionDep) -> dict[str, Any]:
    """The filter choices of the page (only what this person may see) and the to-do count."""
    repo = _repo_of_review()
    base = (
        select(FindingClosure.id)
        .join(ReviewFinding, ReviewFinding.id == FindingClosure.finding_id)
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(_visible(session, principal))
    )
    authors = session.execute(
        select(User.id, User.full_name, User.primary_email)
        .where(User.id.in_(base.with_only_columns(Review.author_user_id)))
        .order_by(User.full_name)
    ).all()
    repos = session.execute(
        select(Project.id, Project.path_with_namespace, BusinessProject.id, BusinessProject.name)
        .outerjoin(BusinessProject, BusinessProject.id == Project.business_project_id)
        .where(
            Project.id.in_(
                base.join(repo, repo.c.review_id == Review.id).with_only_columns(repo.c.project_id)
            )
        )
        .order_by(Project.path_with_namespace)
    ).all()
    managed = _managed_members(session, principal)
    todo = select(func.count()).select_from(
        base.where(
            FindingClosure.kind == "request",
            FindingClosure.status.in_(("pending", "failed")),
            Review.author_user_id != principal.user_id,
            *([] if principal.is_admin else [Review.author_user_id.in_(managed)]),
        ).subquery()
    )
    projects = {(bp_id, bp_name) for _, _, bp_id, bp_name in repos if bp_id is not None}
    return {
        "authors": [{"id": i, "name": n or e} for i, n, e in authors],
        "repos": [{"id": i, "path": p, "business_project_id": bp} for i, p, bp, _ in repos],
        "business_projects": [
            {"id": i, "name": n} for i, n in sorted(projects, key=lambda x: x[1])
        ],
        "todo": session.scalar(todo) or 0,
        "can_decide": principal.is_admin or bool(managed),
    }
