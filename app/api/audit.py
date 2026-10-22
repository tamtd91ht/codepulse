from typing import Any

from sqlalchemy.orm import Session

from app.logging import request_id_var
from app.models import AuditLog


def audit(
    session: Session,
    actor_user_id: int | None,
    action: str,
    target: str | None = None,
    **details: Any,
) -> None:
    """Append one audit row in the caller's transaction (docs/specs/09, 11)."""
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target=target,
            details={k: v for k, v in details.items() if v is not None} or None,
            request_id=request_id_var.get(),
        )
    )
