"""Monthly drafts (docs/specs/20 §6.3 step 1): on the 1st, everyone active last month gets a
draft pack, so the 1:1 starts from what the system already gathered."""

import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.evidence import snapshot
from app.insights import INSIGHTS_VERSION
from app.models import EvidencePack, User

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
