"""Wipe this server's data from the Admin screen (outside the spec, requested by the user; see
PROGRESS.md and app/collector/purge.py). Admins are exactly the ADMIN_EMAILS of .env."""

import logging
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.schemas import PurgeIn, PurgeInfoOut, PurgeOut, PurgeScopeOut
from app.api.security import AdminPrincipal
from app.collector.purge import CONFIRMATION, counts, purge, tables
from app.collector.removal import REMOVAL_JOB_TIMEOUT
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.models import User

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/admin/purge")
def purge_info(principal: AdminPrincipal, session: SessionDep) -> PurgeInfoOut:
    """The scopes, what each deletes, and how many rows there are now."""
    return PurgeInfoOut(
        scopes=[
            PurgeScopeOut(scope=scope, confirm=phrase, tables=[t for t, _ in tables(scope)])
            for scope, phrase in CONFIRMATION.items()
        ],
        counts=counts(session),
    )


@router.post("/admin/purge")
def purge_data(
    body: PurgeIn,
    principal: AdminPrincipal,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    queue: Annotated[Enqueuer, Depends(get_queue)],
) -> PurgeOut:
    """Delete the scope's rows in this database (cannot be undone; GitLab is not touched)."""
    expected = CONFIRMATION[body.scope]
    if body.confirm.strip().casefold() != expected:
        raise HTTPException(422, f'type "{expected}" to confirm')
    try:
        # "people" keeps whoever must still sign in: ADMIN_EMAILS and the admin doing it.
        admins = {e.lower() for e in settings.admin_emails}
        keep = {principal.user_id} | set(
            session.scalars(select(User.id).where(func.lower(User.primary_email).in_(admins)))
        )
        result = purge(session, body.scope, ZoneInfo(settings.report_tz), keep)
    except (IntegrityError, OperationalError):  # a sync inserted rows, or a lock/deadlock timeout
        session.rollback()
        raise HTTPException(
            409, "a sync or review is writing right now; try again shortly"
        ) from None
    audit(session, principal.user_id, "data.purge", f"scope:{body.scope}", rows=result.rows)
    session.commit()

    queued = True
    if result.first_day and result.last_day:
        # Metrics of the MRs that stay, without the deleted reviews.
        try:
            queue.enqueue(
                "app.scoring.aggregate.recompute_job",
                result.first_day.isoformat(),
                result.last_day.isoformat(),
                job_timeout=REMOVAL_JOB_TIMEOUT * 6,
            )
        except Exception:
            log.exception("could not queue the re-aggregation after a purge")
            queued = False
    return PurgeOut(scope=body.scope, rows=result.rows, recompute_queued=queued)
