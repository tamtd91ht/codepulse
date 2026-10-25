"""Reviews in progress (docs/specs/17 §Tiến trình): what the workers are reviewing right now,
what waits in the queue and what just ended, for the dashboard's live indicator.

Running and ended reviews come from the worker's Redis progress records (the review row itself
is not committed until the review ends); waiting ones from the RQ queue's job ids. Each person
sees the work they may review (themself, people they lead; admins everything; an unmapped
author needs a lead or admin) and only a count of the rest. Work whose author is not known yet
(not synced) is shown to admins only.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from app.api.deps import get_queue, get_session
from app.api.schemas import ActivityItem, ActivityOut, LiveReviewOut
from app.api.security import CurrentPrincipal, Principal
from app.db import get_redis
from app.models import Account, Commit, Connection, MergeRequest, Project, Review, User
from app.review.progress import ProgressReader, RedisProgress, queued_reviews

router = APIRouter(tags=["reviews"])
SessionDep = Annotated[Session, Depends(get_session)]
MAX_QUEUED_SHOWN = 20
RECENT_WINDOW = timedelta(minutes=15)

Backlog = Callable[[], list[dict[str, Any]]]
Key = tuple[str, int, str]  # (kind, project_id, ref)
ProjectRow = tuple[Project, str | None]


def get_progress() -> ProgressReader:
    return RedisProgress(get_redis())


def get_backlog() -> Backlog:
    queue = get_queue()
    return lambda: queued_reviews(queue)


@router.get("/activity/reviews", response_model=ActivityOut)
def review_activity(
    principal: CurrentPrincipal,
    session: SessionDep,
    progress: Annotated[ProgressReader, Depends(get_progress)],
    backlog: Annotated[Backlog, Depends(get_backlog)],
) -> ActivityOut:
    running_raw = progress.active()
    since = datetime.now(UTC) - RECENT_WINDOW
    recent_raw = [r for r in progress.recent() if _after(r.get("ended_at"), since)]
    queued_raw = _resolve_queued(session, backlog())
    projects = _projects(
        session,
        {int(i.get("project_id") or 0) for i in running_raw + recent_raw}
        | {i["project_id"] for i in queued_raw},
    )
    names = _names(
        session, {i["author_user_id"] for i in queued_raw if i["author_user_id"] is not None}
    )

    hidden = 0
    running: list[ActivityItem] = []
    for item in running_raw:
        project = projects.get(int(item.get("project_id") or 0))
        if project is None or not principal.may_review_author(item.get("author_user_id")):
            hidden += 1
            continue
        running.append(_from_record("running", item, project))

    recent: list[ActivityItem] = []
    for item in recent_raw:
        project = projects.get(int(item.get("project_id") or 0))
        if project is not None and principal.may_review_author(item.get("author_user_id")):
            recent.append(_from_record("ended", item, project))

    queued: list[ActivityItem] = []
    visible_queued = 0
    for item in queued_raw:
        project = projects.get(item["project_id"])
        if project is None or not _may_see_queued(principal, item):
            hidden += 1
            continue
        visible_queued += 1
        if len(queued) >= MAX_QUEUED_SHOWN:
            continue
        author_id = item["author_user_id"]
        queued.append(
            ActivityItem(
                state="queued",
                kind=item["kind"],
                project_id=item["project_id"],
                project=project[0].path_with_namespace,
                ref=item["ref"],
                sha=item["sha"],
                title=item["title"],
                author=names.get(author_id) if author_id else None,
                author_user_id=author_id,
                trigger=item["trigger"],
                web_url=_web_url(project, item["kind"], item["ref"]),
            )
        )
    return ActivityOut(
        running=running,
        queued=queued,
        queued_total=visible_queued,
        recent=recent,
        hidden=hidden,
    )


@router.get("/activity/reviews/{review_id}/live", response_model=LiveReviewOut)
def live_review(
    review_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    progress: Annotated[ProgressReader, Depends(get_progress)],
) -> LiveReviewOut:
    """What the deep pass did so far (tools, paths, patterns; never file contents). Kept an hour
    after the review; same visibility as the review itself."""
    record = next((r for r in progress.active() if r.get("review_id") == review_id), None) or next(
        (r for r in progress.recent() if r.get("review_id") == review_id), None
    )
    if record is not None:
        author, running = record.get("author_user_id"), "stage" in record
    else:
        review = session.get(Review, review_id)  # ended more than a few minutes ago
        if review is None:
            raise HTTPException(404, "review not found")
        author, running = review.author_user_id, False
    if not principal.may_review_author(author):
        raise HTTPException(404, "review not found")
    return LiveReviewOut(review_id=review_id, running=running, events=progress.live(review_id))


def _may_see_queued(principal: Principal, item: dict[str, Any]) -> bool:
    if not item["found"]:  # not synced yet: its author is unknown, not "unmapped"
        return principal.is_admin
    return principal.may_review_author(item["author_user_id"])


def _from_record(
    state: Literal["running", "ended"], item: dict[str, Any], project: ProjectRow
) -> ActivityItem:
    ref = str(item.get("ref") or "")
    live = state == "running"
    return ActivityItem(
        state=state,
        review_id=item.get("review_id"),
        kind=item["kind"],
        project_id=project[0].id,
        project=project[0].path_with_namespace,
        ref=ref,
        sha=str(item.get("sha") or ""),
        title=item.get("title") or "",
        author=item.get("author"),
        author_user_id=item.get("author_user_id"),
        requested_by=item.get("requested_by"),
        trigger=item.get("trigger") or "",
        round=int(item.get("round") or 1),
        started_at=item.get("started_at"),
        stage=item.get("stage") if live else None,
        detail=(item.get("detail") or "") if live else "",
        stage_at=item.get("stage_at") if live else None,
        loc=item.get("loc"),
        files=item.get("files"),
        status=None if live else item.get("status"),
        ended_at=None if live else item.get("ended_at"),
        web_url=_web_url(project, item["kind"], ref),
    )


def _after(value: Any, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(value)) >= since
    except ValueError:
        return False


def _projects(session: Session, ids: set[int]) -> dict[int, ProjectRow]:
    """Tracked projects by id with their connection's base URL."""
    ids.discard(0)
    if not ids:
        return {}
    rows = session.execute(
        select(Project, Connection.base_url)
        .outerjoin(Connection, Connection.id == Project.connection_id)
        .where(Project.id.in_(ids), Project.tracked)
    ).all()
    return {p.id: (p, base) for p, base in rows}


def _web_url(project: ProjectRow, kind: str, ref: str) -> str | None:
    if not project[1]:
        return None
    path = f"/-/merge_requests/{ref}" if kind == "mr" else f"/-/commit/{ref}"
    return f"{project[1]}/{project[0].path_with_namespace}{path}"


def _resolve_queued(session: Session, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill project, title and author of queued jobs from the synced MRs and commits. A commit
    job id carries a sha prefix only; its project comes from the commit row. A commit that is
    not synced yet has no known project and is left out (it cannot be scoped)."""
    mrs: dict[Key, tuple[str, int | None]] = {}
    wanted = {(i["project_id"], int(i["ref"])) for i in items if i["kind"] == "mr"}
    if wanted:
        for project_id, iid, title, user_id in session.execute(
            select(MergeRequest.project_id, MergeRequest.iid, MergeRequest.title, Account.user_id)
            .outerjoin(Account, Account.id == MergeRequest.author_account_id)
            .where(tuple_(MergeRequest.project_id, MergeRequest.iid).in_(wanted))
        ).all():
            if project_id is not None:
                mrs[("mr", project_id, str(iid))] = ((title or "")[:120], user_id)
    commits: dict[str, tuple[str, int, str, int | None]] = {}
    prefixes = {i["ref"] for i in items if i["kind"] == "commit"}
    if prefixes:
        # Hex-only prefixes (checked by the job id pattern): nothing to escape in LIKE.
        for sha, project_id, message, user_id in session.execute(
            select(Commit.sha, Commit.project_id, Commit.message, Account.user_id)
            .outerjoin(Account, Account.id == Commit.author_account_id)
            .where(or_(*(Commit.sha.startswith(p) for p in prefixes)))
            .limit(len(prefixes) * 2)
        ).all():
            if project_id is None:
                continue
            first = (message or "").strip().splitlines()[:1]
            title = (first[0] if first else sha[:8])[:120]
            for prefix in prefixes:
                if sha.startswith(prefix):
                    commits[prefix] = (sha, project_id, title, user_id)
    out: list[dict[str, Any]] = []
    for item in items:
        if item["kind"] == "mr":
            found_mr = mrs.get(("mr", item["project_id"], item["ref"]))
            title, author = found_mr if found_mr else ("", None)
            out.append({**item, "title": title, "author_user_id": author, "found": bool(found_mr)})
        elif (found := commits.get(item["ref"])) is not None:
            sha, project_id, title, author = found
            out.append(
                {
                    **item,
                    "project_id": project_id,
                    "ref": sha,
                    "sha": sha,
                    "title": title,
                    "author_user_id": author,
                    "found": True,
                }
            )
    return out


def _names(session: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = session.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all()
    return {uid: name or f"#{uid}" for uid, name in rows}
