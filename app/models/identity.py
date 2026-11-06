from datetime import date

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Team(TimestampMixin, Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "close_requests_per_day IS NULL OR close_requests_per_day BETWEEN 0 AND 1000",
            name="close_requests_per_day",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str | None]
    primary_email: Mapped[str | None] = mapped_column(unique=True)
    active: Mapped[bool | None] = mapped_column(server_default="true")
    # Manages every dự án without a grant (below admin, 2026-11-02; app/api/security.py).
    manage_all: Mapped[bool] = mapped_column(server_default=false(), default=False)
    # Admin > Người & Team (2026-11-03): the one avatar file in AVATAR_DIR ("<id>.<ext>").
    avatar_file: Mapped[str | None]
    # Close requests this person may send in 24 hours (web and MCP); NULL: the default of
    # Admin > Cấu hình (close_requests_per_day), 0: none (user 2026-11-06).
    close_requests_per_day: Mapped[int | None]


class TeamMember(TimestampMixin, Base):
    __tablename__ = "team_members"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    joined_at: Mapped[date] = mapped_column(primary_key=True)
    role: Mapped[str | None]
    left_at: Mapped[date | None]


class Account(TimestampMixin, Base):
    """One person has many accounts/emails; kind is 'gitlab_user', 'git_email' or 'jira_user'
    (docs/specs/27 §7)."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("kind", "external_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]
    external_key: Mapped[str]
    display_name: Mapped[str | None]
    # What the source says of the account, kept to retry linking it later (docs/specs/27 §7):
    # for a jira_user its emailAddress and name (username). Not set for the other kinds.
    email: Mapped[str | None]
    login: Mapped[str | None]
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
