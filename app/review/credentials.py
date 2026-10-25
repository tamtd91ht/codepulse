"""The Claude subscription token an admin set from the dashboard (outside the spec, requested by
the user; see PROGRESS.md).

Precedence for LLM_PROVIDER=claude_code: this stored token, then CLAUDE_CODE_OAUTH_TOKEN, then
LLM_API_KEY. The token is read per job, so a new login applies without restarting the worker.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.crypto import SecretBox, secret_box
from app.db import get_sessionmaker
from app.models import StoredSecret

log = logging.getLogger(__name__)

CLAUDE_TOKEN = "claude_code_oauth_token"  # noqa: S105 - a row name, not a secret
# `claude setup-token` issues sk-ant-oat… tokens: only those mean "the subscription session".
TOKEN_PREFIX = "sk-ant-oat"  # noqa: S105


@dataclass
class StoredToken:
    token: str
    source: str
    set_by: int | None
    updated_at: datetime


def stored_claude_token(session: Session, box: SecretBox) -> StoredToken | None:
    row = session.get(StoredSecret, CLAUDE_TOKEN)
    if row is None:
        return None
    return StoredToken(box.decrypt(row.value_encrypted), row.source, row.set_by, row.updated_at)


def save_claude_token(
    session: Session, box: SecretBox, token: str, *, source: str, user_id: int | None
) -> None:
    if not token.startswith(TOKEN_PREFIX):
        raise ValueError("not a Claude subscription token (sk-ant-oat…)")
    row = session.get(StoredSecret, CLAUDE_TOKEN)
    if row is None:
        row = StoredSecret(name=CLAUDE_TOKEN)
        session.add(row)
    row.value_encrypted = box.encrypt(token)
    row.source = source
    row.set_by = user_id
    session.flush()


def clear_claude_token(session: Session) -> bool:
    row = session.get(StoredSecret, CLAUDE_TOKEN)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def load_stored_claude_token() -> str:
    """For workers: the stored token, or "" (none set, or the DB cannot be read; the env token
    then applies). Never raises: a review should not fail because this lookup did."""
    try:
        with get_sessionmaker()() as session:
            stored = stored_claude_token(session, secret_box())
    except Exception as exc:
        log.warning("stored Claude token unavailable", extra={"error": exc.__class__.__name__})
        return ""
    return stored.token if stored else ""
