from datetime import date

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Team(TimestampMixin, Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str | None]
    primary_email: Mapped[str | None] = mapped_column(unique=True)
    active: Mapped[bool | None] = mapped_column(server_default="true")


class TeamMember(TimestampMixin, Base):
    __tablename__ = "team_members"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    joined_at: Mapped[date] = mapped_column(primary_key=True)
    role: Mapped[str | None]
    left_at: Mapped[date | None]


class Account(TimestampMixin, Base):
    """One person has many accounts/emails; kind is 'gitlab_user' or 'git_email'."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("kind", "external_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]
    external_key: Mapped[str]
    display_name: Mapped[str | None]
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
