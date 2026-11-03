"""Permission sets (docs/specs/21): named bundles of catalog permissions (app/permissions/
catalog.py), each at a scope, given to people. Built-in sets follow a role (`auto_role`)
instead of being given by hand."""

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PermissionSet(TimestampMixin, Base):
    __tablename__ = "permission_sets"
    __table_args__ = (
        CheckConstraint(
            "auto_role IS NULL OR auto_role IN "
            "('member','lead','manager','project_manager','manage_all')",
            name="auto_role",
        ),
        UniqueConstraint("auto_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    description: Mapped[str | None]
    builtin: Mapped[bool] = mapped_column(default=False)
    # Who holds it without being given it: everyone of that role (built-in sets only).
    auto_role: Mapped[str | None]


class PermissionSetItem(Base):
    __tablename__ = "permission_set_items"

    set_id: Mapped[int] = mapped_column(
        ForeignKey("permission_sets.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(primary_key=True)  # a catalog key
    scope: Mapped[str]  # own|team|project|all, or "flag"


class UserPermissionSet(TimestampMixin, Base):
    __tablename__ = "user_permission_sets"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    set_id: Mapped[int] = mapped_column(
        ForeignKey("permission_sets.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
