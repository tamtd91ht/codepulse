"""Reviews and findings (docs/specs/09), scoped by role (developer: own; lead: team; admin: all)
and by dự án grant (app/api/access.py)."""

import logging
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Annotated, Any, Literal, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date, Select, exists, func, or_, select, tuple_
from sqlalchemy import cast as sql_cast
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.access import (
    covers_work,
    may_rerun,
    review_repo,
    reviewable_people,
    sees_work,
    whole_projects,
    work_scope,
)
from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.locks import busy_as_409, short_lock_wait
from app.api.schemas import (
    AccountLinked,
    AccountOut,
    BoardFindingOut,
    FindingOut,
    Page,
    RerunIn,
    RerunManyIn,
    RerunManyOut,
    RerunnableOut,
    ReviewAuthorAccount,
    ReviewAuthorLink,
    ReviewAuthorOut,
    ReviewDetail,
    ReviewListItem,
    ReviewOut,
    UserOut,
    decode_cursor,
    decode_cursor_text,
    encode_cursor,
    encode_cursor_text,
)
from app.api.security import CurrentPrincipal, Principal, require
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.identity.attribution import Attribution, attribute_reviews, rebuild_after_commit
from app.jira.commit_links import commit_issues
from app.jira.links import mr_issues
from app.models import (
    Account,
    Commit,
    Connection,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    ReviewStaticFinding,
    TeamMember,
    User,
)
from app.models.review import CHANGE_TIME_UNKNOWN
from app.review.rubric import RUBRIC_VERSION
from app.review.service import TOO_LARGE
from app.scoring.criteria_store import review_scores
from app.webhook.jobs import REVIEW_RETRY, review_job_timeout
from app.workers.queues import review_queue

log = logging.getLogger(__name__)
router = APIRouter(tags=["reviews"])
SessionDep = Annotated[Session, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=200)]
ReviewStatus = Literal["queued", "running", "done", "failed", "skipped", "superseded"]
# "Chọn tất cả" stops here: a batch past it is more LLM spend than one click should start.
RERUNNABLE_MAX = 500
# "Sâu": the pass ran with the repository open (agent; "deep" before spec 19).
ReviewMode = Literal["fast", "deep"]
MODES: dict[str, tuple[str, ...]] = {"fast": ("fast",), "deep": ("agent", "deep")}


ViewPrincipal = Annotated[Principal, require("review.view")]
# Re-runs are offered per review (`may_rerun`, permission review.rerun): the list endpoints
# stay open and answer with nothing to run, as before spec 21, rather than 403.
RerunPrincipal = CurrentPrincipal
AuthorPrincipal = Annotated[Principal, require("review.author")]


def scoped[Q: Select[Any] | Select[Any, Any, Any]](query: Q, principal: Principal) -> Q:
    allowed = work_scope(principal, "review.view")
    return query if allowed is None else cast(Q, query.where(allowed))


def _may_filter_by(principal: Principal, user_id: int) -> bool:
    """A person filter: someone whose work `review.view` covers everywhere, or anyone when it
    covers a whole dự án (the scope then keeps the reviews of that dự án)."""
    return principal.covers("review.view", user_id, None) or bool(
        whole_projects(principal, "review.view")
    )


def _encode_changed_cursor(changed_at: datetime, review_id: int) -> str:
    return encode_cursor_text(f"{changed_at.isoformat()}|{review_id}")


def _decode_changed_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    """(change time, review id) of the last row shown; None for a missing or foreign cursor."""
    text = decode_cursor_text(cursor)
    if text is None or "|" not in text:
        return None
    when, _, review_id = text.rpartition("|")
    try:
        return datetime.fromisoformat(when), int(review_id)
    except ValueError:
        return None


def _range(
    query: Select[Any],
    column: Any,
    date_from: date | None,
    date_to: date | None,
    tz: tzinfo = UTC,
) -> Select[Any]:
    """Whole days [from, to] in `tz` (the list of reviews passes REPORT_TZ)."""
    if date_from:
        query = query.where(column >= datetime.combine(date_from, time.min, tz))
    if date_to:
        query = query.where(column < datetime.combine(date_to + timedelta(days=1), time.min, tz))
    return query


def _target(session: Session, review: Review) -> tuple[Project | None, str | None, str]:
    """Project, GitLab web URL and a short label for a review's MR or commit."""
    project = review_repo(session, review)
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        label, path = (f"MR !{mr.iid}", f"/-/merge_requests/{mr.iid}") if mr else ("MR", None)
    else:
        sha = review.commit_sha or ""
        label, path = f"commit {sha[:8]}", f"/-/commit/{sha}"
    url = None
    if project and path:
        connection = session.get(Connection, project.connection_id)
        if connection:
            url = f"{connection.base_url}/{project.path_with_namespace}{path}"
    return project, url, label


def _filtered(
    principal: Principal,
    settings: Settings,
    *,
    user_id: int | None,
    project_id: int | None,
    business_project_id: int | None,
    severity: str | None,
    verdict: str | None,
    trigger: str | None,
    mode: ReviewMode | None,
    status: list[ReviewStatus] | None,
    date_from: date | None,
    date_to: date | None,
) -> Select[Any]:
    """Reviews the viewer may see that match the filters of "Kết quả review"."""
    if user_id is not None and not _may_filter_by(principal, user_id):
        raise HTTPException(403, "not allowed to see this person's reviews")
    query = scoped(select(Review), principal)
    if user_id is not None:
        query = query.where(Review.author_user_id == user_id)
    if verdict is not None:
        query = query.where(Review.verdict == verdict)
    if mode is not None:
        query = query.where(Review.mode.in_(MODES[mode]))
    if trigger is not None:
        query = query.where(Review.trigger == trigger)
    if status:
        query = query.where(Review.status.in_(status))
    if project_id is not None:
        mr_ids = select(MergeRequest.id).where(MergeRequest.project_id == project_id)
        shas = select(Commit.sha).where(Commit.project_id == project_id)
        query = query.where(Review.mr_id.in_(mr_ids) | Review.commit_sha.in_(shas))
    if business_project_id is not None:
        repos = select(Project.id).where(Project.business_project_id == business_project_id)
        mr_ids = select(MergeRequest.id).where(MergeRequest.project_id.in_(repos))
        shas = select(Commit.sha).where(Commit.project_id.in_(repos))
        query = query.where(Review.mr_id.in_(mr_ids) | Review.commit_sha.in_(shas))
    if severity:
        with_severity = select(ReviewFinding.review_id).where(ReviewFinding.severity == severity)
        query = query.where(Review.id.in_(with_severity))
    # Days of the change itself (commit time; MR opened), as the list is sorted.
    return _range(query, Review.counted_at, date_from, date_to, ZoneInfo(settings.report_tz))


@router.get("/reviews", response_model=Page[ReviewListItem])
def list_reviews(
    principal: ViewPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    user_id: int | None = None,
    project_id: int | None = None,
    business_project_id: int | None = None,
    severity: str | None = None,
    verdict: Literal["approve", "needs_human", "fix_required", "block"] | None = None,
    trigger: str | None = None,
    mode: ReviewMode | None = None,
    status: Annotated[list[ReviewStatus] | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[Any]:
    query = _filtered(
        principal,
        settings,
        user_id=user_id,
        project_id=project_id,
        business_project_id=business_project_id,
        severity=severity,
        verdict=verdict,
        trigger=trigger,
        mode=mode,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    if (after := _decode_changed_cursor(cursor)) is not None:
        query = query.where(tuple_(Review.changed_at, Review.id) < tuple_(*after))
    # Newest change first: ix_reviews_changed_at_id serves both the order and the cursor.
    rows = list(
        session.scalars(query.order_by(Review.changed_at.desc(), Review.id.desc()).limit(limit + 1))
    )
    if user_id is not None and user_id != principal.user_id:
        audit(session, principal.user_id, "reviews.read", f"user:{user_id}")
        session.commit()
    more = len(rows) > limit
    # Authors in one query: the list shows who each review was about.
    author_ids = {r.author_user_id for r in rows[:limit] if r.author_user_id is not None}
    authors = (
        {u.id: u for u in session.scalars(select(User).where(User.id.in_(author_ids)))}
        if author_ids
        else {}
    )
    # No person yet: the git / GitLab identity behind the change, and whether this viewer may
    # tie it to someone (admin, or a manager of the review's dự án).
    unmapped = [r for r in rows[:limit] if r.author_user_id is None]
    account_of = _author_account_ids(session, unmapped)
    accounts = (
        {
            a.id: a
            for a in session.scalars(select(Account).where(Account.id.in_(account_of.values())))
        }
        if account_of
        else {}
    )
    linked_ids = {a.user_id for a in accounts.values() if a.user_id is not None}
    linked = (
        {u.id: u for u in session.scalars(select(User).where(User.id.in_(linked_ids)))}
        if linked_ids
        else {}
    )
    items = []
    for review in rows[:limit]:
        project, url, label = _target(session, review)
        author = authors.get(review.author_user_id) if review.author_user_id else None
        account = accounts.get(account_of.get(review.id, 0))
        author_account = (
            _account_out(
                account,
                linked.get(account.user_id) if account.user_id else None,
                _may_link(principal, project.business_project_id if project else None),
            )
            if account is not None
            else None
        )
        items.append(
            {
                **ReviewOut.model_validate(review).model_dump(),
                "target": label,
                "project": project.path_with_namespace if project else None,
                "web_url": url,
                "author": UserOut.model_validate(author) if author else None,
                "author_account": author_account,
                "changed_at": None
                if review.changed_at == CHANGE_TIME_UNKNOWN
                else review.changed_at,
                "can_rerun": _can_rerun(session, review, project)
                and _reruns(principal, review, project),
            }
        )
    next_cursor = (
        _encode_changed_cursor(rows[limit - 1].changed_at, rows[limit - 1].id) if more else None
    )
    return Page(items=items, next_cursor=next_cursor)


@router.get("/reviews/rerunnable", response_model=RerunnableOut)
def rerunnable_reviews(
    principal: RerunPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    user_id: int | None = None,
    project_id: int | None = None,
    business_project_id: int | None = None,
    severity: str | None = None,
    verdict: Literal["approve", "needs_human", "fix_required", "block"] | None = None,
    trigger: str | None = None,
    mode: ReviewMode | None = None,
    status: Annotated[list[ReviewStatus] | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    """Every review the list would show (same filters, all pages) that the viewer may run again:
    "chọn tất cả" of "Kết quả review", newest change first, at most RERUNNABLE_MAX of them."""
    query = _filtered(
        principal,
        settings,
        user_id=user_id,
        project_id=project_id,
        business_project_id=business_project_id,
        severity=severity,
        verdict=verdict,
        trigger=trigger,
        mode=mode,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    if user_id is not None and user_id != principal.user_id:
        audit(session, principal.user_id, "reviews.read", f"user:{user_id}")
        session.commit()
    managed = principal.managed_projects()  # `may_rerun`: within the dự án one manages
    if managed is not None and not managed:
        return {"ids": [], "done": 0, "truncated": False}
    # _can_rerun and the manage check in SQL, so the scan stops after RERUNNABLE_MAX + 1 rows.
    # Keep this a subset of _can_rerun (still checked per row): the list's can_rerun is the rule.
    repos = select(Project.id).where(Project.tracked, Project.review_enabled.is_(True))
    if managed is not None:
        repos = repos.where(Project.business_project_id.in_(managed))
    open_head = exists().where(
        MergeRequest.id == Review.mr_id,
        MergeRequest.project_id.in_(repos),
        MergeRequest.state == "opened",
        MergeRequest.head_sha == Review.reviewed_sha,
    )
    commit_in_repo = Review.commit_sha.in_(select(Commit.sha).where(Commit.project_id.in_(repos)))
    query = query.where(
        Review.status.in_(("done", "failed", "skipped")),
        Review.superseded_by.is_(None),
        Review.rubric_version == RUBRIC_VERSION,
        or_(open_head, Review.mr_id.is_(None) & commit_in_repo),
    )
    rows = session.scalars(
        query.order_by(Review.changed_at.desc(), Review.id.desc()).limit(RERUNNABLE_MAX + 1)
    ).all()
    ids: list[int] = []
    done = 0
    truncated = False
    for review in rows:
        project = review_repo(session, review)
        if not (_can_rerun(session, review, project) and _reruns(principal, review, project)):
            continue
        if len(ids) == RERUNNABLE_MAX:
            truncated = True
            break
        ids.append(review.id)
        done += review.status == "done"
    return {"ids": ids, "done": done, "truncated": truncated}


def _visible_review(session: Session, principal: Principal, review_id: int) -> Review:
    review = session.get(Review, review_id)
    if review is None:
        raise HTTPException(404, "review not found")
    project = review_repo(session, review)
    bp_id = project.business_project_id if project else None
    if not sees_work(principal, review.author_user_id, bp_id):
        # A dự án you are not granted does not exist for you (docs/specs/09).
        if not principal.can_view_project(bp_id):
            raise HTTPException(404, "review not found")
        raise HTTPException(403, "not allowed to see this review")
    return review


@router.get("/reviews/{review_id}", response_model=ReviewDetail)
def get_review(
    review_id: int,
    principal: ViewPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    review = _visible_review(session, principal, review_id)
    findings = list(
        session.scalars(
            select(ReviewFinding)
            .where(ReviewFinding.review_id == review.id)
            .order_by(ReviewFinding.id)
        )
    )
    project, url, _ = _target(session, review)
    mr = session.get(MergeRequest, review.mr_id) if review.mr_id is not None else None
    bp_id = project.business_project_id if project else None
    jira = (
        mr_issues(session, settings, mr, bp_id)
        if mr is not None
        else commit_issues(session, settings, review.commit_sha, bp_id)
        if review.commit_sha
        else None
    )
    if review.author_user_id not in (None, principal.user_id):
        audit(session, principal.user_id, "review.read", f"review:{review.id}")
        session.commit()
    return {
        **ReviewOut.model_validate(review).model_dump(),
        "findings": findings,
        "static_findings": list(
            session.scalars(
                select(ReviewStaticFinding)
                .where(ReviewStaticFinding.review_id == review.id)
                .order_by(ReviewStaticFinding.id)
            )
        ),
        "project": project.path_with_namespace if project else None,
        "web_url": url,
        "history": _history(session, review),
        "can_rerun": _can_rerun(session, review, project) and _reruns(principal, review, project),
        # docs/specs/22 §5: who sees the review sees its criteria scores.
        "criteria_scores": review_scores(
            session, review.id, project.business_project_id if project else None
        ),
        "jira": jira,
    }


def _history(session: Session, review: Review) -> list[dict[str, Any]]:
    """Every review of the same MR (all heads) or commit, oldest first."""
    if review.mr_id is not None:
        where = Review.mr_id == review.mr_id
    elif review.commit_sha is not None:
        where = Review.commit_sha == review.commit_sha
    else:
        return []
    rows = session.scalars(
        select(Review).where(Review.target_type == review.target_type, where).order_by(Review.id)
    )
    return [
        {
            "id": r.id,
            "round": r.round,
            "reviewed_sha": r.reviewed_sha,
            "status": r.status,
            "score": r.score,
            "verdict": r.verdict,
            "mode": r.mode,
            "trigger": r.trigger,
            "finished_at": r.finished_at,
            "basis": r.basis,
        }
        for r in rows
    ]


def _reruns(principal: Principal, review: Review, project: Project | None) -> bool:
    return may_rerun(
        principal, review.author_user_id, project.business_project_id if project else None
    )


def _may_link(principal: Principal, bp_id: int | None) -> bool:
    """`review.author`: tying the author of a change to a person is about the dự án, not
    about anyone's work."""
    return covers_work(principal, "review.author", None, bp_id)


def _can_rerun(session: Session, review: Review, project: Project | None) -> bool:
    """A finished commit review may always run again; an MR review only while the MR is open
    and this review is of its current head (older heads are history). Never on a repo whose
    review is turned off: the worker skips those."""
    if project is None or not (project.tracked and project.review_enabled):
        return False
    if review.status not in ("done", "failed", "skipped"):
        return False
    if review.superseded_by is not None or review.rubric_version != RUBRIC_VERSION:
        return False
    if review.mr_id is None:
        return True
    mr = session.get(MergeRequest, review.mr_id)
    return bool(mr and mr.state == "opened" and mr.head_sha == review.reviewed_sha)


@router.post("/reviews/{review_id}/rerun", status_code=202)
def rerun_review(
    review_id: int,
    principal: RerunPrincipal,
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
    body: RerunIn | None = None,
) -> dict[str, str]:
    """Re-run a failed or skipped review; with `force` also a finished one, as a new round
    (docs/specs/17 §Lịch sử): the old round is kept and marked superseded. An MR can only be
    reviewed again while it is open and only at its current head. Managers of the dự án
    and admins only: a re-run spends LLM budget."""
    review = _visible_review(session, principal, review_id)
    _queue_rerun(
        session,
        principal,
        queue,
        review,
        force=bool(body and body.force),
        ignore_loc_limit=bool(body and body.ignore_loc_limit),
    )
    session.commit()
    return {"status": "queued"}


@router.post("/reviews/rerun", status_code=202, response_model=RerunManyOut)
def rerun_reviews(
    body: RerunManyIn,
    principal: RerunPrincipal,
    session: SessionDep,
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, Any]:
    """Review several results again (the checkboxes of "Kết quả review"): a finished one as a
    new round, a failed or skipped one in place, each under the rules of the single re-run.
    One that cannot run is reported with its reason and does not stop the others."""
    queued: list[int] = []
    failed: list[dict[str, Any]] = []
    for review_id in dict.fromkeys(body.ids):
        try:
            review = _visible_review(session, principal, review_id)
            _queue_rerun(
                session,
                principal,
                queue,
                review,
                force=review.status == "done",
                ignore_loc_limit=False,
            )
        except HTTPException as exc:
            failed.append({"id": review_id, "error": str(exc.detail)})
        else:
            queued.append(review_id)
    session.commit()
    return {"queued": queued, "failed": failed}


def _queue_rerun(
    session: Session,
    principal: Principal,
    queue: Enqueuer,
    review: Review,
    *,
    force: bool,
    ignore_loc_limit: bool,
) -> None:
    """Checks and enqueues one re-run (HTTPException when it may not run); the caller commits.
    Nothing is changed before every check passed, so a refused one leaves the review as it was."""
    repo = review_repo(session, review)
    if not _reruns(principal, review, repo):
        raise HTTPException(403, "only a manager of this dự án or an admin can do this")
    if review.status not in ("failed", "skipped") and not (force and review.status == "done"):
        raise HTTPException(
            409, f"review is {review.status}; only failed or skipped can be re-run (force for done)"
        )
    if review.superseded_by is not None:
        raise HTTPException(409, "a newer round of this review exists already")
    if review.rubric_version != RUBRIC_VERSION:
        # The worker reviews under the current rubric into a row of its own: this one would be
        # left queued for good. A fresh review is asked for from "Yêu cầu review" instead.
        raise HTTPException(
            409,
            f"reviewed under rubric {review.rubric_version}, now {RUBRIC_VERSION}; "
            "request a new review of this change instead",
        )
    if ignore_loc_limit and not (
        review.status == "skipped" and (review.error or "").startswith(TOO_LARGE)
    ):
        raise HTTPException(422, "ignore_loc_limit is only for a review skipped as too large")
    loc = review.loc_reviewed
    project, _, _ = _target(session, review)
    if project is None:
        raise HTTPException(409, "review target is no longer known")
    if not project.tracked:
        raise HTTPException(409, "repo is not tracked; resume tracking first")
    if not project.review_enabled:
        # The worker would skip it without a word and the page would wait for nothing.
        raise HTTPException(409, "review is turned off for this repo; turn it on first")
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        if mr is None:
            raise HTTPException(409, "merge request not found")
        if force and (mr.state != "opened" or mr.head_sha != review.reviewed_sha):
            raise HTTPException(409, "only an open MR at its current head can be reviewed again")
        args: tuple[Any, ...] = (project.id, mr.iid, review.reviewed_sha)
        func, job_id = (
            "review_mr_job",
            f"review-mr-{project.id}-{mr.iid}-{review.reviewed_sha[:12]}",
        )
    else:
        args = (project.id, review.reviewed_sha)
        func, job_id = "review_commit_job", f"review-commit-{review.reviewed_sha[:12]}"
    previous = review.status, review.error
    if review.status != "done":
        review.status, review.error = "queued", None
    # The row stays locked until this request commits: the worker's claim (SELECT … FOR UPDATE)
    # waits for it instead of being overwritten by this "queued".
    session.flush()
    try:
        review_queue(queue, asked=True, bp_id=project.business_project_id).enqueue(
            f"app.workers.review_jobs.{func}",
            *args,
            trigger="rerun",
            requested_by=principal.user_id,
            force=force,
            expected_round=review.round if force else None,
            ignore_loc_limit=ignore_loc_limit,
            job_id=f"{job_id}-rerun-{review.id}-{review.round + 1 if force else review.round}"
            + ("-nolimit" if ignore_loc_limit else ""),
            job_timeout=review_job_timeout(get_settings()),
            # A run past the LOC limit is paid in full each time: only a person repeats it.
            retry=None if ignore_loc_limit else REVIEW_RETRY,
        )
    except Exception:
        # Redis not answering: this one did not start; earlier ones of a batch did and are kept.
        log.exception("review rerun not queued", extra={"review_id": review.id})
        review.status, review.error = previous
        raise HTTPException(503, "job queue unavailable; try again") from None
    audit(
        session,
        principal.user_id,
        "review.rerun",
        f"review:{review.id}",
        force=force,
        ignore_loc_limit=ignore_loc_limit or None,
        loc=loc if ignore_loc_limit else None,
    )


@router.get("/findings", response_model=Page[BoardFindingOut])
def list_findings(
    principal: ViewPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    severity: Annotated[list[str] | None, Query()] = None,
    status: Literal[
        "open", "fixed", "disputed", "accepted_risk", "false_positive", "any", "counted"
    ] = "open",
    project_id: int | None = None,
    business_project_id: int | None = None,
    found_from: datetime | None = None,
    found_to: datetime | None = None,
    user_id: int | None = None,
    team_id: int | None = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[Any]:
    """Critical Board (docs/specs/10): findings of the latest review of each MR/commit.

    The dashboards link their counts here: `business_project_id` (a dự án's repos),
    `found_from`/`found_to` (reviews finished in [from, to), the dashboard's period),
    `status=any` (found in the period, whatever became of them since: dự án numbers) and
    `status=counted` (what team and person metrics count, app/scoring/aggregate.py: not a
    false positive, an active author, and `team_id` = the team the author was in that day).
    Those metrics are a snapshot of their last aggregation; this list is live, so a change
    since (a user deactivated, membership edited, a review rerun) shows here first."""
    if user_id is not None and not _may_filter_by(principal, user_id):
        raise HTTPException(403, "not allowed to see this person's findings")
    if status == "counted" and (found_from is None or found_to is None):
        # Only a period's metrics count this way; without one it matches no number shown.
        raise HTTPException(422, "status=counted needs found_from and found_to")
    query = scoped(
        select(ReviewFinding, Review, User)
        .join(Review, Review.id == ReviewFinding.review_id)
        .outerjoin(User, User.id == Review.author_user_id)
        .where(Review.status == "done"),
        principal,
    )
    if status == "counted":
        query = query.where(
            or_(ReviewFinding.status.is_(None), ReviewFinding.status != "false_positive"),
            Review.author_user_id.in_(select(User.id).where(User.active.is_not(False))),
        )
    elif status != "any":
        query = query.where(ReviewFinding.status == status)
    # The period of the change (Review.counted_at), as the score cards that link here count it.
    if found_from is not None:
        query = query.where(Review.counted_at >= found_from)
    if found_to is not None:
        query = query.where(Review.counted_at < found_to)
    if business_project_id is not None:
        repos = select(Project.id).where(Project.business_project_id == business_project_id)
        mr_ids = select(MergeRequest.id).where(MergeRequest.project_id.in_(repos))
        shas = select(Commit.sha).where(Commit.project_id.in_(repos))
        query = query.where(Review.mr_id.in_(mr_ids) | Review.commit_sha.in_(shas))
    if severity:
        query = query.where(ReviewFinding.severity.in_(severity))
    if user_id is not None:
        query = query.where(Review.author_user_id == user_id)
    if team_id is not None:
        if not principal.can_see_team(team_id):
            raise HTTPException(403, "not allowed to see this team")
        if status == "counted":
            # Membership on the review's day (REPORT_TZ), as the team's numbers attribute it.
            day = sql_cast(func.timezone(settings.report_tz, Review.counted_at), Date)
            query = query.where(
                exists().where(
                    TeamMember.team_id == team_id,
                    TeamMember.user_id == Review.author_user_id,
                    TeamMember.joined_at <= day,
                    or_(TeamMember.left_at.is_(None), TeamMember.left_at > day),
                )
            )
        else:
            members = select(TeamMember.user_id).where(
                TeamMember.team_id == team_id, TeamMember.left_at.is_(None)
            )
            query = query.where(Review.author_user_id.in_(members))
    if project_id is not None:
        mr_ids = select(MergeRequest.id).where(MergeRequest.project_id == project_id)
        shas = select(Commit.sha).where(Commit.project_id == project_id)
        query = query.where(Review.mr_id.in_(mr_ids) | Review.commit_sha.in_(shas))
    if (after := decode_cursor(cursor)) is not None:
        query = query.where(ReviewFinding.id < after)
    rows = session.execute(query.order_by(ReviewFinding.id.desc()).limit(limit + 1)).tuples().all()
    now = datetime.now(UTC)
    items = []
    for finding, review, user in rows[:limit]:
        project, url, label = _target(session, review)
        opened = review.counted_at or now  # open since the change was committed
        items.append(
            {
                **FindingOut.model_validate(finding).model_dump(),
                "project": project.path_with_namespace if project else "",
                "author": user.full_name if user else None,
                "author_user_id": review.author_user_id,
                "target": label,
                "web_url": url,
                "age_days": max(0, (now - opened).days),
                "committed_at": review.counted_at,
            }
        )
    more = len(rows) > limit
    return Page(items=items, next_cursor=encode_cursor(rows[limit - 1][0].id) if more else None)


# -- tying an unmapped author to a person from the review list -----------------------------------
# Who may: admin, or a manager of the review's dự án (they already see its reviews whose author
# is not mapped). "Đồng bộ" gives every review of that git email / GitLab account the person and
# re-scores their weeks and months (app/identity/attribution.py), like Admin > Tài khoản.

AUTHOR_PEOPLE_DAYS = 90  # a manager links to people of the dự án: granted, or committed this long


def _author_account_ids(session: Session, reviews: list[Review]) -> dict[int, int]:
    """review id -> the account of its commit / MR author (when the change is synced)."""
    shas = {r.commit_sha for r in reviews if r.mr_id is None and r.commit_sha}
    mr_ids = {r.mr_id for r in reviews if r.mr_id is not None}
    by_sha: dict[str, int | None] = {}
    if shas:
        sha_rows = session.execute(
            select(Commit.sha, Commit.author_account_id).where(Commit.sha.in_(shas))
        ).all()
        by_sha = {sha: acc for sha, acc in sha_rows}
    by_mr: dict[int, int | None] = {}
    if mr_ids:
        mr_rows = session.execute(
            select(MergeRequest.id, MergeRequest.author_account_id).where(
                MergeRequest.id.in_(mr_ids)
            )
        ).all()
        by_mr = {mid: acc for mid, acc in mr_rows}
    out: dict[int, int] = {}
    for review in reviews:
        if review.mr_id is not None:
            account_id = by_mr.get(review.mr_id)
        else:
            account_id = by_sha.get(review.commit_sha or "")
        if account_id is not None:
            out[review.id] = account_id
    return out


def _account_out(account: Account, user: User | None, can_link: bool) -> ReviewAuthorAccount:
    return ReviewAuthorAccount(
        id=account.id,
        kind=account.kind,
        name=account.display_name,
        email=account.external_key if account.kind == "git_email" else None,
        user=UserOut.model_validate(user) if user else None,
        can_link=can_link,
    )


def _review_author(
    session: Session, principal: Principal, review_id: int
) -> tuple[Review, int | None, Account]:
    review = _visible_review(session, principal, review_id)
    project = review_repo(session, review)
    bp_id = project.business_project_id if project else None
    if not _may_link(principal, bp_id):
        raise HTTPException(403, "only a manager of this dự án or an admin can link authors")
    account_id = _author_account_ids(session, [review]).get(review.id)
    account = session.get(Account, account_id) if account_id else None
    if account is None:
        raise HTTPException(409, "Chưa biết tác giả của thay đổi này (chưa sync); chưa gắn được.")
    return review, bp_id, account


def _link_candidates(session: Session, principal: Principal, bp_id: int | None) -> list[User]:
    if principal.is_admin:
        return list(
            session.scalars(
                select(User).where(User.active.is_not(False)).order_by(User.full_name, User.id)
            )
        )
    since = datetime.now(UTC) - timedelta(days=AUTHOR_PEOPLE_DAYS)
    return reviewable_people(session, principal, [bp_id] if bp_id is not None else [], since)


def _pending_reviews(session: Session, account_id: int) -> int:
    """Reviews of this author that have no person (what a sync gives the person)."""
    shas = select(Commit.sha).where(Commit.author_account_id == account_id)
    mrs = select(MergeRequest.id).where(MergeRequest.author_account_id == account_id)
    count = session.scalar(
        select(func.count())
        .select_from(Review)
        .where(
            Review.author_user_id.is_(None),
            or_(Review.commit_sha.in_(shas), Review.mr_id.in_(mrs)),
        )
    )
    return int(count or 0)


@router.get("/reviews/{review_id}/author", response_model=ReviewAuthorOut)
def review_author(
    review_id: int, principal: AuthorPrincipal, session: SessionDep
) -> ReviewAuthorOut:
    """The "Gắn" form of a review without a person: its author, who it may go to."""
    _, bp_id, account = _review_author(session, principal, review_id)
    candidates = _link_candidates(session, principal, bp_id)
    user = session.get(User, account.user_id) if account.user_id else None
    if user is not None and not principal.is_admin and user.id not in {u.id for u in candidates}:
        user = None  # someone outside the manager's dự án: not theirs to see (nor to move)
    return ReviewAuthorOut(
        account=_account_out(account, user, True),
        candidates=[UserOut.model_validate(u) for u in candidates],
        can_create=principal.is_admin and account.kind == "git_email",
        reviews=_pending_reviews(session, account.id),
    )


@router.post("/reviews/{review_id}/author", response_model=AccountLinked)
@busy_as_409()
def link_review_author(
    review_id: int,
    body: ReviewAuthorLink,
    principal: AuthorPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> AccountLinked:
    """Tie the author of a review (their git email or GitLab account) to a person; with sync,
    every review of that author follows and the affected periods are re-scored."""
    review, bp_id, account = _review_author(session, principal, review_id)
    # A running GitLab sync may hold the account's row: wait seconds, not minutes (locks.py).
    short_lock_wait(session)
    # Row lock: an admin linking the same account meanwhile cannot be silently overwritten.
    locked = session.get(Account, account.id, with_for_update=True, populate_existing=True)
    if locked is None:  # deleted meanwhile
        raise HTTPException(409, "Tài khoản tác giả vừa bị xoá; tải lại trang.")
    account = locked
    previous = account.user_id
    if (
        previous is not None
        and not principal.is_admin
        and (body.create or body.user_id != previous)
    ):
        # Re-pointing someone's account moves their reviews and scores: the admin's call.
        raise HTTPException(409, "Tác giả này đã gắn với người khác; chỉ admin đổi được.")
    if body.create:
        if not principal.is_admin:
            raise HTTPException(403, "Chỉ admin tạo được người mới.")
        if account.kind != "git_email":
            raise HTTPException(422, "Chỉ tạo người mới được từ email git của commit.")
        email = account.external_key.strip().lower()
        taken = select(User.id).where(func.lower(User.primary_email) == email)
        if session.scalars(taken).first() is not None:
            raise HTTPException(409, f"Đã có người dùng email {email}; hãy chọn người đó.")
        user = User(
            full_name=(account.display_name or email.partition("@")[0]).strip(),
            primary_email=email,
        )
        session.add(user)
    elif body.user_id is not None:
        found = session.get(User, body.user_id)
        if found is None or found.active is False:
            raise HTTPException(404, "person not found")
        allowed = {u.id for u in _link_candidates(session, principal, bp_id)}
        if not principal.is_admin and found.id not in allowed:
            raise HTTPException(403, "Người này không thuộc dự án bạn quản lý.")
        user = found
    else:
        raise HTTPException(422, "Chọn một người (user_id) hoặc tạo mới (create: true).")
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Email vừa được dùng cho người khác; thử lại.") from None
    if previous == user.id and not body.sync:
        return AccountLinked.model_validate(  # nothing changes: no audit row
            {**AccountOut.model_validate(account).model_dump(), "reviews_attributed": 0}
        )
    account.user_id = user.id
    session.flush()
    attribution = (
        attribute_reviews(
            session,
            ZoneInfo(settings.report_tz),
            [account.id],
            unassigned_only=not principal.is_admin,
        )
        if body.sync
        else Attribution()
    )
    audit(
        session,
        principal.user_id,
        "account.link",
        f"account:{account.id}",
        user_id=user.id,
        previous_user_id=previous,
        created=body.create,
        backfill=body.sync,
        via=f"review:{review.id}",
        reviews_attributed=attribution.reviews,
    )
    session.commit()
    queued = rebuild_after_commit(queue, attribution)
    return AccountLinked.model_validate(
        {
            **AccountOut.model_validate(account).model_dump(),
            "reviews_attributed": attribution.reviews,
            "periods_queued": queued,
        }
    )
