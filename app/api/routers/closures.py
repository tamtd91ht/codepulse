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
from sqlalchemy import (
    ColumnElement,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.admin_settings import close_requests_per_day
from app.api.access import (
    covers_work,
    review_repo,
    sees_work,
    team_reach,
    whole_projects,
    work_scope,
)
from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.routers.reviews import _visible_review
from app.api.schemas import ORM
from app.api.security import CurrentPrincipal, Principal, require
from app.config import Settings, get_settings
from app.models import (
    BusinessProject,
    Commit,
    FindingClosure,
    FindingDispute,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    User,
)
from app.models.review import CLOSURE_OPEN, FINDING_CLOSED, shown_status
from app.notifications import inbox
from app.permissions.catalog import BY_KEY
from app.review.masking import looks_secret
from app.review.resolve import close_finding, mark_fixed
from app.review.status import SEVERE, carry_decision, settle_open_requests
from app.scoring.rescore import rescore_copies, rescore_review
from app.workers.queues import review_queue

router = APIRouter(tags=["closures"])
SessionDep = Annotated[Session, Depends(get_session)]
SHA = re.compile(r"^[0-9a-f]{7,40}$")
# A GitLab commit link: …/-/commit/<sha>, …/commit/<sha>, also with a #fragment or ?query.
COMMIT_LINK = re.compile(r"/commits?/([0-9a-fA-F]{7,40})(?:[/?#].*)?$")
MAX_COMMITS = 5
# A request waiting for a manager's or an admin's decision (`unmet`: the check found it not good
# enough; they close the finding anyway, run it again or confirm the refusal).
TO_DECIDE = ("pending", "failed", "proposed", "unmet")
MIN_ARGUMENT = 50  # an argument shorter than this says too little to be checked
MAX_ARGUMENT = 4000
# Close requests per person in 24 hours: their own number or Admin > Cấu hình's default
# (app/admin_settings.close_requests_per_day, user 2026-11-06); each one may cost a check.
CHECK_STALE = timedelta(minutes=30)  # a check begun this long ago was lost (worker killed)
QUEUE_STALE = timedelta(hours=3)  # "Chạy review" this long ago and never begun: job lost
PAGE = 50
ViewPrincipal = Annotated[Principal, require("review.view")]
RequestPrincipal = Annotated[Principal, require("finding.close_request")]
DeciderPrincipal = Annotated[Principal, require("finding.close_decide")]
ManualPrincipal = Annotated[Principal, require("finding.close_manual")]


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


# Closing by an argument or a note, with no commit (user request 2026-11-03): the finding is
# lowered to medium or low and re-scored, or it was wrong (false_positive, out of the score).
# Closing by commits keeps its severity and its score.
Settle = Literal["medium", "low", "false_positive"]
RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class ConfirmIn(BaseModel):
    """What the confirmed argument makes of the finding; default: the check's suggestion."""

    outcome: Settle | None = None
    note: str | None = Field(default=None, max_length=1000)


class OverrideIn(BaseModel):
    """Closing a request the check found not good enough: by its commits (`fixed`, severity
    kept), or accepting the risk at medium/low, or the finding was wrong; why, always."""

    note: str = Field(min_length=5, max_length=1000)
    outcome: Literal["fixed", "medium", "low", "false_positive"]


# How the reason of an `unmet` request goes on once a person weighed it (the page reads these).
CHECKED = "Kết quả kiểm:"
OVERRIDDEN = "Người duyệt vẫn đóng:"


class AdminCloseIn(BaseModel):
    """An admin's close: with the fixing commits (kept as is), or accepting the risk with the
    severity it really has (or the finding was wrong)."""

    note: str = Field(min_length=5, max_length=1000)
    commits: list[str] = Field(default_factory=list, max_length=MAX_COMMITS)
    outcome: Settle | None = None

    @field_validator("commits")
    @classmethod
    def _commits(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for raw in value:
            if raw.strip() and (sha := parse_commit(raw)) not in out:
                out.append(sha)
        return out


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
    severity_before: str | None = None
    severity_after: str | None = None
    suggested_severity: str | None = None
    # docs/specs/26 §8: "web" or "mcp" (Claude Code), and what the MCP client sent with it.
    via: str = "web"
    meta: dict[str, Any] | None = None
    # What the viewer may do with it (the review page's buttons).
    can_decide: bool = False
    can_cancel: bool = False
    # May lower a critical straight to low (a manager of the author's team, or an admin).
    can_lower_far: bool = False
    finding_severity: str | None = None


# -- who -------------------------------------------------------------------------------------------


def _managed_members(principal: Principal) -> set[int]:
    """People whose requests this person decides (`finding.close_decide`, docs/specs/21): in a
    team they manage (sub-teams included) or lead (the same people who decide disputes, user
    request 2026-11-03). Admins: everyone, see `_may_decide`."""
    return team_reach(principal, "finding.close_decide")


def _decides_where(principal: Principal) -> list[ColumnElement[bool]]:
    """`_may_decide` as filters on `reviews` (the "Chờ duyệt" tab and its count): every scope
    of `finding.close_decide` (team, a dự án one manages, every dự án one sees), not only the
    teams one leads or manages, and never one's own work."""
    where: list[ColumnElement[bool]] = [Review.author_user_id != principal.user_id]
    scope = work_scope(principal, "finding.close_decide")
    if scope is not None:
        where.append(scope)
    return where


def _may_decide(principal: Principal, author_id: int | None, bp_id: int | None) -> bool:
    """`finding.close_decide` over the author's work in a dự án one is granted (docs/specs/09:
    one without a grant does not exist for them); never the author themselves (docs/specs/21
    §3, whatever the permission sets)."""
    if author_id == principal.user_id:
        return False
    return covers_work(principal, "finding.close_decide", author_id, bp_id)


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
    if not _may_decide(principal, author, bp_id):
        raise HTTPException(
            403, "chỉ lead, manager của team tác giả hoặc admin được duyệt yêu cầu này"
        )


def _lowers_far(principal: Principal, author_id: int | None, bp_id: int | None) -> bool:
    """`finding.lower_far`, lowering a critical straight to low: a manager of the author's team
    or an admin (a lead may lower it one step less: the team's own score is at stake)."""
    return principal.is_admin or (
        author_id is not None
        and author_id != principal.user_id
        and covers_work(principal, "finding.lower_far", author_id, bp_id)
    )


def _default_settle(severity: str, suggested: str | None, far: bool) -> str:
    """With no choice made: the check's suggestion when this decider may use it, else medium
    when it lowers the finding, else low (as the dashboard's default)."""
    now = RANK.get(severity, 0)

    def ok(to: str) -> bool:
        return RANK[to] <= now and not (severity == "critical" and to == "low" and not far)

    if suggested in ("medium", "low") and ok(suggested):
        return suggested
    return "medium" if ok("medium") else "low"


def _check_settle(finding: ReviewFinding, outcome: str, note: str | None, far: bool) -> None:
    if outcome == "false_positive":
        return
    now, to = RANK.get(finding.severity, 0), RANK[outcome]
    if to > now:
        raise HTTPException(422, f"không nâng mức: finding đang là {finding.severity}")
    if finding.severity == "critical" and outcome == "low":
        if not far:
            raise HTTPException(403, "chỉ manager hoặc admin được hạ critical xuống low")
        if not (note or "").strip() or len((note or "").strip()) < 5:
            raise HTTPException(422, "hạ critical xuống low cần ghi lý do")


def _settle(
    session: Session,
    finding: ReviewFinding,
    closure: FindingClosure,
    outcome: str,
    settings: Settings,
) -> bool:
    """open -> accepted_risk at the lowered severity, or -> false_positive; in one conditional
    UPDATE (a dispute or another close may have moved it meanwhile), then re-score the review
    and its week and month. False when the finding was no longer open."""
    before = finding.severity
    wrong = outcome == "false_positive"
    values: dict[str, Any] = {"status": "false_positive" if wrong else "accepted_risk"}
    if not wrong:
        values["severity"] = outcome
    moved = session.execute(
        update(ReviewFinding)
        .where(ReviewFinding.id == finding.id, ReviewFinding.status == "open")
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if not getattr(moved, "rowcount", 0):
        return False
    session.refresh(finding)
    closure.closed_as = "false_positive" if wrong else "accepted_risk"
    closure.severity_before = before
    closure.severity_after = None if wrong else outcome
    session.flush()
    review = session.get(Review, finding.review_id) if finding.review_id else None
    if review is not None:
        rescore_review(session, review, settings)
    # The same finding re-reported by another review of the MR / commit takes the decision.
    rescore_copies(session, carry_decision(session, finding), settings)
    return True


# -- the author ------------------------------------------------------------------------------------


@router.post("/findings/{finding_id}/close-requests", response_model=ClosureOut, status_code=201)
def request_close(
    finding_id: int, body: CloseRequestIn, principal: RequestPrincipal, session: SessionDep
) -> FindingClosure:
    closure = create_close_request(
        session, principal, finding_id, body.commits, body.argument, body.note
    )
    session.commit()
    return closure


def check_close_request(
    session: Session, principal: Principal, finding_id: int
) -> tuple[ReviewFinding, Review]:
    """Every refusal of a close request that needs nothing but the database: the permission,
    the finding seen as on its review page, its author, critical/high, still open, no request in
    flight, the person's 24-hour limit (`quota`). The MCP server runs it before it asks GitLab
    about the commits (docs/specs/26 §8), and `create_close_request` runs it again when it
    writes. Writes nothing but the sweep of lost checks. HTTPException on every refusal."""
    if not principal.can("finding.close_request"):
        raise HTTPException(403, f"thiếu quyền {BY_KEY['finding.close_request'].label.lower()}")
    finding, review = _finding(session, principal, finding_id)
    if review.author_user_id != principal.user_id:
        raise HTTPException(403, "chỉ tác giả của thay đổi mới được yêu cầu đóng finding của mình")
    if finding.severity not in SEVERE:
        raise HTTPException(422, "chỉ yêu cầu đóng được finding critical/high")
    if finding.status != "open":
        raise HTTPException(409, f"finding đang ở trạng thái {finding.status}")
    if _in_flight(session, finding.id) is not None:
        raise HTTPException(409, "finding này đã có một yêu cầu đóng đang chờ")
    used, limit = quota(session, principal.user_id)
    if limit == 0:
        raise HTTPException(429, "tài khoản này không được gửi yêu cầu đóng (admin đặt giới hạn 0)")
    if used >= limit:
        raise HTTPException(429, f"tối đa {limit} yêu cầu đóng trong 24 giờ (đã gửi {used})")
    return finding, review


def requests_last_day(session: Session, user_id: int) -> int:
    """Close requests this person sent in the last 24 hours (web and MCP)."""
    return int(
        session.scalar(
            select(func.count()).where(
                FindingClosure.requested_by == user_id,
                FindingClosure.kind == "request",
                FindingClosure.created_at >= datetime.now(UTC) - timedelta(days=1),
            )
        )
        or 0
    )


def quota(session: Session, user_id: int, settings: Settings | None = None) -> tuple[int, int]:
    """(sent in the last 24 hours, may send): the person's own limit or the default (Admin >
    Cấu hình, else the environment's `settings`)."""
    limit = close_requests_per_day(session, settings or get_settings(), user_id)
    return requests_last_day(session, user_id), limit


def create_close_request(
    session: Session,
    principal: Principal,
    finding_id: int,
    commits: list[str],
    argument: str | None,
    note: str | None,
    *,
    via: str = "web",
    meta: dict[str, Any] | None = None,
) -> FindingClosure:
    """A close request by the change's author: the dashboard form and the MCP server
    (docs/specs/26 §8) share every rule here (`check_close_request`). `commits` / `argument`
    arrive validated as by CloseRequestIn. Flushed and audited, not committed. HTTPException on
    every refusal."""
    # One request of a person at a time from the count to the insert: requests sent at once
    # (several findings, an MCP agent in a loop) cannot pass the 24-hour limit together.
    session.execute(select(User.id).where(User.id == principal.user_id).with_for_update())
    finding, _review = check_close_request(session, principal, finding_id)
    closure = FindingClosure(
        finding_id=finding.id,
        kind="request",
        status="pending",
        shas=commits,
        argument=argument,
        requested_by=principal.user_id,
        note=(note or "").strip() or None,
        via=via,
        meta=meta,
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
        shas=commits,
        argument=bool(argument),
        via=via,
    )
    return closure


@router.post("/close-requests/{closure_id}/cancel", response_model=ClosureOut)
def cancel_request(
    closure_id: int, principal: RequestPrincipal, session: SessionDep
) -> FindingClosure:
    closure = cancel_close_request(session, principal, closure_id)
    session.commit()
    return closure


def cancel_close_request(
    session: Session, principal: Principal, closure_id: int, *, via: str = "web"
) -> FindingClosure:
    """The sender withdraws their request while it waits (`pending`, `unmet`): the dashboard's
    "Rút" button and the MCP server (docs/specs/26 §6) share it. Audited, not committed.
    HTTPException on every refusal."""
    if not principal.can("finding.close_request"):
        raise HTTPException(403, f"thiếu quyền {BY_KEY['finding.close_request'].label.lower()}")
    closure = _request(session, closure_id)
    if closure.requested_by != principal.user_id:
        raise HTTPException(403, "chỉ người gửi mới rút được yêu cầu")
    if not _move(
        session, closure, ("pending", "unmet"), status="cancelled", decided_at=datetime.now(UTC)
    ):
        raise HTTPException(409, "yêu cầu đã được duyệt hoặc đang được kiểm; tải lại trang")
    audit(
        session,
        principal.user_id,
        "finding.close_request.cancel",
        f"finding:{closure.finding_id}",
        via=None if via == "web" else via,
    )
    return closure


# -- the manager / admin ---------------------------------------------------------------------------


@router.post("/close-requests/{closure_id}/run", response_model=ClosureOut, status_code=202)
def run_request(
    closure_id: int,
    principal: DeciderPrincipal,
    session: SessionDep,
    queue: Annotated[Any, Depends(get_queue)],
) -> FindingClosure:
    """ "Chạy review": check the commits now (a failed or unmet check may be run again)."""
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
        "evidence": closure.evidence,
    }
    token = secrets.token_hex(8)
    try:
        moved = _move(
            session,
            closure,
            ("pending", "failed", "unmet"),
            status="checking",
            run_token=token,
            started_at=None,
            decided_by=principal.user_id,
            decided_at=None,
            reason=None,
            evidence=None,
        )
    except IntegrityError:  # another request of the same finding is in flight
        raise HTTPException(409, "finding này đang có yêu cầu khác") from None
    if not moved:
        raise HTTPException(409, "yêu cầu vừa được người khác duyệt; tải lại trang")
    audit(session, principal.user_id, "finding.close_request.run", f"finding:{closure.finding_id}")
    session.commit()
    try:
        # A member's request: served before automatic reviews (app/workers/queues.py).
        review_queue(queue, asked=True).enqueue(
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
    closure_id: int, body: NoteIn, principal: DeciderPrincipal, session: SessionDep
) -> FindingClosure:
    closure = _request(session, closure_id)
    _decider(session, principal, closure)
    was = closure.status
    reason = f"Từ chối: {body.note.strip()}"
    if was == "unmet" and closure.reason:  # the check's reason stays with the refusal
        reason = f"{reason}\n{CHECKED} {closure.reason}"
    moved = (
        was in TO_DECIDE
        and _move(  # from the state read: the reason was built on it
            session,
            closure,
            (was,),
            status="rejected",
            decided_by=principal.user_id,
            decided_at=datetime.now(UTC),
            reason=reason[:1000],
        )
    )
    if not moved:
        raise HTTPException(409, "yêu cầu đang được kiểm hoặc đã có kết quả; tải lại trang")
    audit(
        session, principal.user_id, "finding.close_request.reject", f"finding:{closure.finding_id}"
    )
    session.commit()
    return closure


@router.post("/close-requests/{closure_id}/close", response_model=ClosureOut)
def close_unmet(
    closure_id: int,
    body: OverrideIn,
    principal: DeciderPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FindingClosure:
    """ "Đóng finding" on a request the check found not good enough (`unmet`): the author's
    lead, a manager or an admin who read the check's reason closes the finding anyway, a note
    being mandatory: as fixed by the request's commits (severity and score kept), or accepting
    the risk at medium/low (re-scored), or as wrong (false_positive)."""
    closure = _request(session, closure_id)
    _decider(session, principal, closure)
    if closure.status != "unmet":
        raise HTTPException(409, "chỉ đóng được yêu cầu kiểm chưa đạt; tải lại trang")
    finding = session.get(ReviewFinding, closure.finding_id)
    if finding is None:
        raise HTTPException(404, "finding not found")
    if finding.status != "open":
        raise HTTPException(409, f"finding đang ở trạng thái {finding.status}, không còn mở")
    note = body.note.strip()
    if body.outcome == "fixed":
        if not closure.shas:
            raise HTTPException(422, "yêu cầu không kèm commit: chọn mức mới hoặc 'finding sai'")
    else:
        author, bp_id = _owner(session, closure)
        _check_settle(finding, body.outcome, note, _lowers_far(principal, author, bp_id))
    reason = f"{closure.reason or ''}\n{OVERRIDDEN} {note}".strip()[:1000]
    if not _move(
        session,
        closure,
        ("unmet",),
        status="closed",
        decided_by=principal.user_id,
        decided_at=datetime.now(UTC),
        reason=reason,
        evidence=None,  # what the check pointed at is not where a fix is
    ):
        raise HTTPException(409, "yêu cầu vừa được người khác xử lý; tải lại trang")
    if body.outcome == "fixed":
        closure.closed_as = "fixed"
        session.flush()
        closed = mark_fixed(session, finding)
        if closed:
            rescore_copies(session, carry_decision(session, finding), settings)
    else:
        closed = _settle(session, finding, closure, body.outcome, settings)
    if not closed:
        session.rollback()
        raise HTTPException(409, "finding vừa đổi trạng thái; tải lại trang")
    audit(
        session,
        principal.user_id,
        "finding.close_request.override",
        f"finding:{closure.finding_id}",
        closure_id=closure.id,
        outcome=body.outcome,
        note=note,
    )
    session.refresh(finding)
    inbox.finding_closed(session, finding, closure, principal.user_id)
    session.commit()
    return closure


@router.post("/close-requests/{closure_id}/confirm", response_model=ClosureOut)
def confirm_proposal(
    closure_id: int,
    principal: DeciderPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    body: ConfirmIn | None = None,
) -> FindingClosure:
    """ "Xác nhận": the check found the argument holds; the author's lead, a manager or an
    admin who read its reason and evidence closes the finding, lowered to medium or low (the
    check's suggestion unless chosen otherwise) and re-scored, or as wrong (false_positive)."""
    body = body or ConfirmIn()
    closure = _request(session, closure_id)
    _decider(session, principal, closure)
    finding = session.get(ReviewFinding, closure.finding_id)
    if finding is None:
        raise HTTPException(404, "finding not found")
    author, bp_id = _owner(session, closure)
    far = _lowers_far(principal, author, bp_id)
    outcome = body.outcome or _default_settle(finding.severity, closure.suggested_severity, far)
    _check_settle(finding, outcome, body.note, far)
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
    if not _settle(session, finding, closure, outcome, settings):
        session.rollback()
        raise HTTPException(409, f"finding đang ở trạng thái {finding.status}, không còn mở")
    decider_note = (body.note or "").strip()
    if decider_note:  # the author's note to the decider stays as it was
        closure.reason = f"{closure.reason or ''}\nNgười duyệt: {decider_note}".strip()[:1000]
    audit(
        session,
        principal.user_id,
        "finding.close_request.confirm",
        f"finding:{closure.finding_id}",
        closure_id=closure.id,
        outcome=outcome,
        severity_before=closure.severity_before,
        note=decider_note or None,
    )
    inbox.finding_closed(session, finding, closure, principal.user_id)
    session.commit()
    return closure


@router.post("/findings/{finding_id}/close", response_model=ClosureOut)
def close_by_admin(
    finding_id: int,
    body: AdminCloseIn,
    principal: ManualPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FindingClosure:
    """An admin closes by hand: with the fixing commits, as `fixed` (severity and score kept);
    without one, accepting the risk at the severity chosen (re-scored), or as wrong."""
    finding, _ = _finding(session, principal, finding_id)
    if finding.status != "open":
        raise HTTPException(409, f"finding đang ở trạng thái {finding.status}")
    if not body.commits and body.outcome is None:
        raise HTTPException(
            422,
            "dán commit đã sửa, hoặc chọn mức mới (medium/low) / 'finding sai'"
            " khi chấp nhận rủi ro",
        )
    if not body.commits and body.outcome is not None:
        _check_settle(finding, body.outcome, body.note, far=True)
    waiting = _in_flight(session, finding.id)
    if waiting is not None and waiting.status == "checking":
        raise HTTPException(409, "finding đang được kiểm; chờ kết quả")
    if waiting is not None and not _move(  # a waiting request: the admin's decision answers it
        session,
        waiting,
        ("pending", "proposed", "unmet"),
        status="rejected",
        decided_by=principal.user_id,
        decided_at=datetime.now(UTC),
        reason="Admin đã đóng thủ công finding này.",
    ):
        raise HTTPException(409, "yêu cầu đóng vừa được chạy; chờ kết quả")
    if body.commits:
        closure = close_finding(
            session,
            finding,
            kind="admin",
            now=datetime.now(UTC),
            shas=body.commits,
            requested_by=principal.user_id,
            decided_by=principal.user_id,
            note=body.note.strip(),
        )
        if closure is None:
            raise HTTPException(409, "finding vừa đổi trạng thái; tải lại trang")
    else:
        closure = FindingClosure(
            finding_id=finding.id,
            kind="admin",
            status="closed",
            requested_by=principal.user_id,
            decided_by=principal.user_id,
            note=body.note.strip(),
            decided_at=datetime.now(UTC),
        )
        session.add(closure)
        session.flush()
        if not _settle(session, finding, closure, body.outcome or "medium", settings):
            session.rollback()
            raise HTTPException(409, "finding vừa đổi trạng thái; tải lại trang")
    audit(
        session,
        principal.user_id,
        "finding.close",
        f"finding:{finding.id}",
        note=body.note,
        commits=body.commits,
        outcome=body.outcome if not body.commits else "fixed",
    )
    session.refresh(finding)
    inbox.finding_closed(session, finding, closure, principal.user_id)
    session.commit()
    return closure


class SettleIn(BaseModel):
    note: str = Field(default="", max_length=1000)


@router.post("/findings/{finding_id}/close-decided", response_model=ClosureOut)
def close_decided(
    finding_id: int,
    body: SettleIn,
    principal: CurrentPrincipal,
    session: SessionDep,
) -> FindingClosure:
    """Close by hand a finding already decided not an error (user 2026-11-04: "đã xác định
    không phải lỗi thì cho phép đóng thủ công"): its waiting request is closed with that
    decision, or, when only an old refused request is left, a closing is recorded. The score
    does not change (the finding is already out of it). An admin, or who decides the author's
    close requests."""
    finding, review = _finding(session, principal, finding_id)
    if finding.status != "false_positive":
        raise HTTPException(
            409, f"chỉ dùng cho finding đã duyệt là không phải lỗi (đang {finding.status})"
        )
    repo = review_repo(session, review)
    author, bp_id = review.author_user_id, (repo.business_project_id if repo else None)
    if not (principal.can("finding.close_manual") or _may_decide(principal, author, bp_id)):
        raise HTTPException(403, "chỉ admin hoặc người duyệt yêu cầu đóng của tác giả")
    note = body.note.strip()
    reason = "Finding được duyệt là không phải lỗi: đóng thủ công." + (f" {note}" if note else "")
    settled = settle_open_requests(
        session,
        [finding.id],
        closed_as="false_positive",
        reason=reason,
        decided_by=principal.user_id,
    )
    if settled:
        closure = session.scalars(
            select(FindingClosure)
            .where(FindingClosure.finding_id == finding.id)
            .order_by(FindingClosure.id.desc())
            .limit(1)
        ).one()
    else:
        newest = session.scalars(
            select(FindingClosure)
            .where(FindingClosure.finding_id == finding.id)
            .order_by(FindingClosure.id.desc())
            .limit(1)
        ).first()
        if newest is not None and newest.status == "closed":
            raise HTTPException(409, "finding đã đóng")
        closure = FindingClosure(
            finding_id=finding.id,
            kind="admin",
            status="closed",
            closed_as="false_positive",
            requested_by=principal.user_id,
            decided_by=principal.user_id,
            note=note or None,
            reason=reason,
            decided_at=datetime.now(UTC),
        )
        session.add(closure)
        session.flush()
    audit(
        session,
        principal.user_id,
        "finding.close_decided",
        f"finding:{finding.id}",
        note=note or None,
    )
    session.commit()
    return closure


@router.post("/findings/{finding_id}/reopen", response_model=ClosureOut)
def reopen_by_admin(
    finding_id: int,
    body: NoteIn,
    principal: ManualPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FindingClosure:
    finding, _ = _finding(session, principal, finding_id)
    # The newest record of the finding: a close not undone since (a reopen adds its own row).
    newest = session.scalars(
        select(FindingClosure)
        .where(FindingClosure.finding_id == finding.id)
        .order_by(FindingClosure.id.desc())
        .limit(1)
    ).first()
    last = newest if newest is not None and newest.status == "closed" else None
    closed_here = finding.status in ("fixed", "accepted_risk") or (
        # found wrong by a close (not by a dispute: that one is decided there)
        finding.status == "false_positive"
        and last is not None
        and last.closed_as == "false_positive"
    )
    if not closed_here:
        raise HTTPException(409, f"chỉ mở lại được finding đã đóng (đang {finding.status})")
    accepted_dispute = session.scalar(
        select(FindingDispute.id).where(
            FindingDispute.finding_id == finding.id, FindingDispute.decision == "accepted"
        )
    )
    if finding.status == "false_positive" and accepted_dispute is not None:
        raise HTTPException(
            409, "finding được duyệt là không phải lỗi qua khiếu nại: quyết định ở trang khiếu nại"
        )
    rescore = finding.status == "false_positive"
    finding.status = "open"
    if last is not None and last.severity_before and last.severity_before != finding.severity:
        finding.severity = last.severity_before  # what the review found, before it was lowered
        rescore = True
    # A request left waiting while the finding was closed another way (a later review, a
    # dispute) is answered now: reopened, the finding needs a new request with today's code.
    session.execute(
        update(FindingClosure)
        .where(
            FindingClosure.finding_id == finding.id,
            FindingClosure.status.in_(("pending", "proposed", "unmet")),
        )
        .values(
            status="rejected",
            decided_at=datetime.now(UTC),
            reason="Finding đã được đóng cách khác trong lúc chờ duyệt.",
        )
        .execution_options(synchronize_session=False)
    )
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
    if rescore:
        review = session.get(Review, finding.review_id) if finding.review_id else None
        if review is not None:
            rescore_review(session, review, settings)
    audit(session, principal.user_id, "finding.reopen", f"finding:{finding.id}", note=body.note)
    session.commit()
    return closure


# -- reading ---------------------------------------------------------------------------------------


@router.get("/reviews/{review_id}/closures", response_model=list[ClosureOut])
def review_closures(
    review_id: int, principal: ViewPrincipal, session: SessionDep
) -> list[dict[str, Any]]:
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
    project = review_repo(session, review)
    bp_id = project.business_project_id if project else None
    decides = _may_decide(principal, review.author_user_id, bp_id)
    far = decides and _lowers_far(principal, review.author_user_id, bp_id)
    severity = {
        int(i): str(sev)
        for i, sev in session.execute(
            select(ReviewFinding.id, ReviewFinding.severity).where(
                ReviewFinding.review_id == review.id
            )
        )
    }
    return [
        {
            **ClosureOut.model_validate(row).model_dump(),
            "can_decide": decides and row.kind == "request" and row.status in TO_DECIDE,
            "can_cancel": row.status in ("pending", "unmet")
            and row.requested_by == principal.user_id,
            "can_lower_far": far,
            "finding_severity": severity.get(row.finding_id),
        }
        for row in rows
    ]


def _repo_of_review() -> Any:
    """review id -> repo id, for MR and commit reviews alike."""
    mr = select(Review.id.label("review_id"), MergeRequest.project_id.label("project_id")).join(
        MergeRequest, MergeRequest.id == Review.mr_id
    )
    commit = select(Review.id.label("review_id"), Commit.project_id.label("project_id")).join(
        Commit, Commit.sha == Review.commit_sha
    )
    return mr.union_all(commit).subquery("review_repo")


def _visible(principal: Principal, repo: Any) -> ColumnElement[bool]:
    """Whose closings this person sees: everyone's (admin), their own work anywhere, every one in
    a dự án whose reviews they all see (`review.view` at "project": the dự án they manage), and
    that of the people whose requests they decide, in the dự án they are granted."""
    if principal.is_admin:
        return true()
    seen: list[ColumnElement[bool]] = [Review.author_user_id == principal.user_id]
    if managed_bps := whole_projects(principal, "review.view") or set():
        seen.append(
            repo.c.project_id.in_(
                select(Project.id).where(Project.business_project_id.in_(managed_bps))
            )
        )
    managed = _managed_members(principal)
    granted = principal.visible_projects() or set()
    if managed and granted:
        in_granted = repo.c.project_id.in_(
            select(Project.id).where(Project.business_project_id.in_(granted))
        )
        seen.append(and_(Review.author_user_id.in_(managed), in_granted))
    return or_(*seen)


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


def _shown_status() -> ColumnElement[str]:
    """`shown_status` in SQL: what the row shows and the status filter matches."""
    return case(
        (ReviewFinding.status.in_(FINDING_CLOSED), literal("closed")),
        (FindingClosure.status == "closed", literal("reopened")),
        else_=FindingClosure.status,
    )


@router.get("/close-requests")
def list_closures(
    principal: ViewPrincipal,
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
        .where(_visible(principal, repo))
    )
    if view == "todo":
        query = query.where(
            FindingClosure.kind == "request",
            FindingClosure.status.in_(TO_DECIDE),
            ReviewFinding.status == "open",  # closed another way meanwhile: nothing to decide
            *_decides_where(principal),
        )
    if status:
        # The state the row shows (the finding's when it is closed), not the request's alone.
        query = query.where(_shown_status().in_(status))
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
    items = []
    for closure, finding, review, project, bp, who, asker, judge in rows[:PAGE]:
        bp_id = project.business_project_id if project else None
        decides = (
            closure.kind == "request"
            and closure.status in TO_DECIDE
            and finding.status == "open"
            and _may_decide(principal, review.author_user_id, bp_id)
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
                # What the row shows (user 2026-11-06): the finding's state when closed, the
                # request's own `status` beside it when they differ.
                "shown_status": shown_status(closure.status, finding.status),
                "shown_closed_as": finding.status if finding.status in FINDING_CLOSED else None,
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
                "can_lower_far": decides and _lowers_far(principal, review.author_user_id, bp_id),
                "finding_severity": finding.severity,
                "can_cancel": closure.status in ("pending", "unmet")
                and closure.requested_by == principal.user_id,
                "can_open_review": sees_work(principal, review.author_user_id, bp_id),
            }
        )
    return {
        "items": items,
        "next_cursor": _cursor(rows[PAGE - 1][0].id) if len(rows) > PAGE else None,
    }


@router.get("/close-requests/facets")
def closure_facets(principal: ViewPrincipal, session: SessionDep) -> dict[str, Any]:
    """The filter choices of the page (only what this person may see) and the to-do count."""
    _sweep(session)
    session.commit()
    repo = _repo_of_review()
    base = (
        select(FindingClosure.id)
        .join(ReviewFinding, ReviewFinding.id == FindingClosure.finding_id)
        .join(Review, Review.id == ReviewFinding.review_id)
        .outerjoin(repo, repo.c.review_id == Review.id)
        .where(_visible(principal, repo))
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
    todo = select(func.count()).select_from(
        base.where(
            FindingClosure.kind == "request",
            FindingClosure.status.in_(TO_DECIDE),
            ReviewFinding.status == "open",
            *_decides_where(principal),
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
        "can_decide": principal.is_admin
        or bool(_managed_members(principal))
        or bool(whole_projects(principal, "finding.close_decide")),
    }
