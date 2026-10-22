from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    """Admin actions, dispute decisions and reads of personal data (docs/specs/09, 11).

    Not in docs/specs/03; added by migration. Append-only: rows are never updated or deleted.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_at", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str]  # e.g. "project.update", "dispute.decide", "metrics.user.read"
    target: Mapped[str | None]  # e.g. "project:3", "user:12"
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None]
