"""Outbound notification webhooks (outside docs/specs/03; requested by the user, see
docs/specs/16_NOTIFICATION_WEBHOOKS.md).

Not the GitLab hook (that one is inbound, GitLab -> CodePulse). These POST the result of a
finished review to an endpoint the admin names: a chat connector, an alerting tool, a company
service. Scoped to one dự án or one repo, or to everything when both are NULL.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class NotificationWebhook(TimestampMixin, Base):
    __tablename__ = "notification_webhooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    url: Mapped[str]
    # json (CodePulse payload, HMAC signed when a secret is set) | slack | teams (text cards).
    format: Mapped[str] = mapped_column(server_default="json")
    # Fernet like the GitLab token; signs the JSON body (X-CodePulse-Signature).
    secret_encrypted: Mapped[str | None]
    # ["review.done", "review.failed", "review.severe"]
    events: Mapped[list[str]] = mapped_column(JSONB)
    business_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE")
    )
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    enabled: Mapped[bool] = mapped_column(server_default="true")
    # Delivery health, shown in the dashboard; many failures in a row disable the hook.
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_code: Mapped[int | None]
    last_error: Mapped[str | None]
    failures: Mapped[int] = mapped_column(server_default="0")
    deliveries: Mapped[int] = mapped_column(server_default="0")

    def scope(self) -> dict[str, Any]:
        return {"business_project_id": self.business_project_id, "project_id": self.project_id}


class UserNotification(TimestampMixin, Base):
    """In-app notification of one person (docs/specs/24): the bell, the toast and the history.

    Written in the transaction of the event itself (a review saved, a finding closed, a dispute
    raised or decided), pushed live once that transaction commits (app/notifications/inbox.py).
    """

    __tablename__ = "user_notifications"
    __table_args__ = (
        # The history (newest first) and the unread count of one person.
        Index("ix_user_notifications_user_id_id", "user_id", "id"),
        Index(
            "ix_user_notifications_unread",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index("ix_user_notifications_actor_id", "actor_id"),
        Index("ix_user_notifications_created_at", "created_at"),  # the daily retention delete
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # review_done | review_failed | finding_closed | dispute_new | dispute_decided
    kind: Mapped[str]
    title: Mapped[str]
    body: Mapped[str | None]
    link: Mapped[str | None]  # a dashboard path, e.g. /reviews/12#finding-40
    tone: Mapped[str] = mapped_column(server_default="info")  # info | good | warn | bad
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ref: Mapped[str | None]  # review:12, finding:40, dispute:7
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
