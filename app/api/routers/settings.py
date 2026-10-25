"""Review settings edited on the Admin screen (outside the spec, requested by the user; see
PROGRESS.md). Today MAX_REVIEW_LOC; the next review uses the new value, no restart."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.admin_settings import EDITABLE, effective, overrides, set_value
from app.api.audit import audit
from app.api.deps import get_session
from app.api.schemas import ReviewSettingOut, ReviewSettingsIn
from app.api.security import AdminPrincipal
from app.config import Settings, get_settings

router = APIRouter(tags=["admin"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _out(session: Session, settings: Settings) -> list[ReviewSettingOut]:
    stored = overrides(session)
    values = effective(session, settings)
    return [
        ReviewSettingOut(
            key=key,
            env=spec.env,
            value=values[key],
            default=int(getattr(settings, key)),
            overridden=key in stored,
            minimum=spec.minimum,
            maximum=spec.maximum,
        )
        for key, spec in EDITABLE.items()
    ]


@router.get("/admin/review-settings")
def review_settings(
    principal: AdminPrincipal, session: SessionDep, settings: SettingsDep
) -> list[ReviewSettingOut]:
    return _out(session, settings)


@router.put("/admin/review-settings")
def update_review_settings(
    body: ReviewSettingsIn, principal: AdminPrincipal, session: SessionDep, settings: SettingsDep
) -> list[ReviewSettingOut]:
    """Only the keys sent change; null restores the environment default."""
    changes = body.model_dump(exclude_unset=True)
    before = effective(session, settings)
    try:
        for key, value in changes.items():
            set_value(session, key, value, principal.user_id)
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
