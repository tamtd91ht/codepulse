"""Monthly drafts (docs/specs/20 §6.3 step 1): on the 1st, everyone active last month gets a
draft pack, so the 1:1 starts from what the system already gathered."""

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.config import Settings
from app.crypto import SecretBox
from app.evidence import draft, snapshot
from app.insights import INSIGHTS_VERSION
from app.models import EvidenceItem, EvidencePack, Project, Review, User

log = logging.getLogger(__name__)


def previous_month(today: date) -> date:
    first = today.replace(day=1)
    return date(first.year - 1, 12, 1) if first.month == 1 else date(first.year, first.month - 1, 1)


def create_drafts(
    session: Session,
    period_start: date,
    tz: ZoneInfo,
    stale_hours: int,
    knowledge: snapshot.KnowledgeConfig | None = None,
) -> int:
    """A draft for every active person (switched-on users) without a pack that month."""
    flows = snapshot.month_flows(session, period_start, tz)
    people = snapshot.active_people(flows)
    if not people:
        return 0
    have = set(
        session.scalars(
            select(EvidencePack.user_id).where(EvidencePack.period_start == period_start)
        )
    )
    active = set(
        session.scalars(select(User.id).where(User.id.in_(people), User.active.is_not(False)))
    )
    created = 0
    for user_id in sorted(active - have):
        # One person at a time: a pack created meanwhile (by the person, or another run) or
        # one failing snapshot must not cost everyone else their draft.
        try:
            with session.begin_nested():
                built = snapshot.build(
                    session,
                    user_id,
                    period_start,
                    tz,
                    stale_hours,
                    flows=flows,
                    knowledge=knowledge,
                )
                inserted = session.execute(
                    pg_insert(EvidencePack)
                    .values(
                        user_id=user_id,
                        period_start=period_start,
                        status="draft",
                        snapshot=built,
                        insights_version=INSIGHTS_VERSION,
                    )
                    .on_conflict_do_nothing(index_elements=["user_id", "period_start"])
                    .returning(EvidencePack.id)
                )
                created += 1 if inserted.scalar_one_or_none() is not None else 0
        except Exception:
            log.exception(
                "evidence draft failed", extra={"user_id": user_id, "period": str(period_start)}
            )
    log.info("evidence drafts", extra={"period": str(period_start), "created": created})
    return created


def create_drafts_job() -> dict[str, int]:
    """Scheduler entry point (days 1-3 of the month, REPORT_TZ): last month's drafts. Running
    on three days lets a missed run catch up; people who already have a pack are skipped."""
    from app.config import get_settings
    from app.db import get_sessionmaker

    settings = get_settings()
    tz = ZoneInfo(settings.report_tz)
    period = previous_month(datetime.now(UTC).astimezone(tz).date())
    with get_sessionmaker()() as session, session.begin():
        created = create_drafts(
            session,
            period,
            tz,
            settings.insights_stale_sync_hours,
            snapshot.knowledge_config(settings),
        )
    return {"created": created}


# -- AI draft of the lead's note (docs/specs/20 §6.4, 9.11) --------------------------------------

# A draft job dead longer than this (its timeout plus margin) no longer holds the pack.
DRAFT_STALE = timedelta(minutes=10)
DraftRunner = Callable[[str, Mapping[str, str], Settings, str], draft.DraftOutcome]


def llm_token(session: Session, box: SecretBox, settings: Settings) -> str:
    """The Claude account reviews would use now (app/review/llm_accounts.py)."""
    from app.review.llm_accounts import current_token

    return current_token(session, box, settings)


def refused_repos(session: Session, snap: Mapping[str, Any]) -> list[int]:
    """Repos the prompt draws on (MRs, and the reviews behind its findings) that refuse
    external LLMs (`projects.allow_external_llm = false`)."""
    from app.api.access import review_repo

    repo_ids = {
        int(m["project_id"])
        for key in ("merged", "reviews_given")
        for m in (snap.get(key) or {}).get("items", [])
        if m.get("project_id") is not None
    }
    for f in (snap.get("quality") or {}).get("items", []):
        review = session.get(Review, f.get("review_id")) if f.get("review_id") else None
        repo = review_repo(session, review) if review else None
        if repo is not None:
            repo_ids.add(repo.id)
    if not repo_ids:
        return []
    return list(
        session.scalars(
            select(Project.id).where(
                Project.id.in_(repo_ids), Project.allow_external_llm.is_(False)
            )
        )
    )


def _run_cli(
    prompt: str, known: Mapping[str, str], settings: Settings, token: str
) -> draft.DraftOutcome:
    from app.admin_settings import effective
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        model = str(effective(session, settings)["llm_model"])
    return draft.run_draft(
        binary=settings.claude_code_bin, model=model, token=token, prompt=prompt, known=known
    )


def write_draft(
    session: Session,
    pack_id: int,
    actor_id: int,
    settings: Settings,
    token: str,
    runner: DraftRunner = _run_cli,
) -> draft.DraftOutcome:
    """Draft one pack's lead note. The row is not locked while the model writes; afterwards the
    pack must still be open to the lead, else the draft is dropped. The cost is audited whatever
    the outcome (it was paid)."""
    pack = session.get(EvidencePack, pack_id)
    if pack is None:
        return draft.DraftOutcome(error="evidence pack not found")
    items = [
        {"id": i.id, "kind": i.kind, "title": i.title, "note": i.note}
        for i in session.scalars(select(EvidenceItem).where(EvidenceItem.pack_id == pack.id))
    ]
    snap = pack.snapshot or {}
    card = _dpi_card(session, pack.user_id, pack.period_start)
    known = draft.references(snap, items, card)
    prompt = draft.build_prompt(snap, items, pack.self_notes, card)
    user_id, period = pack.user_id, str(pack.period_start)
    session.commit()  # no lock and no open transaction while the model writes
    outcome = runner(prompt, known, settings, token)
    pack = session.scalars(
        select(EvidencePack).where(EvidencePack.id == pack_id).with_for_update()
    ).first()
    if pack is None:
        return outcome
    kept = outcome.ok and pack.status in ("submitted", "reviewed")
    if kept:
        pack.ai_draft = outcome.text
    pack.ai_draft_started_at = None
    audit(
        session,
        actor_id,
        "evidence.ai_draft",
        f"user:{user_id}",
        period=period,
        ok=outcome.ok,
        kept=kept,
        model=outcome.model,
        cost_usd=outcome.cost_usd,
        dropped=outcome.dropped,
        error=(outcome.error or "")[:300] or None,
    )
    session.commit()
    return outcome


def ai_draft_job(pack_id: int, actor_id: int) -> dict[str, Any]:
    """RQ entry point (queued by POST /evidence/{id}/{yyyy-mm}/ai-draft)."""
    from app.config import get_settings
    from app.crypto import secret_box
    from app.db import get_sessionmaker

    settings = get_settings()
    with get_sessionmaker()() as session:
        token = llm_token(session, secret_box(), settings)
        outcome = write_draft(session, pack_id, actor_id, settings, token)
    return {"ok": outcome.ok, "cost_usd": outcome.cost_usd, "error": outcome.error}


def _dpi_card(session: Session, user_id: int, month: Any) -> dict[str, Any] | None:
    """The month's latest DPI scorecard (docs/specs/25 §8: the AI draft reads it), if any."""
    from app.models import DpiSnapshot

    row = session.scalars(
        select(DpiSnapshot)
        .where(DpiSnapshot.user_id == user_id, DpiSnapshot.month == month)
        .order_by((DpiSnapshot.kind == "final").desc(), DpiSnapshot.id.desc())
        .limit(1)
    ).first()
    return dict(row.values) if row else None
