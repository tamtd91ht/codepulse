"""Closing critical/high findings a later change fixed (outside the spec, requested by the user
2026-10-31; app/review/resolve.py, app/models/review.FindingClosure).

The manual flow:
1. the author of the change that brought the finding (only them, as for a dispute) asks to close
   it with the commits that fix it (hashes or GitLab links), an argument (why it does not hold
   where it matters, at least MIN_ARGUMENT characters), or both: `pending`, nothing is paid yet;
2. a manager of a team the author is in, or an admin, refuses it (`rejected`, with a note) or
   presses "Chạy review": `checking`, a worker checks it (app/workers/closure_jobs). Commits that
   fix it close the finding (`fixed`); an argument the current code proves becomes `proposed`,
   which the manager or admin confirms ("Xác nhận chấp nhận rủi ro": `accepted_risk`) or
   refuses; otherwise it says why not. A failed check can be run again.
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
import secrets
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import ColumnElement, String, and_, cast, func, or_, select, true, update
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
from app.review.masking import looks_secret
from app.review.resolve import close_finding, mark_fixed
from app.review.status import SEVERE

router = APIRouter(tags=["closures"])
SessionDep = Annotated[Session, Depends(get_session)]
SHA = re.compile(r"^[0-9a-f]{7,40}$")
# A GitLab commit link: …/-/commit/<sha>, …/commit/<sha>, also with a #fragment or ?query.
COMMIT_LINK = re.compile(r"/commits?/([0-9a-fA-F]{7,40})(?:[/?#].*)?$")
MAX_COMMITS = 5
# A request waiting for a manager's or an admin's decision.
TO_DECIDE = ("pending", "failed", "proposed")
MIN_ARGUMENT = 50  # an argument shorter than this says too little to be checked
MAX_ARGUMENT = 4000
MAX_REQUESTS_PER_DAY = 20  # per author: each one may cost a check
CHECK_STALE = timedelta(minutes=30)  # a check begun this long ago was lost (worker killed)
QUEUE_STALE = timedelta(hours=3)  # "Chạy review" this long ago and never begun: job lost
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
    commits: list[str] = Field(default_factory=list, max_length=MAX_COMMITS)
    argument: str | None = Field(default=None, max_length=MAX_ARGUMENT)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("commits")
    @classmethod
    def _commits(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for raw in value:
            if raw.strip() and (sha := parse_commit(raw)) not in out:
                out.append(sha)
        return out

    @field_validator("argument")
    @classmethod
    def _argument(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        if text and looks_secret(text):
            raise ValueError(
                "phản biện có vẻ chứa khoá/mật khẩu thật: xoá giá trị đi, chỉ cần nói nó nằm ở "
                "file nào và được đọc từ đâu"
            )
        if text and len(text) < MIN_ARGUMENT:
            raise ValueError(
                f"phản biện cần ít nhất {MIN_ARGUMENT} ký tự (đang có {len(text)}): nói rõ vì sao "
                "finding không đúng ở môi trường thật và file nào chứng minh"
            )
        return text or None

    @model_validator(mode="after")
    def _something(self) -> "CloseRequestIn":
        if not self.commits and not self.argument:
            raise ValueError(
                f"cần ít nhất một commit đã sửa, hoặc phản biện từ {MIN_ARGUMENT} ký tự"
            )
        return self


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
    argument: str | None
    closed_as: str | None
    reason: str | None
    evidence: str | None
    cost_usd: float | None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None


# -- who -------------------------------------------------------------------------------------------


def _managed_members(session: Session, principal: Principal) -> set[int]:
    """People in a team this person manages (sub-teams included) today, counted as
    app/api/security.load_principal counts memberships."""
    if not principal.managed_teams:
        return set()
    today = datetime.now(UTC).date()
    return set(
        session.scalars(
            select(TeamMember.user_id).where(
                TeamMember.team_id.in_(principal.managed_teams),
                TeamMember.joined_at <= today,
                or_(TeamMember.left_at.is_(None), TeamMember.left_at > today),
            )
        )
    )


def _may_decide(
    principal: Principal, managed: set[int], author_id: int | None, bp_id: int | None
) -> bool:
    """An admin, or a manager of a team the author is in who is granted the dự án (docs/specs/09:
    one without a grant does not exist for them); never the author themselves."""
    if author_id == principal.user_id:
        return False
    if principal.is_admin:
        return True
    return author_id is not None and author_id in managed and principal.can_view_project(bp_id)


def _finding(
    session: Session, principal: Principal, finding_id: int
) -> tuple[ReviewFinding, Review]:
    finding = session.get(ReviewFinding, finding_id)
    if finding is None or finding.review_id is None:
        raise HTTPException(404, "finding not found")
    review = _visible_review(session, principal, finding.review_id)  # 404/403 as on its page
    return finding, review


def _sweep(session: Session, finding_id: int | None = None) -> None:
    """Checks that will not end become `failed` (and can be run again): begun by a worker longer
    than CHECK_STALE ago (it died), or never begun QUEUE_STALE after "Chạy review" (the job was
    lost). A late job of the old run can no longer claim it: a re-run gets a new token."""
    now = datetime.now(UTC)
    query = (
        update(FindingClosure)
        .where(
            FindingClosure.status == "checking",
            or_(
                FindingClosure.started_at < now - CHECK_STALE,
                and_(
                    FindingClosure.started_at.is_(None),
                    FindingClosure.updated_at < now - QUEUE_STALE,
                ),
            ),
        )
        .values(status="failed", reason="Lần kiểm bị gián đoạn (worker dừng); chạy lại.")
        .execution_options(synchronize_session=False)
    )
    if finding_id is not None:
        query = query.where(FindingClosure.finding_id == finding_id)
    session.execute(query)


def _in_flight(session: Session, finding_id: int) -> FindingClosure | None:
    """The request being decided or checked (a lost check is failed first)."""
    _sweep(session, finding_id)
    return session.scalars(
        select(FindingClosure).where(
            FindingClosure.finding_id == finding_id, FindingClosure.status.in_(CLOSURE_OPEN)
        )
    ).first()


def _move(session: Session, closure: FindingClosure, frm: tuple[str, ...], **values: Any) -> bool:
    """One state change, only from the states `frm`: two people pressing at once, or a cancel
    racing a run, change the row once; the other gets False (409)."""
    result = session.execute(
        update(FindingClosure)
        .where(FindingClosure.id == closure.id, FindingClosure.status.in_(frm))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if not getattr(result, "rowcount", 0):
        return False
    session.refresh(closure)
    return True


def _request(session: Session, closure_id: int) -> FindingClosure:
    closure = session.get(FindingClosure, closure_id)
    if closure is None or closure.kind != "request":
        raise HTTPException(404, "close request not found")
    return closure


def _owner(session: Session, closure: FindingClosure) -> tuple[int | None, int | None]:
    """(author of the change, its repo's dự án) of the request's finding."""
    repo = _repo_of_review()
    row = session.execute(
        select(Review.author_user_id, Project.business_project_id)
        .join(ReviewFinding, ReviewFinding.review_id == Review.id)
        .outerjoin(repo, repo.c.review_id == Review.id)
        .outerjoin(Project, Project.id == repo.c.project_id)
        .where(ReviewFinding.id == closure.finding_id)
    ).first()
    return (row[0], row[1]) if row else (None, None)


def _decider(session: Session, principal: Principal, closure: FindingClosure) -> None:
    author, bp_id = _owner(session, closure)
    if not _may_decide(principal, _managed_members(session, principal), author, bp_id):
        raise HTTPException(403, "chỉ manager của team tác giả hoặc admin được duyệt yêu cầu này")


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
        argument=body.argument,
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
        argument=bool(body.argument),
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
    if not _move(session, closure, ("pending",), status="cancelled", decided_at=datetime.now(UTC)):
        raise HTTPException(409, "yêu cầu đã được duyệt hoặc đang được kiểm; tải lại trang")
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
    _decider(session, principal, closure)
    _sweep(session, closure.finding_id)  # a lost check may be run again
    session.refresh(closure)
    finding = session.get(ReviewFinding, closure.finding_id)
    if finding is None or finding.status != "open":
        raise HTTPException(409, "finding không còn mở")
    before = {
        "status": closure.status,
        "reason": closure.reason,
        "decided_at": closure.decided_at,
        "decided_by": closure.decided_by,
    }
    token = secrets.token_hex(8)
    try:
        moved = _move(
            session,
            closure,
            ("pending", "failed"),
            status="checking",
            run_token=token,
            started_at=None,
            decided_by=principal.user_id,
            decided_at=None,
            reason=None,
        )
    except IntegrityError:  # another request of the same finding is in flight
        raise HTTPException(409, "finding này đang có yêu cầu khác") from None
    if not moved:
        raise HTTPException(409, "yêu cầu vừa được người khác duyệt; tải lại trang")
    audit(session, principal.user_id, "finding.close_request.run", f"finding:{closure.finding_id}")
    session.commit()
    try:
        queue.enqueue(
            "app.workers.closure_jobs.close_request_job",
            closure.id,
            token,
            job_id=f"close-request-{closure.id}-{token}",
            job_timeout=600,
        )
    except Exception:
        # Not queued: back to where it was (this run only), or it would wait for a check
        # that never comes.
        session.execute(
            update(FindingClosure)
            .where(FindingClosure.id == closure.id, FindingClosure.run_token == token)
            .values(**before, run_token=None)
        )
        session.commit()
        raise HTTPException(503, "không đưa được vào hàng đợi kiểm; thử lại") from None
    return closure


@router.post("/close-requests/{closure_id}/reject", response_model=ClosureOut)
def reject_request(
    closure_id: int, body: NoteIn, principal: CurrentPrincipal, session: SessionDep
) -> FindingClosure:
    closure = _request(session, closure_id)
    _decider(session, principal, closure)
    moved = _move(
        session,
        closure,
        ("pending", "failed", "proposed"),
        status="rejected",
        decided_by=principal.user_id,
        decided_at=datetime.now(UTC),
        reason=f"Từ chối: {body.note.strip()}",
    )
    if not moved:
        raise HTTPException(409, "yêu cầu đang được kiểm hoặc đã có kết quả; tải lại trang")
    audit(
        session, principal.user_id, "finding.close_request.reject", f"finding:{closure.finding_id}"
    )
    session.commit()
    return closure


@router.post("/close-requests/{closure_id}/confirm", response_model=ClosureOut)
def confirm_proposal(
    closure_id: int, principal: CurrentPrincipal, session: SessionDep
) -> FindingClosure:
    """ "Xác nhận chấp nhận rủi ro": the check found the argument holds; a manager or an admin
    who read its reason and evidence closes the finding as `accepted_risk`."""
    closure = _request(session, closure_id)
    _decider(session, principal, closure)
    finding = session.get(ReviewFinding, closure.finding_id)
    if finding is None:
        raise HTTPException(404, "finding not found")
    now = datetime.now(UTC)
    if not _move(
        session,
        closure,
        ("proposed",),
        status="closed",
        decided_by=principal.user_id,
        decided_at=now,
    ):
        raise HTTPException(409, "đề xuất đã được xử lý; tải lại trang")
    if not mark_fixed(session, finding, "accepted_risk"):
        session.rollback()
        raise HTTPException(409, f"finding đang ở trạng thái {finding.status}, không còn mở")
    audit(
        session,
        principal.user_id,
        "finding.close_request.confirm",
        f"finding:{closure.finding_id}",
        closure_id=closure.id,
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
    if waiting is not None and not _move(  # a waiting request: the admin's decision answers it
        session,
        waiting,
        ("pending", "proposed"),
        status="rejected",
        decided_by=principal.user_id,
        decided_at=datetime.now(UTC),
        reason="Admin đã đóng thủ công finding này.",
    ):
        raise HTTPException(409, "yêu cầu đóng vừa được chạy; chờ kết quả")
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
    if finding.status not in ("fixed", "accepted_risk"):
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
    if any(row.status == "checking" for row in rows):
        _sweep(session)
        session.commit()
        for row in rows:
            session.refresh(row)
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


def _visible(session: Session, principal: Principal, repo: Any) -> ColumnElement[bool]:
    """Whose closings this person sees: everyone's (admin), their own work anywhere, and that of
    the teams they manage in the dự án they are granted (they decide those requests)."""
    if principal.is_admin:
        return true()
    own = Review.author_user_id == principal.user_id
    managed = _managed_members(session, principal)
    granted = principal.visible_projects() or set()
    if not managed or not granted:
        return own
    in_granted = repo.c.project_id.in_(
        select(Project.id).where(Project.business_project_id.in_(granted))
    )
    return or_(own, and_(Review.author_user_id.in_(managed), in_granted))


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
    _sweep(session)  # lost checks show as such (and can be run again) right away
    session.commit()
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
        .where(_visible(session, principal, repo))
    )
    if view == "todo":
        query = query.where(
            FindingClosure.kind == "request",
            FindingClosure.status.in_(TO_DECIDE),
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
        text = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{text}%"
        query = query.where(
            or_(
                func.lower(ReviewFinding.title).like(like, escape="\\"),
                func.lower(ReviewFinding.file_path).like(like, escape="\\"),
                func.lower(cast(FindingClosure.shas, String)).like(like, escape="\\"),
                func.lower(FindingClosure.argument).like(like, escape="\\"),
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
            and closure.status in TO_DECIDE
            and _may_decide(
                principal,
                managed,
                review.author_user_id,
                project.business_project_id if project else None,
            )
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
    _sweep(session)
    session.commit()
    repo = _repo_of_review()
    base = (
        select(FindingClosure.id)
        .join(ReviewFinding, ReviewFinding.id == FindingClosure.finding_id)
        .join(Review, Review.id == ReviewFinding.review_id)
        .outerjoin(repo, repo.c.review_id == Review.id)
        .where(_visible(session, principal, repo))
    )
    authors = session.execute(
        select(User.id, User.full_name, User.primary_email)
        .where(User.id.in_(base.with_only_columns(Review.author_user_id)))
        .order_by(User.full_name)
    ).all()
    repos = session.execute(
        select(Project.id, Project.path_with_namespace, BusinessProject.id, BusinessProject.name)
        .outerjoin(BusinessProject, BusinessProject.id == Project.business_project_id)
        .where(Project.id.in_(base.with_only_columns(repo.c.project_id)))
        .order_by(Project.path_with_namespace)
    ).all()
    managed = _managed_members(session, principal)
    todo = select(func.count()).select_from(
        base.where(
            FindingClosure.kind == "request",
            FindingClosure.status.in_(TO_DECIDE),
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
