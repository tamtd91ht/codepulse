"""Anonymous usage events (docs/specs/31 §21.2): which screens and flows are used, by role.

Only (role, event, surface, at): never a user id, a task, a key or any text. Kept
`app.usage.KEEP_DAYS` (180) days; the task-automation tick deletes older rows.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_at", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # The person's highest automatic role (member … admin), or "system" for git and rules.
    role: Mapped[str] = mapped_column(String(20))
    event: Mapped[str] = mapped_column(String(40))
    surface: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
