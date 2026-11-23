"""Who was online and when (docs/specs/33): one row per stretch of time a person spent in one
state, "online" (a tab in use) or "away" (tabs open, none in use). Offline is not stored: it is
the gap between rows. A row is written when the person's state changes, never per heartbeat.

`ended_at` NULL is the stretch still going on; the partial unique index allows one per person.
`last_seen_at` is a checkpoint the sweeper moves at most once a minute: when the live state is
lost (Redis flushed, the API killed), the open stretch is closed there.
"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

PRESENCE_STATES = ("online", "away")


class PresenceSegment(Base):
    __tablename__ = "presence_segments"
    __table_args__ = (
        CheckConstraint("state IN ('online', 'away')", name="state"),
        Index("ix_presence_segments_user_id_started_at", "user_id", "started_at"),
        Index(
            "uq_presence_segments_open",
            "user_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_presence_segments_ended_at", "ended_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
