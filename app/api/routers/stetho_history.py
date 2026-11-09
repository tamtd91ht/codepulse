"""Lịch sử Stetho (docs/specs/28 §9, user 2026-11-09): conversations with Stetho, a report by
member and by tag. ONLY admins (ADMIN_EMAILS): no permission opens it, not even the admin.*
rights of a permission set. Opening one conversation is audited.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.security import CurrentPrincipal, Principal
from app.assistant import history
from app.models import AssistantConversation, AssistantTurn, User

router = APIRouter(tags=["assistant"])
SessionDep = Annotated[Session, Depends(get_session)]


def admin_only(principal: CurrentPrincipal) -> Principal:
    if not principal.is_admin:
        raise HTTPException(403, "Lịch sử Stetho chỉ dành cho admin")
    return principal


Admin = Annotated[Principal, Depends(admin_only)]
Day = Annotated[date | None, Query()]
TagKey = Literal[
    "codepulse_howto",
    "jira_commit",
    "quality_analysis",
    "findings_fix",
    "people_team",
    "dpi",
    "admin_config",
    "tech_general",
    "other",
]


def _where(
    start: date | None,
    end: date | None,
    user_id: int | None,
    tag: str | None,
    q: str | None = None,
) -> list[ColumnElement[bool]]:
    """Conversations active in the period (their last turn), of one person, one tag, matching
    a text in any of their questions."""
    first = start or (datetime.now(UTC).date() - timedelta(days=history.HISTORY_KEEP_DAYS))
    where: list[ColumnElement[bool]] = [
        AssistantConversation.last_at >= datetime.combine(first, time.min, UTC)
    ]
    if end:
        where.append(
            AssistantConversation.last_at < datetime.combine(end, time.min, UTC) + timedelta(days=1)
        )
    if user_id is not None:
        where.append(AssistantConversation.user_id == user_id)
    if tag == "untagged":
        where.append(AssistantConversation.tag.is_(None))
    elif tag:
        where.append(AssistantConversation.tag == tag)
    if q and q.strip():
        like = "%" + q.strip().replace("%", r"\%").replace("_", r"\_") + "%"
        where.append(
            or_(
                AssistantConversation.title.ilike(like),
                AssistantConversation.id.in_(
                    select(AssistantTurn.conversation_id).where(AssistantTurn.question.ilike(like))
                ),
            )
        )
    return where


def _names(session: Session, ids: set[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    return {
        u.id: {
            "id": u.id,
            "name": u.full_name or u.primary_email or f"#{u.id}",
            "email": u.primary_email,
        }
        for u in session.scalars(select(User).where(User.id.in_(ids)))
    }


TAGS_OUT = [{"key": t.key, "label": t.label} for t in history.TAGS]


@router.get("/stetho/history/summary")
def summary(
    principal: Admin,
    session: SessionDep,
    start: Day = None,
    end: Day = None,
    user_id: int | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
) -> dict[str, Any]:
    """KPIs, the report by member (with each one's top tags), by tag, and by day."""
    where = _where(start, end, user_id, tag)
    c = AssistantConversation
    kpi = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(c.turns), 0),
            func.count(func.distinct(c.user_id)),
            func.count().filter(c.tag.is_(None)),
            func.coalesce(func.sum(c.cost_usd), 0),
        ).where(*where)
    ).one()
    members = session.execute(
        select(
            c.user_id,
            func.count(),
            func.coalesce(func.sum(c.turns), 0),
            func.coalesce(func.sum(c.cost_usd), 0),
            func.max(c.last_at),
        )
        .where(*where)
        .group_by(c.user_id)
        .order_by(func.count().desc())
    ).all()
    member_tags: dict[int, list[dict[str, Any]]] = {}
    for uid, t, n in session.execute(
        select(c.user_id, c.tag, func.count())
        .where(*where, c.tag.is_not(None))
        .group_by(c.user_id, c.tag)
        .order_by(func.count().desc())
    ):
        member_tags.setdefault(int(uid), []).append({"tag": t, "n": int(n)})
    names = _names(session, {int(m[0]) for m in members})
    by_tag = session.execute(
        select(c.tag, func.count(), func.count(func.distinct(c.user_id)))
        .where(*where)
        .group_by(c.tag)
        .order_by(func.count().desc())
    ).all()
    day = func.date_trunc("day", c.last_at)
    by_day = session.execute(
        select(cast(day, String), func.count()).where(*where).group_by(day).order_by(day)
    ).all()
    return {
        "keep_days": history.HISTORY_KEEP_DAYS,
        "tags": TAGS_OUT,
        "kpis": {
            "conversations": int(kpi[0]),
            "turns": int(kpi[1]),
            "people": int(kpi[2]),
            "untagged": int(kpi[3]),
            "cost_usd": float(kpi[4]),
        },
        "by_member": [
            {
                "user": names.get(int(uid), {"id": int(uid), "name": f"#{uid}", "email": None}),
                "conversations": int(n),
                "turns": int(turns),
                "cost_usd": float(cost),
                "last_at": last,
                "tags": member_tags.get(int(uid), [])[:3],
            }
            for uid, n, turns, cost, last in members
        ],
        "by_tag": [
            {
                "tag": t,
                "label": history.TAG_LABEL.get(t or "", "Chưa gắn tag"),
                "n": int(n),
                "people": int(p),
            }
            for t, n, p in by_tag
        ],
        "by_day": [{"day": str(d)[:10], "n": int(n)} for d, n in by_day],
    }


@router.get("/stetho/history/conversations")
def conversations(
    principal: Admin,
    session: SessionDep,
    start: Day = None,
    end: Day = None,
    user_id: int | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    where = _where(start, end, user_id, tag, q)
    c = AssistantConversation
    total = int(session.scalar(select(func.count()).select_from(c).where(*where)) or 0)
    rows = list(
        session.scalars(
            select(c)
            .where(*where)
            .order_by(c.last_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    names = _names(session, {r.user_id for r in rows})
    return {
        "items": [
            {
                "id": r.id,
                "user": names.get(r.user_id),
                "title": r.title,
                "turns": r.turns,
                "tag": r.tag,
                "tag_source": r.tag_source,
                "started_at": r.started_at,
                "last_at": r.last_at,
                "cost_usd": float(r.cost_usd or 0),
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "tags": TAGS_OUT,
    }


def _conversation(session: Session, conversation_id: str) -> AssistantConversation:
    row = session.get(AssistantConversation, conversation_id)
    if row is None:
        raise HTTPException(404, "conversation not found")
    return row


@router.get("/stetho/history/conversations/{conversation_id}")
def conversation(conversation_id: str, principal: Admin, session: SessionDep) -> dict[str, Any]:
    """One conversation: every question and the gist of each answer. Audited."""
    row = _conversation(session, conversation_id)
    turns = list(
        session.scalars(
            select(AssistantTurn)
            .where(AssistantTurn.conversation_id == row.id)
            .order_by(AssistantTurn.asked_at, AssistantTurn.id)
        )
    )
    audit(
        session,
        principal.user_id,
        "stetho.history.read",
        f"stetho_conversation:{row.id}",
        owner=row.user_id,
    )
    session.commit()
    return {
        "id": row.id,
        "user": _names(session, {row.user_id}).get(row.user_id),
        "title": row.title,
        "tag": row.tag,
        "tag_source": row.tag_source,
        "started_at": row.started_at,
        "last_at": row.last_at,
        "cost_usd": float(row.cost_usd or 0),
        "turns": [
            {
                "asked_at": t.asked_at,
                "question": t.question,
                "answer_points": (t.answer_points or "").split("\n") if t.answer_points else [],
                "answer_chars": t.answer_chars,
                "status": t.status,
                "screen": t.screen,
                "model": t.model,
                "cost_usd": float(t.cost_usd) if t.cost_usd is not None else None,
                "duration_ms": t.duration_ms,
                "docs": t.docs,
                "web": t.web,
            }
            for t in turns
        ],
        "tags": TAGS_OUT,
        "keep_until": row.last_at + timedelta(days=history.HISTORY_KEEP_DAYS),
    }


class TagIn(BaseModel):
    tag: TagKey


@router.patch("/stetho/history/conversations/{conversation_id}")
def set_tag(
    conversation_id: str, body: TagIn, principal: Admin, session: SessionDep
) -> dict[str, Any]:
    """An admin's tag: kept even when the conversation goes on."""
    row = _conversation(session, conversation_id)
    row.tag, row.tag_source, row.tagged_at = body.tag, "manual", datetime.now(UTC)
    audit(
        session,
        principal.user_id,
        "stetho.history.tag",
        f"stetho_conversation:{row.id}",
        tag=body.tag,
    )
    session.commit()
    return {"id": row.id, "tag": row.tag, "tag_source": row.tag_source}


@router.delete("/stetho/history/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, principal: Admin, session: SessionDep) -> None:
    row = _conversation(session, conversation_id)
    audit(
        session,
        principal.user_id,
        "stetho.history.delete",
        f"stetho_conversation:{row.id}",
        owner=row.user_id,
    )
    session.delete(row)
    session.commit()


@router.post("/stetho/history/tag-now", status_code=202)
def tag_now(principal: Admin, queue: Annotated[Any, Depends(get_queue)]) -> dict[str, str]:
    """Queue the tagging batch now instead of waiting for the next 10-minute tick."""
    queue.enqueue("app.assistant.history.tag_job", job_id="stetho-tag-now", job_timeout=300)
    return {"status": "queued"}
