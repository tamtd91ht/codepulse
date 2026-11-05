"""What the MCP tools read (docs/specs/26 §3-6): the token owner's open critical/high findings,
those of a commit or an MR, one finding with its close-request history, and the owner's own
close requests.

Visibility is the dashboard's, never a parallel model: lists are filtered by `work_scope`
(`review.view`, app/api/access.py) and single reviews go through `_visible_review`, the check
of the review page. Errors are `McpError` (a code and a Vietnamese sentence saying what to do).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import ColumnElement, String, case, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.access import team_reach, whole_projects, work_scope
from app.api.routers.closures import parse_commit
from app.api.routers.reviews import _visible_review
from app.api.security import Principal
from app.models import (
    Account,
    BusinessProject,
    Commit,
    Connection,
    FindingClosure,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    Team,
    TeamMember,
    User,
)
from app.models.review import CLOSURE_OPEN, CLOSURE_STATUSES
from app.review.status import SEVERE

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
REPEAT_DAYS = 90
# HTTP status of a shared business function -> the tool error code.
HTTP_CODES = {
    400: "invalid_argument",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "invalid_argument",
    429: "limit_reached",
}


class McpError(Exception):
    """A tool error: `code` for programs, `message` (Vietnamese) for Claude and the dev."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def from_http(exc: HTTPException) -> McpError:
    """The same refusal the dashboard gives, as a tool error."""
    return McpError(HTTP_CODES.get(exc.status_code, "error"), str(exc.detail))


@dataclass
class FindingView:
    finding: ReviewFinding
    review: Review
    project: Project | None
    mr: MergeRequest | None
    pending: FindingClosure | None = None
    repeats: int = 0
    repo_url: str | None = None  # the repo on GitLab (web), for links
    author: str | None = None  # the review author's name


# -- arguments -------------------------------------------------------------------------------------


def severities(wanted: list[str] | None) -> tuple[str, ...]:
    """critical/high, or a part of it (never wider, docs/specs/26 §4)."""
    if not wanted:
        return SEVERE
    clean = {s.strip().lower() for s in wanted}
    wrong = sorted(clean - set(SEVERE))
    if wrong:
        raise McpError(
            "invalid_argument",
            f"severity chỉ nhận critical/high (không có {', '.join(wrong)}): "
            "MCP chỉ trả các lỗi phải sửa",
        )
    return tuple(s for s in SEVERE if s in clean)


def _day(raw: str, name: str) -> date:
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        raise McpError(
            "invalid_argument", f"{name} cần ngày ISO (YYYY-MM-DD), nhận {raw!r}"
        ) from None


def _midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def since_date(raw: str | None, tz: ZoneInfo | None = None) -> datetime | None:
    """The start of that day (REPORT_TZ, else UTC): reviews counted from it on."""
    if not raw or not raw.strip():
        return None
    return _midnight(_day(raw, "since"), tz or ZoneInfo("UTC"))


def until_date(raw: str | None, tz: ZoneInfo | None = None) -> datetime | None:
    """The end of that day (exclusive bound: the next midnight), so `until` includes the day."""
    if not raw or not raw.strip():
        return None
    return _midnight(_day(raw, "until") + timedelta(days=1), tz or ZoneInfo("UTC"))


def resolve_user(session: Session, principal: Principal, user: str | None) -> int:
    """Whose findings: the token's owner by default; someone else (email or id) only when the
    owner's `review.view` reaches beyond their own work (team / project / all). What exactly
    they see of that person is then `work_scope`. One whose reach is their own work only gets
    `forbidden` for anyone else, existing or not (no lookup of who exists)."""
    if user is None or not user.strip() or user.strip() == str(principal.user_id):
        return principal.user_id
    text = user.strip()
    reaches_others = (
        principal.is_admin
        or bool(team_reach(principal, "review.view"))
        or bool(whole_projects(principal, "review.view"))
    )
    found: int | None = None
    if text.isdigit():
        row = session.get(User, int(text))
        found = row.id if row is not None else None
    else:
        email = text.lower()
        found = session.scalar(select(User.id).where(func.lower(User.primary_email) == email))
        if found is None:
            found = session.scalar(
                select(Account.user_id).where(
                    Account.kind == "git_email",
                    func.lower(Account.external_key) == email,
                    Account.user_id.is_not(None),
                )
            )
    if found is not None and found == principal.user_id:
        return found
    if not reaches_others:
        raise McpError(
            "forbidden",
            f"không có quyền xem finding của {text}: quyền xem review của bạn chỉ phủ việc của "
            "chính bạn (bỏ tham số user)",
        )
    if found is None:
        raise McpError("not_found", f"không tìm thấy người dùng {text!r} (email hoặc id)")
    reach = (
        principal.is_admin
        or found in team_reach(principal, "review.view")
        or bool(whole_projects(principal, "review.view"))
    )
    if not reach:
        raise McpError(
            "forbidden",
            f"không có quyền xem finding của {text}: quyền xem review của bạn không phủ người này",
        )
    return found


def resolve_repo(session: Session, principal: Principal, repo: str) -> Project:
    """A repo by its GitLab path (group/name) or its id (CodePulse's, else GitLab's), among the
    dự án this person sees (docs/specs/09: the others do not exist for them). A number that is
    the CodePulse id of one visible repo and the GitLab id of another is `ambiguous_repo`."""
    text = repo.strip().strip("/")
    project: Project | None = None
    if text.isdigit():
        seen = principal.can_view_project
        own = session.get(Project, int(text))
        own = own if own is not None and seen(own.business_project_id) else None
        gitlab = next(
            (
                p
                for p in session.scalars(
                    select(Project).where(Project.external_id == int(text)).order_by(Project.id)
                )
                if seen(p.business_project_id)
            ),
            None,
        )
        if own is not None and gitlab is not None and own.id != gitlab.id:
            raise McpError(
                "ambiguous_repo",
                f"repo {text} vừa là id CodePulse của {own.path_with_namespace} vừa là id GitLab "
                f"của {gitlab.path_with_namespace}: truyền đường dẫn GitLab (group/name)",
            )
        project = own or gitlab
    else:
        if "://" in text:  # a pasted GitLab URL: keep the path
            text = text.split("://", 1)[1].split("/", 1)[-1].split("/-/", 1)[0].strip("/")
        project = session.scalars(
            select(Project)
            .where(func.lower(Project.path_with_namespace) == text.lower())
            .order_by(Project.tracked.desc(), Project.id)
        ).first()
    if project is None or not principal.can_view_project(project.business_project_id):
        raise McpError(
            "not_found", f"không tìm thấy repo {repo!r} (đường dẫn GitLab như group/name, hoặc id)"
        )
    return project


# -- the findings ----------------------------------------------------------------------------------


def _base() -> Any:
    """findings + review + repo + MR (a review is of an MR or of a commit)."""
    return (
        select(ReviewFinding, Review, Project, MergeRequest)
        .join(Review, Review.id == ReviewFinding.review_id)
        .outerjoin(MergeRequest, MergeRequest.id == Review.mr_id)
        .outerjoin(Commit, Commit.sha == Review.commit_sha)
        .outerjoin(Project, Project.id == func.coalesce(MergeRequest.project_id, Commit.project_id))
    )


def _still_to_fix(sev: tuple[str, ...], include_disputed: bool) -> list[ColumnElement[bool]]:
    statuses = ["open", "disputed"] if include_disputed else ["open"]
    return [
        ReviewFinding.severity.in_(sev),
        ReviewFinding.status.in_(statuses),
        ReviewFinding.verified != "rejected",
    ]


def _in_flight() -> ColumnElement[bool]:
    return exists().where(
        FindingClosure.finding_id == ReviewFinding.id, FindingClosure.status.in_(CLOSURE_OPEN)
    )


def _top_order() -> list[Any]:
    """docs/specs/26 §5: critical, then confirmed, then confidence, then newest review, id."""
    return [
        case((ReviewFinding.severity == "critical", 0), else_=1),
        case((ReviewFinding.verified == "confirmed", 0), else_=1),
        ReviewFinding.confidence.desc().nulls_last(),
        Review.finished_at.desc().nulls_last(),
        Review.id.desc(),
        ReviewFinding.id,
    ]


FINDING_STATUSES = ("open", "disputed", "fixed", "accepted_risk", "false_positive", "any")
Order = Literal["priority", "newest"]
MAX_OFFSET = 1000


def resolve_business_project(session: Session, principal: Principal, raw: str) -> BusinessProject:
    """A dự án by id or name (case-insensitive) among those this person sees."""
    text = raw.strip()
    found: BusinessProject | None = None
    if text.isdigit():
        found = session.get(BusinessProject, int(text))
    if found is None:
        found = session.scalars(
            select(BusinessProject).where(func.lower(BusinessProject.name) == text.lower())
        ).first()
    if found is None or not principal.can_view_project(found.id):
        raise McpError("not_found", f"không tìm thấy dự án {raw!r} (tên hoặc id)")
    return found


def resolve_team(session: Session, principal: Principal, raw: str) -> Team:
    """A team by id or name (case-insensitive) that this person may filter by (as on the
    dashboard: own teams, teams led or managed, admin)."""
    text = raw.strip()
    found: Team | None = None
    if text.isdigit():
        found = session.get(Team, int(text))
    if found is None:
        found = session.scalars(select(Team).where(func.lower(Team.name) == text.lower())).first()
    if found is None or not principal.can_see_team(found.id):
        raise McpError(
            "not_found", f"không tìm thấy team {raw!r} (tên hoặc id) trong phạm vi của bạn"
        )
    return found


def _statuses(status: str | None, include_disputed: bool) -> list[str] | None:
    """The finding statuses wanted; None: any."""
    wanted = (status or "open").strip().lower()
    if wanted not in FINDING_STATUSES:
        raise McpError(
            "invalid_argument", f"status chỉ nhận {', '.join(FINDING_STATUSES)}, nhận {status!r}"
        )
    if wanted == "any":
        return None
    if wanted == "open" and include_disputed:
        return ["open", "disputed"]
    return [wanted]


@dataclass
class ListResult:
    views: list[FindingView]
    owner: int | None  # whose findings (None: a team's)
    team: Team | None = None
    business_project: BusinessProject | None = None
    next_offset: int | None = None


def list_findings(
    session: Session,
    principal: Principal,
    *,
    user: str | None = None,
    severity: list[str] | None = None,
    repo: str | None = None,
    business_project: str | None = None,
    team: str | None = None,
    since: str | None = None,
    until: str | None = None,
    status: str | None = None,
    include_disputed: bool = False,
    include_pending: bool = False,
    order: Order = "priority",
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    tz: ZoneInfo | None = None,
) -> ListResult:
    """Critical/high findings with the Critical Board's filters (docs/specs/26 §4, §5):
    one person (default the token's owner) or a team's current members, a dự án or a repo,
    the review period [since, until] (Review.counted_at, days in REPORT_TZ, both inclusive),
    the finding status (default open: still to fix). Paged by offset."""
    team_row = resolve_team(session, principal, team) if team and team.strip() else None
    owner: int | None = None
    if team_row is None or (user and user.strip()):
        owner = resolve_user(session, principal, user)
    statuses = _statuses(status, include_disputed)
    query = _base().where(Review.status == "done", ReviewFinding.severity.in_(severities(severity)))
    if statuses is not None:
        query = query.where(ReviewFinding.status.in_(statuses))
    if statuses is not None and "open" in statuses:
        # A rejected one is not still to fix (§4); other statuses list what was decided.
        query = query.where(ReviewFinding.verified != "rejected")
    if owner is not None:
        query = query.where(Review.author_user_id == owner)
    if team_row is not None:
        members = select(TeamMember.user_id).where(
            TeamMember.team_id == team_row.id, TeamMember.left_at.is_(None)
        )
        query = query.where(Review.author_user_id.in_(members))
    if (scope := work_scope(principal)) is not None:
        query = query.where(scope)
    if repo and repo.strip():
        query = query.where(Project.id == resolve_repo(session, principal, repo).id)
    bp_row = None
    if business_project and business_project.strip():
        bp_row = resolve_business_project(session, principal, business_project)
        query = query.where(Project.business_project_id == bp_row.id)
    start, end = since_date(since, tz), until_date(until, tz)
    if start is not None and end is not None and start >= end:
        raise McpError("invalid_argument", "since phải trước hoặc bằng until")
    if start is not None:
        query = query.where(Review.counted_at >= start)
    if end is not None:
        query = query.where(Review.counted_at < end)
    if not include_pending:
        query = query.where(~_in_flight())
    if not 0 <= offset <= MAX_OFFSET:
        raise McpError("invalid_argument", f"offset trong khoảng 0-{MAX_OFFSET}")
    size = max(1, min(limit, MAX_LIMIT))
    ordering = (
        [Review.counted_at.desc().nulls_last(), Review.id.desc(), ReviewFinding.id.desc()]
        if order == "newest"
        else _top_order()
    )
    rows = session.execute(query.order_by(*ordering).offset(offset).limit(size + 1)).all()
    more = len(rows) > size
    return ListResult(
        views=_views(session, rows[:size]),
        owner=owner,
        team=team_row,
        business_project=bp_row,
        next_offset=offset + size if more else None,
    )


def _views(session: Session, rows: Any) -> list[FindingView]:
    views = [FindingView(finding=f, review=r, project=p, mr=m) for f, r, p, m in rows]
    ids = [v.finding.id for v in views]
    if ids:
        pending = {
            c.finding_id: c
            for c in session.scalars(
                select(FindingClosure).where(
                    FindingClosure.finding_id.in_(ids), FindingClosure.status.in_(CLOSURE_OPEN)
                )
            )
        }
        repeats = repeat_counts(session, [v.finding.fingerprint for v in views])
        urls = repo_urls(session, [v.project for v in views])
        author_ids = {v.review.author_user_id for v in views if v.review.author_user_id}
        names = (
            {
                int(uid): name
                for uid, name in session.execute(
                    select(User.id, User.full_name).where(User.id.in_(author_ids))
                )
            }
            if author_ids
            else {}
        )
        for view in views:
            view.author = names.get(view.review.author_user_id or 0)
            view.pending = pending.get(view.finding.id)
            view.repeats = repeats.get(view.finding.fingerprint or "", 0)
            view.repo_url = urls.get(view.project.id) if view.project else None
    return views


def repo_urls(session: Session, projects: list[Project | None]) -> dict[int, str]:
    """project id -> its GitLab web URL (the connection's base URL, never its token)."""
    known = {p.id: p for p in projects if p is not None}
    wanted = {p.connection_id for p in known.values() if p.connection_id is not None}
    if not wanted:
        return {}
    rows = session.execute(
        select(Connection.id, Connection.base_url).where(Connection.id.in_(wanted))
    ).all()
    bases = {int(cid): str(url) for cid, url in rows}
    return {
        pid: f"{bases[p.connection_id].rstrip('/')}/{p.path_with_namespace}"
        for pid, p in known.items()
        if p.connection_id in bases
    }


def repeat_counts(
    session: Session, fingerprints: list[str | None], now: datetime | None = None
) -> dict[str, int]:
    """In how many changes (MRs or commits) the same fingerprint was reported in the last
    REPEAT_DAYS days: a mistake made again and again (§7). Re-reviews of one change count
    once."""
    wanted = sorted({f for f in fingerprints if f})
    if not wanted:
        return {}
    start = (now or datetime.now(UTC)) - timedelta(days=REPEAT_DAYS)
    change = func.coalesce(cast(Review.mr_id, String), Review.commit_sha)
    rows = session.execute(
        select(ReviewFinding.fingerprint, func.count(func.distinct(change)))
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(ReviewFinding.fingerprint.in_(wanted), ReviewFinding.created_at >= start)
        .group_by(ReviewFinding.fingerprint)
    ).all()
    return {str(fp): int(n) for fp, n in rows}


def _review_findings(
    session: Session, review: Review, include_disputed: bool = True
) -> list[FindingView]:
    """Every critical/high still open of one review, a pending request shown, not hidden."""
    rows = session.execute(
        _base()
        .where(ReviewFinding.review_id == review.id, *_still_to_fix(SEVERE, include_disputed))
        .order_by(*_top_order())
    ).all()
    return _views(session, rows)


def _visible(session: Session, principal: Principal, review: Review) -> None:
    try:
        _visible_review(session, principal, review.id)
    except HTTPException as exc:
        raise from_http(exc) from None


def commit_findings(
    session: Session, principal: Principal, sha: str, repo: str | None = None
) -> tuple[Review, list[FindingView]]:
    """The latest round of a commit's review and its open critical/high findings."""
    try:
        wanted = parse_commit(sha)
    except ValueError as exc:
        raise McpError("invalid_argument", f"{exc} (cần ≥7 ký tự hex hoặc link commit)") from None
    query = select(Commit).where(Commit.sha.startswith(wanted, autoescape=True))
    if repo:
        query = query.where(Commit.project_id == resolve_repo(session, principal, repo).id)
    elif (seen := principal.visible_projects()) is not None:
        # Only the repos this person sees count (the others do not exist for them, docs/specs/09)
        # before deciding "not found" / "ambiguous"; their own work stays theirs wherever it is.
        query = query.where(
            or_(
                Commit.project_id.in_(
                    select(Project.id).where(Project.business_project_id.in_(seen))
                ),
                exists().where(
                    Review.commit_sha == Commit.sha, Review.author_user_id == principal.user_id
                ),
            )
        )
    commits = list(session.scalars(query.limit(20)))
    if not commits:
        raise McpError(
            "not_found",
            f"CodePulse chưa có commit {wanted[:12]}: commit đã push chưa, hoặc repo chưa được "
            "theo dõi",
        )
    repos = {c.project_id for c in commits}
    if len(repos) > 1:
        paths = sorted(
            p.path_with_namespace
            for p in session.scalars(select(Project).where(Project.id.in_(repos)))
        )
        raise McpError(
            "ambiguous_sha",
            f"SHA {wanted} trùng ở nhiều repo ({', '.join(paths)}): truyền thêm repo",
        )
    if len(commits) > 1:
        raise McpError("ambiguous_sha", f"SHA {wanted} trùng nhiều commit: dùng SHA dài hơn")
    commit = commits[0]
    review = session.scalars(
        select(Review)
        .where(Review.commit_sha == commit.sha, Review.status == "done")
        .order_by(Review.round.desc(), Review.id.desc())
    ).first()
    if review is None:
        raise McpError(
            "not_reviewed",
            f"commit {commit.sha[:12]} chưa có review xong (có thể nằm trong một MR: dùng "
            "get_mr_findings)",
        )
    _visible(session, principal, review)
    return review, _review_findings(session, review)


def mr_findings(
    session: Session, principal: Principal, repo: str, iid: int
) -> tuple[Review, list[FindingView]]:
    """The latest finished review of an MR (its newest head) and its open critical/high."""
    project = resolve_repo(session, principal, repo)
    mr = session.scalar(
        select(MergeRequest).where(MergeRequest.project_id == project.id, MergeRequest.iid == iid)
    )
    if mr is None:
        raise McpError("not_found", f"không tìm thấy MR !{iid} ở {project.path_with_namespace}")
    review = session.scalars(
        select(Review)
        .where(Review.mr_id == mr.id, Review.status == "done")
        .order_by(Review.finished_at.desc().nulls_last(), Review.id.desc())
    ).first()
    if review is None:
        raise McpError("not_reviewed", f"MR !{iid} chưa có review xong")
    _visible(session, principal, review)
    return review, _review_findings(session, review)


def finding_view(session: Session, principal: Principal, finding_id: int) -> FindingView:
    """One finding the person may see (as on its review page), any severity or status."""
    finding = session.get(ReviewFinding, finding_id)
    if finding is None or finding.review_id is None:
        raise McpError("not_found", f"không có finding #{finding_id}")
    rows = session.execute(_base().where(ReviewFinding.id == finding_id)).all()
    view = _views(session, rows)[0]
    _visible(session, principal, view.review)
    return view


def closure_history(session: Session, finding_id: int) -> list[FindingClosure]:
    return list(
        session.scalars(
            select(FindingClosure)
            .where(FindingClosure.finding_id == finding_id)
            .order_by(FindingClosure.id)
        )
    )


@dataclass
class RequestView:
    closure: FindingClosure
    finding: ReviewFinding | None
    review_id: int | None
    project: Project | None


def my_requests(
    session: Session, principal: Principal, status: str | None, limit: int
) -> list[RequestView]:
    """The owner's own close requests, newest first."""
    query = (
        select(FindingClosure, ReviewFinding, Review.id, Project)
        .outerjoin(ReviewFinding, ReviewFinding.id == FindingClosure.finding_id)
        .outerjoin(Review, Review.id == ReviewFinding.review_id)
        .outerjoin(MergeRequest, MergeRequest.id == Review.mr_id)
        .outerjoin(Commit, Commit.sha == Review.commit_sha)
        .outerjoin(Project, Project.id == func.coalesce(MergeRequest.project_id, Commit.project_id))
        .where(FindingClosure.requested_by == principal.user_id, FindingClosure.kind == "request")
    )
    wanted = [s.strip().lower() for s in (status or "").split(",") if s.strip()]
    if wrong := sorted(set(wanted) - set(CLOSURE_STATUSES)):
        raise McpError(
            "invalid_argument",
            f"status không hợp lệ: {', '.join(wrong)} (chọn trong {', '.join(CLOSURE_STATUSES)})",
        )
    if wanted:
        query = query.where(FindingClosure.status.in_(wanted))
    rows = session.execute(
        query.order_by(FindingClosure.id.desc()).limit(max(1, min(limit, MAX_LIMIT)))
    ).all()
    return [RequestView(closure=c, finding=f, review_id=r, project=p) for c, f, r, p in rows]
