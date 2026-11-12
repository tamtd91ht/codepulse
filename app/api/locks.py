"""Short waits for row locks held by background jobs (user 2026-11-12: "Gắn" a review's author
hung, then failed).

A GitLab sync runs its whole collection in one transaction; any row it wrote stays locked until
it ends, minutes later. A request that writes one of those rows (linking an author account to a
person) would wait the whole time, then fail on the proxy's timeout with no explanation. Such a
request waits at most a few seconds and answers "busy, try again" instead.
"""

import functools
from collections.abc import Callable
from typing import Any, TypeVar, cast

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

F = TypeVar("F", bound=Callable[..., Any])

LOCK_WAIT_MS = 8_000
LOCK_NOT_AVAILABLE = "55P03"  # lock_timeout ran out
DEADLOCK = "40P01"
ACCOUNT_BUSY = (
    "Tác giả này đang được cập nhật bởi lượt đồng bộ GitLab đang chạy; thử lại sau 1-2 phút."
)


def short_lock_wait(session: Session, ms: int | None = None) -> None:
    """Every lock this transaction waits for (rows, advisory locks) gives up after `ms`
    (LOCK_WAIT_MS by default)."""
    wait = f"{ms if ms is not None else LOCK_WAIT_MS}ms"
    session.execute(text("SELECT set_config('lock_timeout', :wait, true)"), {"wait": wait})


def lock_busy(exc: OperationalError) -> bool:
    return getattr(exc.orig, "sqlstate", None) in (LOCK_NOT_AVAILABLE, DEADLOCK)


def busy_as_409(message: str = ACCOUNT_BUSY) -> Callable[[F], F]:
    """An endpoint whose lock wait ran out answers 409 with `message` (its session rolled
    back) instead of a 500."""

    def wrap(fn: F) -> F:
        @functools.wraps(fn)
        def endpoint(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except OperationalError as exc:
                if not lock_busy(exc):
                    raise
                session = kwargs.get("session")
                if isinstance(session, Session):
                    session.rollback()
                raise HTTPException(409, message) from None

        return cast(F, endpoint)

    return wrap
