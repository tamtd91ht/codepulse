"""Phản ánh & Đề xuất (docs/specs/32, user 2026-11-18).

`feedback.submit`: send a report, see and edit one's own (while it is still `new`).
`feedback.manage`: see every report on the tree, triage it (area, feature, priority, status,
note), re-run the analysis, delete, and export a batch as one Markdown file for a coding agent.
Every report shows its author.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.orm import Session, undefer

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.security import CurrentPrincipal, Principal, require
from app.avatars import avatar_url
from app.feedback import analysis
from app.feedback.areas import (
    AREAS,
    KIND_LABEL,
    NO_FEATURE,
    PRIORITY_LABEL,
    STATUS_LABEL,
    area_for_page,
    slug,
)
from app.feedback.export import (
    Author,
    area_of,
    build_export,
    item_markdown,
    kind_of,
    path_of,
    title_of,
)
from app.models import AuditLog, FeedbackExport, FeedbackItem, Team, TeamMember, User
from app.notifications.inbox import notify
from app.workers.queues import review_queue

router = APIRouter(tags=["feedback"])
SessionDep = Annotated[Session, Depends(get_session)]
QueueDep = Annotated[Queue, Depends(get_queue)]
Submitter = Annotated[Principal, require("feedback.submit")]
Manager = Annotated[Principal, require("feedback.manage")]

Kind = Literal["bug", "feature", "improvement", "question"]
AreaKey = Literal[
    "overview",
    "review",
    "tasks",
    "testcases",
    "docs",
    "dpi",
    "reports",
    "insights",
    "stetho",
    "admin",
    "integrations",
    "general",
]
Status = Literal["new", "accepted", "planned", "done", "rejected"]
Priority = Literal["p1", "p2", "p3"]
LIST_MAX = 2_000
EXPORT_MAX = 200
STATUS_MAX = 500
TRIAGE_NOTE_MAX = 4_000


# -- shapes ----------------------------------------------------------------------------------------


class Person(BaseModel):
    id: int
    name: str
    avatar_url: str | None = None
    teams: list[str] = []


class Option(BaseModel):
    key: str
    label: str


class MetaOut(BaseModel):
    areas: list[Option]
    kinds: list[Option]
    statuses: list[Option]
    priorities: list[Option]
    suggested_area: str
    can_manage: bool
    per_day: int
    title_max: int
    body_max: int


class ItemRow(BaseModel):
    id: int
    title: str  # the analysed title, else the author's
    kind: str
    area: str
    feature: str
    priority: str | None
    status: str
    analysis_status: str
    summary: str | None
    duplicates: list[int]
    author: Person | None
    created_at: str
    exported: int  # how many export batches it went into
    path: str
    analysing: bool  # queued or running and not given up as stale


class ItemOut(ItemRow):
    raw_title: str
    body: str
    page_url: str | None
    author_kind: str
    author_area: str
    analysis_md: str | None
    result: dict[str, Any] | None
    triage_note: str | None
    error: str | None
    can_edit: bool  # the author, while new
    can_manage: bool
    triaged_by: Person | None
    triaged_at: str | None
    exported_at: str | None


class TreeFeature(BaseModel):
    name: str
    count: int
    open: int


class TreeArea(BaseModel):
    key: str
    label: str
    count: int
    open: int
    features: list[TreeFeature]


class BoardOut(BaseModel):
    items: list[ItemRow]
    tree: list[TreeArea]
    truncated: bool


class SubmitIn(BaseModel):
    kind: Kind
    area: AreaKey | None = None
    title: str = Field(min_length=3, max_length=analysis.TITLE_MAX)
    body: str = Field(min_length=10, max_length=analysis.BODY_MAX)
    page_url: str | None = Field(default=None, max_length=500)


class EditIn(BaseModel):
    # The author's fields (while new): a change re-runs the analysis.
    kind: Kind | None = None
    area: AreaKey | None = None
    title: str | None = Field(default=None, min_length=3, max_length=analysis.TITLE_MAX)
    body: str | None = Field(default=None, min_length=10, max_length=analysis.BODY_MAX)
    # The manager's (triage).
    ai_kind: Kind | None = None
    ai_area: AreaKey | None = None
    feature: str | None = Field(default=None, max_length=60)
    priority: Priority | None = None
    status: Status | None = None
    triage_note: str | None = Field(default=None, max_length=TRIAGE_NOTE_MAX)
    ai_title: str | None = Field(default=None, min_length=3, max_length=analysis.TITLE_MAX)


class ExportIn(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=EXPORT_MAX)
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4_000)
    mark_planned: bool = True


class StatusIn(BaseModel):
    """Move several reports to one status (spec 32 §7.3): e.g. the batch handed to the coding
    agent is done. The note, when given, is added under each report's existing triage note."""

    item_ids: list[int] = Field(min_length=1, max_length=STATUS_MAX)
    status: Status
    note: str | None = Field(default=None, max_length=1_000)


class StatusOut(BaseModel):
    changed: list[int]  # moved to the status
    unchanged: list[int]  # already in it (a note, if any, was still added)


class ExportRow(BaseModel):
    id: int
    title: str
    note: str | None
    items: int
    item_ids: list[int]
    created_by: Person | None
    created_at: str


# -- helpers ---------------------------------------------------------------------------------------


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at else None


def _people(session: Session, ids: set[int]) -> dict[int, Person]:
    ids.discard(0)
    if not ids:
        return {}
    users = session.scalars(select(User).where(User.id.in_(ids))).all()
    today = datetime.now(UTC).date()
    teams: dict[int, list[str]] = {}
    for user_id, name in session.execute(
        select(TeamMember.user_id, Team.name)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            TeamMember.user_id.in_(ids),
            (TeamMember.left_at.is_(None)) | (TeamMember.left_at > today),
        )
        .order_by(Team.name)
    ):
        if name not in teams.setdefault(int(user_id), []):
            teams[int(user_id)].append(str(name))
    return {
        u.id: Person(
            id=u.id,
            name=u.full_name or u.primary_email or f"#{u.id}",
            avatar_url=avatar_url(u.id, u.avatar_file, u.updated_at),
            teams=teams.get(u.id, []),
        )
        for u in users
    }


def _row(item: FeedbackItem, people: dict[int, Person], manage: bool = True) -> ItemRow:
    return ItemRow(
        id=item.id,
        title=title_of(item),
        kind=kind_of(item),
        area=area_of(item),
        feature=item.feature or NO_FEATURE,
        priority=item.priority,
        status=item.status,
        analysis_status=item.analysis_status,
        summary=item.summary,
        duplicates=list((item.ai_result or {}).get("duplicates") or []) if manage else [],
        author=people.get(item.author_id or 0),
        created_at=item.created_at.isoformat(),
        exported=item.export_count,
        path=path_of(item),
        analysing=analysis.running(item),
    )


def _public(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """The analysis as its author sees it: without the ids of other people's reports."""
    if result is None:
        return None
    return {k: v for k, v in result.items() if k != "duplicates"}


def _detail(session: Session, item: FeedbackItem, principal: Principal) -> ItemOut:
    people = _people(session, {item.author_id or 0, item.triaged_by or 0})
    manage = principal.can("feedback.manage")
    mine = item.author_id == principal.user_id
    return ItemOut(
        **_row(item, people, manage).model_dump(),
        raw_title=item.title,
        body=item.body,
        page_url=item.page_url,
        author_kind=item.kind,
        author_area=item.area,
        analysis_md=item.analysis_md,
        result=item.ai_result if manage else _public(item.ai_result),
        triage_note=item.triage_note,
        error=item.error,
        can_edit=mine and item.status == "new",
        can_manage=manage,
        triaged_by=people.get(item.triaged_by or 0),
        triaged_at=_iso(item.triaged_at),
        exported_at=_iso(item.exported_at),
    )


def _get(session: Session, item_id: int, principal: Principal) -> FeedbackItem:
    """The report, when it is one's own or one manages feedback (else 404: not even its
    existence is told)."""
    item = session.get(FeedbackItem, item_id, options=[undefer(FeedbackItem.body)])
    if item is None:
        raise HTTPException(404, "không có phản ánh này")
    manage = principal.can("feedback.manage")
    if not manage and not principal.can("feedback.submit"):
        raise HTTPException(403, "thiếu quyền gửi phản ánh")
    if item.author_id != principal.user_id and not manage:
        raise HTTPException(404, "không có phản ánh này")
    return item


def _enqueue(session: Session, queue: Queue, item: FeedbackItem) -> None:
    try:
        review_queue(queue, asked=True).enqueue(
            "app.feedback.analysis.analyze_job",
            item.id,
            item.requested_at.isoformat(),
            job_id=f"feedback-{item.id}-{int(item.requested_at.timestamp() * 1000)}",
            job_timeout=analysis.JOB_TIMEOUT_S,
        )
    except Exception:
        item.analysis_status = "failed"
        item.error = "Không xếp hàng phân tích được; bấm Phân tích lại."
        item.finished_at = datetime.now(UTC)
        session.commit()


def _guard_analysis(session: Session, item: FeedbackItem, principal: Principal) -> None:
    """A run already going is not asked again; an author's re-runs count toward a daily cap
    (the LLM budget is shared with code review: docs/specs/32 §5)."""
    if analysis.running(item):
        raise HTTPException(409, "đang phân tích; đợi xong rồi thử lại")
    if principal.can("feedback.manage"):
        return
    since = datetime.now(UTC) - timedelta(days=1)
    runs = session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.actor_user_id == principal.user_id,
            AuditLog.action.in_(("feedback.submit", "feedback.reanalyze")),
            AuditLog.at >= since,
        )
    )
    if (runs or 0) >= analysis.RUNS_PER_DAY:
        raise HTTPException(
            429, f"tối đa {analysis.RUNS_PER_DAY} lần AI phân tích mỗi ngày; mai thử tiếp nhé"
        )


def _ask_analysis(session: Session, queue: Queue, item: FeedbackItem, principal: Principal) -> None:
    audit(session, principal.user_id, "feedback.reanalyze", f"feedback:{item.id}")
    item.analysis_status = "queued"
    item.requested_at = datetime.now(UTC)
    item.started_at = item.finished_at = None
    item.error = None
    session.commit()
    _enqueue(session, queue, item)


STATUS_TONE = {"done": "good", "rejected": "bad", "planned": "info", "accepted": "info"}


def _tell_authors(
    session: Session,
    moved: list[FeedbackItem],
    status: str,
    actor_id: int,
    note: str | None = None,
) -> None:
    """The bell of each author whose reports changed status (spec 32 §7.3, user 2026-11-19):
    one notification per author per change, naming the report (or how many, when a bulk move
    took several of theirs). The manager does not notify themselves (`notify` skips the actor)."""
    label = STATUS_LABEL.get(status, status)
    mine: dict[int, list[FeedbackItem]] = {}
    for item in moved:
        if item.author_id is not None:
            mine.setdefault(item.author_id, []).append(item)
    for author_id, items in mine.items():
        items.sort(key=lambda i: i.id)
        if len(items) == 1:
            title = f"Phản ánh #{items[0].id} chuyển sang “{label}”"
            lines = [title_of(items[0])]
        else:
            title = f"{len(items)} phản ánh của bạn chuyển sang “{label}”"
            lines = [f"#{i.id} {title_of(i)}" for i in items[:5]]
            if len(items) > 5:
                lines.append(f"… và {len(items) - 5} phản ánh khác")
        if note:
            lines.append(f"Ghi chú: {note}")
        notify(
            session,
            [author_id],
            kind="feedback.status",
            title=title,
            body="\n".join(lines),
            link=f"/feedback?item={items[0].id}",
            tone=STATUS_TONE.get(status, "info"),
            actor_id=actor_id,
            ref=f"feedback:{items[0].id}",
        )


def _filename(text: str) -> str:

    return slug(text, 60)


def _md_response(body: str, name: str) -> Response:
    return Response(
        body.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}.md"; '
            f"filename*=UTF-8''{quote(name)}.md"
        },
    )


# -- endpoints -------------------------------------------------------------------------------------


@router.get("/feedback/meta", response_model=MetaOut)
def feedback_meta(
    principal: CurrentPrincipal, page: Annotated[str | None, Query(max_length=500)] = None
) -> MetaOut:
    if not (principal.can("feedback.submit") or principal.can("feedback.manage")):
        raise HTTPException(403, "thiếu quyền gửi phản ánh")
    return MetaOut(
        areas=[Option(key=a.key, label=a.label) for a in AREAS],
        kinds=[Option(key=k, label=v) for k, v in KIND_LABEL.items()],
        statuses=[Option(key=k, label=v) for k, v in STATUS_LABEL.items()],
        priorities=[Option(key=k, label=v) for k, v in PRIORITY_LABEL.items()],
        suggested_area=area_for_page(page),
        can_manage=principal.can("feedback.manage"),
        per_day=analysis.PER_DAY,
        title_max=analysis.TITLE_MAX,
        body_max=analysis.BODY_MAX,
    )


@router.post("/feedback", response_model=ItemOut, status_code=201)
def submit_feedback(
    body: SubmitIn, principal: Submitter, session: SessionDep, queue: QueueDep
) -> ItemOut:
    since = datetime.now(UTC) - timedelta(days=1)
    sent = session.scalar(
        select(func.count())
        .select_from(FeedbackItem)
        .where(FeedbackItem.author_id == principal.user_id, FeedbackItem.created_at >= since)
    )
    if (sent or 0) >= analysis.PER_DAY:
        raise HTTPException(429, f"tối đa {analysis.PER_DAY} phản ánh mỗi ngày; mai gửi tiếp nhé")
    page = (body.page_url or "").strip() or None
    item = FeedbackItem(
        author_id=principal.user_id,
        kind=body.kind,
        area=body.area or area_for_page(page),
        title=" ".join(body.title.split()),
        body=body.body.strip(),
        page_url=page,
        requested_at=datetime.now(UTC),
    )
    session.add(item)
    session.flush()
    audit(session, principal.user_id, "feedback.submit", f"feedback:{item.id}", kind=item.kind)
    session.commit()
    _enqueue(session, queue, item)
    session.refresh(item)
    return _detail(session, item, principal)


@router.get("/feedback/mine", response_model=list[ItemRow])
def my_feedback(principal: Submitter, session: SessionDep) -> list[ItemRow]:
    items = session.scalars(
        select(FeedbackItem)
        .where(FeedbackItem.author_id == principal.user_id)
        .order_by(FeedbackItem.id.desc())
        .limit(500)
    ).all()
    people = _people(session, {principal.user_id})
    manage = principal.can("feedback.manage")
    return [_row(i, people, manage) for i in items]


@router.get("/feedback", response_model=BoardOut)
def feedback_board(principal: Manager, session: SessionDep) -> BoardOut:
    items = session.scalars(
        select(FeedbackItem).order_by(FeedbackItem.id.desc()).limit(LIST_MAX + 1)
    ).all()
    truncated = len(items) > LIST_MAX
    items = items[:LIST_MAX]
    people = _people(session, {i.author_id or 0 for i in items})
    rows = [_row(i, people) for i in items]
    tree: list[TreeArea] = []
    for area in AREAS:
        mine = [r for r in rows if r.area == area.key]
        if not mine:
            continue
        features: dict[str, list[ItemRow]] = {}
        for r in mine:
            features.setdefault(r.feature, []).append(r)
        tree.append(
            TreeArea(
                key=area.key,
                label=area.label,
                count=len(mine),
                open=sum(r.status not in ("done", "rejected") for r in mine),
                features=[
                    TreeFeature(
                        name=name,
                        count=len(rs),
                        open=sum(r.status not in ("done", "rejected") for r in rs),
                    )
                    for name, rs in sorted(features.items(), key=lambda kv: (-len(kv[1]), kv[0]))
                ],
            )
        )
    return BoardOut(items=rows, tree=tree, truncated=truncated)


@router.get("/feedback/exports", response_model=list[ExportRow])
def list_exports(principal: Manager, session: SessionDep) -> list[ExportRow]:
    rows = session.scalars(
        select(FeedbackExport).order_by(FeedbackExport.id.desc()).limit(100)
    ).all()
    people = _people(session, {r.created_by or 0 for r in rows})
    return [
        ExportRow(
            id=r.id,
            title=r.title,
            note=r.note,
            items=len(r.item_ids),
            item_ids=list(r.item_ids),
            created_by=people.get(r.created_by or 0),
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.post("/feedback/exports", response_model=ExportRow, status_code=201)
def create_export(body: ExportIn, principal: Manager, session: SessionDep) -> ExportRow:
    ids = list(dict.fromkeys(body.item_ids))
    items = session.scalars(
        select(FeedbackItem)
        .where(FeedbackItem.id.in_(ids))
        .options(undefer(FeedbackItem.body), undefer(FeedbackItem.analysis_md))
    ).all()
    if len(items) != len(ids):
        raise HTTPException(404, "có phản ánh không còn; tải lại danh sách")
    people = _people(session, {i.author_id or 0 for i in items} | {principal.user_id})
    authors = {pid: Author(p.name, tuple(p.teams)) for pid, p in people.items()}
    me = people.get(principal.user_id)
    now = datetime.now(UTC)
    title = " ".join(body.title.split())
    text = build_export(
        list(items),
        authors,
        title=title,
        note=body.note,
        by=me.name if me else f"#{principal.user_id}",
        at=now,
    )
    row = FeedbackExport(
        title=title,
        note=(body.note or "").strip() or None,
        item_ids=ids,
        body=text,
        created_by=principal.user_id,
    )
    session.add(row)
    planned: list[FeedbackItem] = []
    for item in items:
        item.exported_at = now
        item.export_count = FeedbackItem.export_count + 1
        if body.mark_planned and item.status in ("new", "accepted"):
            item.status = "planned"
            item.triaged_by, item.triaged_at = principal.user_id, now
            planned.append(item)
    session.flush()
    _tell_authors(session, planned, "planned", principal.user_id)
    audit(session, principal.user_id, "feedback.export", f"feedback_export:{row.id}", items=ids)
    session.commit()
    return ExportRow(
        id=row.id,
        title=row.title,
        note=row.note,
        items=len(ids),
        item_ids=ids,
        created_by=me,
        created_at=row.created_at.isoformat(),
    )


@router.post("/feedback/status", response_model=StatusOut)
def set_status(body: StatusIn, principal: Manager, session: SessionDep) -> StatusOut:
    """One or many reports to one status at once (all or nothing: an unknown id refuses the
    whole call, so a stale list never half-applies)."""
    ids = list(dict.fromkeys(body.item_ids))
    # Rows locked in id order: an author's edit (which checks `new`) or another triage of the
    # same report waits instead of racing this move or losing the appended note.
    items = session.scalars(
        select(FeedbackItem)
        .where(FeedbackItem.id.in_(ids))
        .order_by(FeedbackItem.id)
        .with_for_update()
    ).all()
    if len(items) != len(ids):
        raise HTTPException(404, "có phản ánh không còn; tải lại danh sách")
    note = (body.note or "").strip()
    if note:
        full = [i.id for i in items if len(i.triage_note or "") + len(note) + 1 > TRIAGE_NOTE_MAX]
        if full:
            listed = ", ".join(f"#{i}" for i in full[:10])
            raise HTTPException(422, f"ghi chú của {listed} sẽ quá {TRIAGE_NOTE_MAX} ký tự")
    now = datetime.now(UTC)
    changed: list[int] = []
    unchanged: list[int] = []
    for item in items:
        if item.status == body.status and not note:
            unchanged.append(item.id)
            continue
        before = item.status
        (changed if before != body.status else unchanged).append(item.id)
        item.status = body.status
        if note:
            item.triage_note = f"{item.triage_note}\n{note}" if item.triage_note else note
        item.triaged_by, item.triaged_at = principal.user_id, now
        # One row per report, on the target its other triage rows use: its history shows it.
        audit(
            session,
            principal.user_id,
            "feedback.status",
            f"feedback:{item.id}",
            status=body.status,
            before=before,
            batch=len(ids),
            note=note[:200] or None,
        )
    moved = [i for i in items if i.id in set(changed)]
    _tell_authors(session, moved, body.status, principal.user_id, note or None)
    session.commit()
    return StatusOut(changed=changed, unchanged=unchanged)


@router.get("/feedback/exports/{export_id}/download")
def download_export(export_id: int, principal: Manager, session: SessionDep) -> Response:
    row = session.get(FeedbackExport, export_id, options=[undefer(FeedbackExport.body)])
    if row is None:
        raise HTTPException(404, "không có gói export này")
    stamp = row.created_at.strftime("%Y%m%d")
    return _md_response(row.body, f"codepulse-feedback-{stamp}-{_filename(row.title)}")


@router.get("/feedback/{item_id}", response_model=ItemOut)
def get_feedback(item_id: int, principal: CurrentPrincipal, session: SessionDep) -> ItemOut:
    item = _get(session, item_id, principal)
    return _detail(session, item, principal)


@router.get("/feedback/{item_id}/markdown")
def feedback_markdown(item_id: int, principal: CurrentPrincipal, session: SessionDep) -> Response:
    item = _get(session, item_id, principal)
    people = _people(session, {item.author_id or 0})
    who = people.get(item.author_id or 0)
    author = Author(who.name, tuple(who.teams)) if who else Author("(không rõ)")
    name = re.sub(r"[/]", "-", path_of(item).rsplit("/", 1)[-1].removesuffix(".md"))
    return _md_response(item_markdown(item, author), name)


@router.patch("/feedback/{item_id}", response_model=ItemOut)
def edit_feedback(
    item_id: int,
    body: EditIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    queue: QueueDep,
) -> ItemOut:
    item = _get(session, item_id, principal)
    session.refresh(item, with_for_update=True)
    given = body.model_dump(exclude_unset=True)
    authored: dict[str, Any] = {
        k: given[k] for k in ("kind", "area", "title", "body") if k in given
    }
    triage = {k: v for k, v in given.items() if k not in authored}
    if authored:
        if item.author_id != principal.user_id:
            raise HTTPException(403, "chỉ tác giả sửa được nội dung phản ánh")
        if item.status != "new":
            raise HTTPException(409, "phản ánh đã được xử lý; không sửa được nữa")
        _guard_analysis(session, item, principal)
        for key, value in authored.items():
            if value is None:
                continue
            if key == "title":
                value = " ".join(str(value).split())
            elif key == "body":
                value = str(value).strip()
            setattr(item, key, value)
    if triage:
        if not principal.can("feedback.manage"):
            raise HTTPException(403, "thiếu quyền phân loại phản ánh")
        was = item.status
        for name, value in triage.items():
            if name == "feature":
                value = " ".join(str(value or "").split()) or None
            elif name == "triage_note":
                value = (value or "").strip() or None
            elif value is None:
                continue
            setattr(item, name, value)
        item.triaged_by, item.triaged_at = principal.user_id, datetime.now(UTC)
        audit(
            session,
            principal.user_id,
            "feedback.triage",
            f"feedback:{item.id}",
            **{k: str(v)[:200] for k, v in triage.items()},
        )
        if item.status != was:
            _tell_authors(session, [item], item.status, principal.user_id, item.triage_note)
    session.commit()
    if authored:
        _ask_analysis(session, queue, item, principal)
    session.refresh(item)
    return _detail(session, item, principal)


@router.post("/feedback/{item_id}/analyze", response_model=ItemOut)
def reanalyze_feedback(
    item_id: int, principal: CurrentPrincipal, session: SessionDep, queue: QueueDep
) -> ItemOut:
    item = _get(session, item_id, principal)
    if not principal.can("feedback.manage") and not (
        item.author_id == principal.user_id and item.status == "new"
    ):
        raise HTTPException(403, "không phân tích lại được phản ánh này")
    _guard_analysis(session, item, principal)
    _ask_analysis(session, queue, item, principal)
    session.refresh(item)
    return _detail(session, item, principal)


@router.delete("/feedback/{item_id}", status_code=204)
def delete_feedback(item_id: int, principal: CurrentPrincipal, session: SessionDep) -> Response:
    item = _get(session, item_id, principal)
    manage = principal.can("feedback.manage")
    if not manage and item.status != "new":
        raise HTTPException(409, "phản ánh đã được xử lý; không xoá được nữa")
    audit(session, principal.user_id, "feedback.delete", f"feedback:{item.id}", title=item.title)
    session.delete(item)
    session.commit()
    return Response(status_code=204)
