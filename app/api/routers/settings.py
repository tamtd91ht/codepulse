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
from app.api.routers.closures import requests_last_day
from app.api.schemas import CloseQuotaIn, CloseQuotaOut, ReviewSettingOut, ReviewSettingsIn
from app.api.security import Principal, require
from app.config import Settings, get_settings
from app.models import User

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


# -- a person's close-request limit (user 2026-11-06) ------------------------------------------
# Same right as the default on Admin > Cấu hình: each request may cost a paid check.


def _quota(session: Session, settings: Settings, user: User) -> CloseQuotaOut:
    default = int(effective(session, settings)["close_requests_per_day"])
    own = user.close_requests_per_day
    return CloseQuotaOut(
        user_id=user.id,
        own=own,
        default=default,
        limit=default if own is None else own,
        used_24h=requests_last_day(session, user.id),
    )


@router.get("/admin/users/{user_id}/close-quota", response_model=CloseQuotaOut)
def close_quota(
    user_id: int, principal: ReviewSettingsAdmin, session: SessionDep, settings: SettingsDep
) -> CloseQuotaOut:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    return _quota(session, settings, user)


@router.put("/admin/users/{user_id}/close-quota", response_model=CloseQuotaOut)
def set_close_quota(
    user_id: int,
    body: CloseQuotaIn,
    principal: ReviewSettingsAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> CloseQuotaOut:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    if not principal.is_admin:
        # As for every other per-person edit (app/api/routers/admin.py): an admin's limit is an
        # admin's to set, and nobody raises their own.
        if user.id == principal.user_id:
            raise HTTPException(403, "không tự đặt giới hạn cho chính mình")
        admins = {e.lower() for e in settings.admin_emails}
        if (user.primary_email or "").lower() in admins:
            raise HTTPException(403, "chỉ admin đặt giới hạn cho admin")
    old = user.close_requests_per_day
    user.close_requests_per_day = body.own
    audit(
        session,
        principal.user_id,
        "user.close_quota",
        f"user:{user.id}",
        old=old,
        new=body.own,
    )
    session.commit()
    return _quota(session, settings, user)
