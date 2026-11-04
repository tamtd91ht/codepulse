"""Review settings edited on the Admin screen (outside the spec, requested by the user; see
PROGRESS.md): MAX_REVIEW_LOC, the review model and the base of the links sent out. The next
review uses the new value, no restart."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.admin_settings import (
    EDITABLE,
    check_auto_close,
    defaults,
    effective,
    overrides,
    set_value,
)
from app.api.audit import audit
from app.api.deps import get_session
from app.api.schemas import ReviewSettingOut, ReviewSettingsIn
from app.api.security import Principal, require
from app.config import Settings, get_settings

router = APIRouter(tags=["admin"])
SessionDep = Annotated[Session, Depends(get_session)]
ReviewSettingsAdmin = Annotated[Principal, require("admin.review_settings")]  # docs/specs/21 B4
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _out(session: Session, settings: Settings) -> list[ReviewSettingOut]:
    stored = overrides(session)
    values = effective(session, settings)
    base = defaults(settings)
    return [
        ReviewSettingOut(
            key=key,
            env=spec.env,
            kind=spec.kind,
            value=values[key],
            default=base[key],
            overridden=key in stored,
            minimum=spec.minimum if spec.kind == "int" else None,
            maximum=spec.maximum if spec.kind == "int" else None,
        )
        for key, spec in EDITABLE.items()
    ]


@router.get("/admin/review-settings")
def review_settings(
    principal: ReviewSettingsAdmin, session: SessionDep, settings: SettingsDep
) -> list[ReviewSettingOut]:
    return _out(session, settings)


@router.put("/admin/review-settings")
def update_review_settings(
    body: ReviewSettingsIn,
    principal: ReviewSettingsAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> list[ReviewSettingOut]:
    """Only the keys sent change; null restores the environment default."""
    changes = body.model_dump(exclude_unset=True)
    before = effective(session, settings)
    try:
        for key, value in changes.items():
            set_value(session, key, value, principal.user_id)
        check_auto_close(session, settings)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    after = effective(session, settings)
    for key in changes:
        audit(
            session,
            principal.user_id,
            "settings.update",
            f"setting:{key}",
            old=before[key],
            new=after[key],
        )
    session.commit()
    return _out(session, settings)


@router.get("/admin/llm-health")
def llm_health(principal: ReviewSettingsAdmin, session: SessionDep) -> dict[str, object]:
    """Is the LLM working, since when not, when it is probed next, how many reviews wait."""
    from app.review import llm_recovery

    return llm_recovery.status(session)


@router.post("/admin/llm-health/probe")
def llm_probe_now(principal: ReviewSettingsAdmin, session: SessionDep) -> dict[str, object]:
    """Probe at the next tick (within a few minutes) instead of at the end of the interval."""
    from app.review import llm_recovery

    if not llm_recovery.probe_soon(session):
        raise HTTPException(409, "the LLM is not down")
    audit(session, principal.user_id, "llm.probe_now", "llm_health")
    session.commit()
    session.expire_all()
    return llm_recovery.status(session)
