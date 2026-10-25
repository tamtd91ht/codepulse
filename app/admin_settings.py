"""Review settings an admin edits on the Admin screen (outside the spec, requested by the user;
see PROGRESS.md). A stored value overrides the environment default; clearing it restores that.

Workers read them per job, so a change applies to the next review without a restart.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppSetting


@dataclass(frozen=True)
class Editable:
    env: str  # the environment variable it overrides
    minimum: int
    maximum: int


EDITABLE: dict[str, Editable] = {
    # docs/specs/06: a larger diff is skipped as "MR quá lớn".
    "max_review_loc": Editable("MAX_REVIEW_LOC", 1, 1_000_000),
}


def overrides(session: Session) -> dict[str, Any]:
    rows = session.scalars(select(AppSetting).where(AppSetting.key.in_(EDITABLE))).all()
    return {row.key: row.value for row in rows}


def effective(session: Session, settings: Settings) -> dict[str, int]:
    """Every editable key with the value reviews use now."""
    stored = overrides(session)
    return {key: int(stored.get(key, getattr(settings, key))) for key in EDITABLE}


def set_value(session: Session, key: str, value: int | None, user_id: int | None) -> None:
    """Store an override, or clear it with None. Bounds are checked here, not only in the API."""
    spec = EDITABLE[key]
    row = session.get(AppSetting, key)
    if value is None:
        if row is not None:
            session.delete(row)
    else:
        if not spec.minimum <= value <= spec.maximum:
            raise ValueError(f"{key} must be between {spec.minimum} and {spec.maximum}")
        if row is None:
            row = AppSetting(key=key)
            session.add(row)
        row.value, row.set_by = value, user_id
    session.flush()
