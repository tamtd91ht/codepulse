"""Evidence packs API (docs/specs/20 §6, §9): one person's month for the 1:1.

Who (decision D4): the person, the lead of a team they are a member of now (not a co-lead of
that team), and admins; nobody else, not a dự án manager, "Quản lý tổng" or HR. What each may
do in each state: §6.2b. Coaching only (D1): nothing here is a score. Every look at someone
else's pack and every change is audited. Writes lock the pack row, so two people acting at once
cannot both move it.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, get_args
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_secret_box, get_session
from app.api.personal import covers_person
from app.api.security import Principal, require
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.evidence import draft, snapshot
from app.evidence import jobs as draft_jobs
from app.insights import INSIGHTS_VERSION
from app.models import EvidenceItem, EvidencePack, TeamMember, User
from app.models.evidence import EVIDENCE_KINDS

router = APIRouter(tags=["evidence"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# docs/specs/21: seeing a pack (every endpoint here; what one may change in it then follows
# `Role` and the state machine), and the lead's AI draft.
EvidenceViewer = Annotated[Principal, require("evidence.view")]
EvidenceLead = Annotated[Principal, require("evidence.lead")]
MAX_MONTHS_BACK = 24
NOTE_MAX = 10_000
PERIOD = re.compile(r"\d{4}-\d{2}")
Kind = Literal[
    "mr", "commit", "review_given", "finding_fixed", "incident", "design", "mentoring", "other"
]
if set(EVIDENCE_KINDS) != set(get_args(Kind)):  # the API and the table must agree
    raise RuntimeError("evidence kinds differ between the API and app/models/evidence.py")


def today(settings: Settings) -> date:
    """Today in REPORT_TZ (a function, so tests can fix the date)."""
    return datetime.now(UTC).astimezone(ZoneInfo(settings.report_tz)).date()


@dataclass(frozen=True)
class Role:
    """How the viewer relates to the pack's person (docs/specs/21: `evidence.view` to see it,
    `evidence.edit_self` for the person's part, `evidence.lead` for the lead's part). The state
    machine of §6.2b (`_can`) stays as it is whatever the sets say."""

    self_: bool  # the person, writing their own part
    lead: bool  # their lead now, or an admin; never yourself
    me: bool = False  # the viewer is the pack's person (not audited, shown as "self")


def _leads(session: Session, principal: Principal, user_id: int, day: date) -> bool:
    """`principal` leads a team `user_id` is a plain member of today (a co-lead of the same team
    is a peer, not their lead)."""
    if not principal.lead_teams or user_id not in principal.lead_members:
        return False
    rows = session.scalars(
        select(TeamMember).where(
            TeamMember.user_id == user_id, TeamMember.team_id.in_(principal.lead_teams)
        )
    )
    return any(
        m.joined_at <= day
        and (m.left_at is None or m.left_at > day)
        and (m.role or "").lower() != "lead"
        for m in rows
    )


def _person(
    session: Session, principal: Principal, user_id: int, settings: Settings
) -> tuple[User, Role]:
    me = principal.user_id == user_id
    # The "team" scope here is the direct lead only (docs/specs/20 D4), never a co-lead or the
    # manager of the team (`_leads` returns at once for someone who leads nobody).
    direct = not me and _leads(session, principal, user_id, today(settings))
    if not covers_person(session, principal, "evidence.view", user_id, leads=direct):
        raise HTTPException(403, "only the person, their lead or an admin sees evidence packs")
    role = Role(
        self_=me and principal.can("evidence.edit_self"),
        lead=not me and covers_person(session, principal, "evidence.lead", user_id, leads=direct),
        me=me,
    )
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "person not found")
    return user, role


def months_back(settings: Settings) -> int:
    """No pack older than its notes are kept (EVIDENCE_RETENTION_MONTHS)."""
    return min(MAX_MONTHS_BACK, settings.evidence_retention_months)


def _period(value: str, settings: Settings) -> date:
    if not PERIOD.fullmatch(value):
        raise HTTPException(422, "period must be YYYY-MM")
    try:
        start = date(int(value[:4]), int(value[5:]), 1)
    except ValueError:
        raise HTTPException(422, "period must be YYYY-MM") from None
    now = today(settings)
    if start > now.replace(day=1):
        raise HTTPException(422, "that month has not started")
    back = months_back(settings)
    if (now.year - start.year) * 12 + now.month - start.month > back:
        raise HTTPException(422, f"at most {back} months back")
    return start


def _stored(
    session: Session, user_id: int, start: date, *, lock: bool = False
) -> EvidencePack | None:
    query = select(EvidencePack).where(
        EvidencePack.user_id == user_id, EvidencePack.period_start == start
    )
    if lock:
        query = query.with_for_update()
    return session.scalars(query).first()


def _build(session: Session, user_id: int, start: date, settings: Settings) -> dict[str, Any]:
    return snapshot.build(
        session,
        user_id,
        start,
        ZoneInfo(settings.report_tz),
        settings.insights_stale_sync_hours,
        knowledge=snapshot.knowledge_config(settings),
    )


def _create(session: Session, user_id: int, start: date, settings: Settings) -> EvidencePack:
    """The person's draft for the month, locked; a concurrent create wins and its row (locked)
    is returned instead."""
    pack = EvidencePack(
        user_id=user_id,
        period_start=start,
        status="draft",
        snapshot=_build(session, user_id, start, settings),
        insights_version=INSIGHTS_VERSION,
    )
    session.add(pack)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        existing = _stored(session, user_id, start, lock=True)
        if existing is None:
            raise
        return existing
    return pack


def _drafting(pack: EvidencePack) -> bool:
    """A draft job holds the pack (a job dead for longer than its timeout frees it)."""
    started = pack.ai_draft_started_at
    return started is not None and datetime.now(UTC) - started < draft_jobs.DRAFT_STALE


def _can(role: Role, status: str | None) -> dict[str, bool]:
    """§6.2b; status None = no stored pack yet (a draft in all but name)."""
    st = status or "draft"
    return {
        "edit_self_notes": role.self_ and st == "draft",
        # After the 1:1 the person may still answer: appended, never replacing (§6.3.5).
        "reply": role.self_ and st == "shared",
        "edit_lead_notes": role.lead and st in ("submitted", "reviewed"),
        "edit_goals": (role.self_ or role.lead) and st == "reviewed",
        "add_item": (role.self_ and st == "draft")
        or (role.lead and st in ("submitted", "reviewed")),
        "refresh": role.self_ and st == "draft",
        "ai_draft": role.lead and st in ("submitted", "reviewed"),
        "submit": role.self_ and st == "draft",
        "return": role.lead and st == "submitted",
        "review": role.lead and st == "submitted",
        "share": role.lead and st == "reviewed",
    }


def _names(session: Session, ids: set[int | None]) -> dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {
        u.id: u.full_name or u.primary_email or f"#{u.id}"
        for u in session.scalars(select(User).where(User.id.in_(wanted)))
    }


def _out(
    session: Session,
    viewer: int,
    user: User,
    role: Role,
    start: date,
    pack: EvidencePack | None,
    live: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`live`: a snapshot built now, shown instead of the stored one (a draft, or no pack)."""
    items = (
        list(
            session.scalars(
                select(EvidenceItem)
                .where(EvidenceItem.pack_id == pack.id)
                .order_by(EvidenceItem.id)
            )
        )
        if pack is not None
        else []
    )
    names = _names(session, {pack.reviewed_by if pack else None, *(i.added_by for i in items)})
    return {
        "user": {"id": user.id, "full_name": user.full_name, "primary_email": user.primary_email},
        "period": start.strftime("%Y-%m"),
        "persisted": pack is not None,
        "status": pack.status if pack else "draft",
        "snapshot": live if live is not None else (pack.snapshot if pack else None),
        "self_notes": pack.self_notes if pack else None,
        "lead_notes": pack.lead_notes if pack else None,
        "goals_next": pack.goals_next if pack else None,
        # §6.4: the AI draft is the lead's working material only.
        "ai_draft": pack.ai_draft if pack and role.lead else None,
        "ai_draft_running": bool(pack and role.lead and _drafting(pack)),
        "submitted_at": pack.submitted_at.isoformat() if pack and pack.submitted_at else None,
        "reviewed_at": pack.reviewed_at.isoformat() if pack and pack.reviewed_at else None,
        "reviewed_by_name": names.get(pack.reviewed_by) if pack and pack.reviewed_by else None,
        "shared_at": pack.shared_at.isoformat() if pack and pack.shared_at else None,
        "items": [
            {
                "id": i.id,
                "kind": i.kind,
                "ref": i.ref,
                "title": i.title,
                "note": i.note,
                "added_by": i.added_by,
                "added_by_name": names.get(i.added_by) if i.added_by else None,
                "mine": i.added_by == viewer,
            }
            for i in items
        ],
        "role": "self" if role.me else "lead",
        "can": _can(role, pack.status if pack else None),
        "insights_version": pack.insights_version if pack else INSIGHTS_VERSION,
    }


# -- endpoints -------------------------------------------------------------------------------


@router.get("/evidence/{user_id}")
def list_packs(
    user_id: int, principal: EvidenceViewer, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """The months this person has a pack for, newest first."""
    user, role = _person(session, principal, user_id, settings)
    packs = list(
        session.scalars(
            select(EvidencePack)
            .where(EvidencePack.user_id == user.id)
            .order_by(EvidencePack.period_start.desc())
        )
    )
    if not role.me:
        audit(session, principal.user_id, "evidence.list", f"user:{user.id}")
        session.commit()
    return {
        "user": {"id": user.id, "full_name": user.full_name},
        "months_back": months_back(settings),
        "packs": [
            {
                "period": p.period_start.strftime("%Y-%m"),
                "status": p.status,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in packs
        ],
    }


@router.get("/evidence/{user_id}/{period}")
def get_pack(
    user_id: int,
    period: str,
    principal: EvidenceViewer,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """A draft (or a month without a pack) shows what the system gathers now; once submitted,
    the snapshot frozen at that moment."""
    user, role = _person(session, principal, user_id, settings)
    start = _period(period, settings)
    pack = _stored(session, user.id, start)
    live = (
        _build(session, user.id, start, settings)
        if pack is None or pack.status == "draft"
        else None
    )
    if not role.me:
        audit(session, principal.user_id, "evidence.view", f"user:{user.id}", period=str(start))
    session.commit()
    return _out(session, principal.user_id, user, role, start, pack, live)


class PackPatch(BaseModel):
    self_notes: str | None = Field(default=None, max_length=NOTE_MAX)
    lead_notes: str | None = Field(default=None, max_length=NOTE_MAX)
    goals_next: str | None = Field(default=None, max_length=NOTE_MAX)
    # After the 1:1 (shared): the person's answer, appended to self_notes with its date.
    reply: str | None = Field(default=None, min_length=1, max_length=4000)


@router.patch("/evidence/{user_id}/{period}")
def update_pack(
    user_id: int,
    period: str,
    body: PackPatch,
    principal: EvidenceViewer,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    user, role = _person(session, principal, user_id, settings)
    start = _period(period, settings)
    pack = _stored(session, user.id, start, lock=True)
    changes = body.model_dump(exclude_unset=True)
    can = _can(role, pack.status if pack else None)
    allowed = {
        "self_notes": can["edit_self_notes"],
        "lead_notes": can["edit_lead_notes"],
        "goals_next": can["edit_goals"],
        "reply": can["reply"],
    }
    refused = [k for k in changes if not allowed[k]]
    if refused:
        raise HTTPException(409, f"cannot edit {', '.join(refused)} now")
    if not changes:
        return _out(session, principal.user_id, user, role, start, pack)
    if pack is None:
        pack = _create(session, user.id, start, settings)
        if pack.status != "draft":  # created by someone else meanwhile and moved on
            raise HTTPException(409, f"the pack is {pack.status}")
    previous: dict[str, str | None] = {}
    for key, value in changes.items():
        if key == "reply":
            stamp = today(settings).strftime("%d/%m/%Y")
            text = f"Phản hồi sau 1:1 ({stamp}): {(value or '').strip()}"
            previous["self_notes"] = pack.self_notes
            pack.self_notes = f"{pack.self_notes}\n\n{text}" if pack.self_notes else text
            continue
        previous[key] = getattr(pack, key)
        setattr(pack, key, (value or "").strip() or None)
    audit(
        session,
        principal.user_id,
        "evidence.update",
        f"user:{user.id}",
        period=str(start),
        fields=sorted(changes),
        # Earlier text stays recoverable after the pack leaves the draft (docs/specs/20 §6.3).
        previous=previous if pack.status != "draft" else None,
    )
    session.commit()
    live = _build(session, user.id, start, settings) if pack.status == "draft" else None
    return _out(session, principal.user_id, user, role, start, pack, live)


class ItemIn(BaseModel):
    kind: Kind
    title: str = Field(min_length=1, max_length=300)
    ref: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=2000)


@router.post("/evidence/{user_id}/{period}/items", status_code=201)
def add_item(
    user_id: int,
    period: str,
    body: ItemIn,
    principal: EvidenceViewer,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    user, role = _person(session, principal, user_id, settings)
    start = _period(period, settings)
    pack = _stored(session, user.id, start, lock=True)
    if not _can(role, pack.status if pack else None)["add_item"]:
        raise HTTPException(409, "cannot add evidence now")
    if pack is None:
        pack = _create(session, user.id, start, settings)
        if not _can(role, pack.status)["add_item"]:
            raise HTTPException(409, "cannot add evidence now")
    item = EvidenceItem(
        pack_id=pack.id,
        kind=body.kind,
        title=body.title.strip(),
        ref=(body.ref or "").strip() or None,
        note=(body.note or "").strip() or None,
        added_by=principal.user_id,
    )
    session.add(item)
    session.flush()
    audit(
        session,
        principal.user_id,
        "evidence.item_add",
        f"user:{user.id}",
        period=str(start),
        item_id=item.id,
        kind=item.kind,
    )
    session.commit()
    live = _build(session, user.id, start, settings) if pack.status == "draft" else None
    return _out(session, principal.user_id, user, role, start, pack, live)


@router.delete("/evidence/{user_id}/{period}/items/{item_id}")
def delete_item(
    user_id: int,
    period: str,
    item_id: int,
    principal: EvidenceViewer,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    user, role = _person(session, principal, user_id, settings)
    start = _period(period, settings)
    pack = _stored(session, user.id, start, lock=True)
    item = session.get(EvidenceItem, item_id)
    if pack is None or item is None or item.pack_id != pack.id:
        raise HTTPException(404, "evidence item not found")
    if item.added_by != principal.user_id or not _can(role, pack.status)["add_item"]:
        raise HTTPException(409, "only who added it, while the pack is open to them")
    audit(
        session,
        principal.user_id,
        "evidence.item_delete",
        f"user:{user.id}",
        period=str(start),
        item_id=item.id,
        kind=item.kind,
        title=item.title,
    )
    session.delete(item)
    session.commit()
    live = _build(session, user.id, start, settings) if pack.status == "draft" else None
    return _out(session, principal.user_id, user, role, start, pack, live)


@router.post("/evidence/{user_id}/{period}/ai-draft", status_code=202)
def ai_draft(
    user_id: int,
    period: str,
    principal: EvidenceLead,
    session: SessionDep,
    settings: SettingsDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> dict[str, Any]:
    """§6.4 (9.11): queue a draft of the lead's note from the frozen snapshot (never code;
    every bullet cites the snapshot). Lead or admin only, once the pack is submitted; one at a
    time per pack; within the month's LLM budget; never when a repo the pack draws on refuses
    external LLMs. The pack's `ai_draft_running` turns false when the draft is in."""
    from app.admin_settings import monthly_budget_usd
    from app.workers.knowledge_jobs import month_spend_usd  # lazy: the worker module is heavy

    user, role = _person(session, principal, user_id, settings)
    if not role.lead:
        raise HTTPException(403, "only the lead or an admin drafts the lead's note")
    start = _period(period, settings)
    pack = _stored(session, user.id, start, lock=True)
    if pack is None or not _can(role, pack.status)["ai_draft"]:
        raise HTTPException(409, "the lead drafts once the pack is submitted")
    if _drafting(pack):
        raise HTTPException(409, "a draft is being written for this pack")
    if settings.llm_provider != "claude_code":
        raise HTTPException(503, "the AI draft needs LLM_PROVIDER=claude_code")
    if not draft_jobs.llm_token(session, box, settings):
        raise HTTPException(503, "CodePulse is not connected to Claude (Admin > LLM & Chi phí)")
    budget = monthly_budget_usd(session, settings)
    if budget > 0 and month_spend_usd(session) >= budget:
        raise HTTPException(409, "the month's LLM budget is used up")
    if draft_jobs.refused_repos(session, pack.snapshot or {}):
        raise HTTPException(409, "a repo of this month does not allow sending data to the LLM")
    pack.ai_draft_started_at = datetime.now(UTC)
    audit(
        session, principal.user_id, "evidence.ai_draft_queue", f"user:{user.id}", period=str(start)
    )
    session.commit()
    try:
        queue.enqueue(
            "app.evidence.jobs.ai_draft_job",
            pack.id,
            principal.user_id,
            job_id=f"evidence-draft-{pack.id}",
            job_timeout=int(draft.TIMEOUT_S) + 120,
        )
    except Exception:
        pack = _stored(session, user.id, start, lock=True) or pack
        pack.ai_draft_started_at = None
        session.commit()
        raise HTTPException(503, "could not queue the draft; try again") from None
    return _out(session, principal.user_id, user, role, start, pack)


Action = Literal["refresh", "submit", "return", "review", "share"]
# (allowed from, goes to); refresh keeps the draft a draft.
TRANSITIONS: dict[str, tuple[str, str]] = {
    "refresh": ("draft", "draft"),
    "submit": ("draft", "submitted"),
    "return": ("submitted", "draft"),
    "review": ("submitted", "reviewed"),
    "share": ("reviewed", "shared"),
}


@router.post("/evidence/{user_id}/{period}/{action}")
def act(
    user_id: int,
    period: str,
    action: Action,
    principal: EvidenceViewer,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    user, role = _person(session, principal, user_id, settings)
    start = _period(period, settings)
    # Locked: a concurrent action waits here and then sees the state this one leaves.
    pack = _stored(session, user.id, start, lock=True)
    if not _can(role, pack.status if pack else None)[action]:
        raise HTTPException(409, f"cannot {action} this pack now")
    built_now = False
    if pack is None:
        pack = _create(session, user.id, start, settings)
        built_now = True
    source, target = TRANSITIONS[action]
    if pack.status != source:  # created by someone else meanwhile, in another state
        raise HTTPException(409, f"the pack is {pack.status}")
    now = datetime.now(UTC)
    if action in ("refresh", "submit") and not built_now:
        # Submitting freezes what the system gathered at that moment.
        pack.snapshot = _build(session, user.id, start, settings)
        pack.insights_version = INSIGHTS_VERSION
    if action == "submit":
        pack.submitted_at = now
    elif action == "review":
        pack.reviewed_by, pack.reviewed_at = principal.user_id, now
    elif action == "share":
        pack.shared_at = now
    elif action == "return":
        pack.submitted_at = None
    pack.status = target
    audit(session, principal.user_id, f"evidence.{action}", f"user:{user.id}", period=str(start))
    session.commit()
    return _out(session, principal.user_id, user, role, start, pack)
