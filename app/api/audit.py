from typing import Any

from sqlalchemy.orm import Session

from app.logging import request_id_var
from app.models import AuditLog


def audit(
    session: Session,
    actor_user_id: int | None,
    action: str,
    target: str | None = None,
    *,
    # `Any`, not `int | None`: callers spread `**{...: str}` details, which mypy would match
    # against this keyword; a non-int value is kept in the details only.
    business_project_id: Any = None,
    **details: Any,
) -> None:
    """Append one audit row in the caller's transaction (docs/specs/09, 11).

    `business_project_id` is the row's dự án (its own column: the testcase audit trail filters
    on it, docs/specs/30 §15.14); it stays in `details` too, as callers have always had it."""
    bp: int | None = None
    if business_project_id is not None:
        details = {"business_project_id": business_project_id, **details}
        if isinstance(business_project_id, int) and not isinstance(business_project_id, bool):
            bp = business_project_id
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target=target,
            details={k: v for k, v in details.items() if v is not None} or None,
            request_id=request_id_var.get(),
            business_project_id=bp,
        )
    )
