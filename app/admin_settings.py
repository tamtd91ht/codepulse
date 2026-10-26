"""Review settings an admin edits on the Admin screen (outside the spec, requested by the user;
see PROGRESS.md). A stored value overrides the environment default; clearing it restores that.

Workers read them per job, so a change applies to the next review without a restart.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppSetting

# The review model when neither the Admin screen nor LLM_MODEL names one.
DEFAULT_REVIEW_MODEL = "claude-opus-5-5"
# A model id or alias as `claude --model` takes it (claude-opus-5-5, opus, claude-x[1m]); never
# starts with "-", so it cannot pass for another CLI flag.
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,99}$")
MAX_URL_CHARS = 300

Value = int | str


@dataclass(frozen=True)
class Editable:
    env: str  # the environment variable it overrides
    kind: Literal["int", "model", "url"]
    minimum: int = 0
    maximum: int = 0


EDITABLE: dict[str, Editable] = {
    # docs/specs/06: a larger diff is skipped as "MR quá lớn".
    "max_review_loc": Editable("MAX_REVIEW_LOC", "int", 1, 1_000_000),
    # The model of every review call (fast pass, deep pass, project profile).
    "llm_model": Editable("LLM_MODEL", "model"),
    # .claude files quoted to the model (app/review/claude_kit.py, claude_repo.py): one file,
    # the first pass in total (repeated in each part of a large diff), the deep pass in total.
    "claude_file_chars": Editable("CLAUDE_FILE_CHARS", "int", 2_000, 500_000),
    "fast_guidance_chars": Editable("FAST_GUIDANCE_CHARS", "int", 5_000, 1_000_000),
    "deep_instructions_chars": Editable("DEEP_INSTRUCTIONS_CHARS", "int", 10_000, 2_000_000),
    # Base of the links sent out (Telegram, notification webhooks, report e-mails). Sign-in also
    # accepts it as an origin (app/api/auth.py); CORS keeps DASHBOARD_URL.
    "public_url": Editable("DASHBOARD_URL", "url"),
}


def defaults(settings: Settings) -> dict[str, Value]:
    """What each key is when nobody set it on the Admin screen."""
    return {
        "max_review_loc": settings.max_review_loc,
        "llm_model": settings.llm_model.strip() or DEFAULT_REVIEW_MODEL,
        "public_url": settings.dashboard_url.rstrip("/"),
        "claude_file_chars": settings.claude_file_chars,
        "fast_guidance_chars": settings.fast_guidance_chars,
        "deep_instructions_chars": settings.deep_instructions_chars,
    }


def overrides(session: Session) -> dict[str, Any]:
    rows = session.scalars(select(AppSetting).where(AppSetting.key.in_(EDITABLE))).all()
    return {row.key: row.value for row in rows}


def effective(session: Session, settings: Settings) -> dict[str, Value]:
    """Every editable key with the value reviews use now."""
    stored = overrides(session)
    return {key: stored.get(key, default) for key, default in defaults(settings).items()}


def with_overrides(session: Session, settings: Settings) -> Settings:
    """`settings` with what an admin set on top, for code that reads them from Settings (the
    review provider's model, the links in messages)."""
    values = effective(session, settings)
    return settings.model_copy(
        update={
            "max_review_loc": int(values["max_review_loc"]),
            "llm_model": str(values["llm_model"]),
            "dashboard_url": str(values["public_url"]),
        }
    )


def link_base(session: Session, settings: Settings) -> str:
    """Where the links sent out point: the Admin screen's public URL, else DASHBOARD_URL."""
    return str(effective(session, settings)["public_url"])


def normalize_url(raw: str) -> str:
    """`abc.xyz`, `https://abc.xyz/codepulse/` or `10.0.0.5:3000` -> an http(s) base without a
    trailing slash. No credentials, query or fragment: it is pasted in front of /reviews/42."""
    text = raw.strip()
    if not text or any(c.isspace() for c in text) or len(text) > MAX_URL_CHARS:
        raise ValueError("public_url must be a URL such as https://codepulse.company.vn")
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    try:
        parts.port  # noqa: B018 - raises on a port that is not a number
    except ValueError:
        raise ValueError("public_url has an invalid port") from None
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("public_url must be an http(s) address without login, ? or #")
    return text.rstrip("/")


def clean(key: str, value: object) -> Value:
    """The value to store for `key`, or ValueError. Checked here, not only in the API."""
    spec = EDITABLE[key]
    if spec.kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be a whole number")
        if not spec.minimum <= value <= spec.maximum:
            raise ValueError(f"{key} must be between {spec.minimum} and {spec.maximum}")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    if spec.kind == "model":
        model = value.strip()
        if not MODEL_ID.match(model):
            raise ValueError("llm_model must be a model id such as claude-opus-5-5")
        return model
    return normalize_url(value)


def set_value(session: Session, key: str, value: object | None, user_id: int | None) -> None:
    """Store an override, or clear it with None."""
    row = session.get(AppSetting, key)
    if value is None:
        if row is not None:
            session.delete(row)
    else:
        cleaned = clean(key, value)
        if row is None:
            row = AppSetting(key=key)
            session.add(row)
        row.value, row.set_by = cleaned, user_id
    session.flush()
