"""Permission sets (docs/specs/21): named bundles of catalog permissions (app/permissions/
catalog.py), each at a scope, given to people. Built-in sets follow a role (`auto_role`)
instead of being given by hand."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PermissionSet(TimestampMixin, Base):
    __tablename__ = "permission_sets"
    __table_args__ = (
        CheckConstraint(
            "auto_role IS NULL OR auto_role IN "
            "('member','lead','manager','project_manager','manage_all','project_admin','tester')",
            name="auto_role",
        ),
        UniqueConstraint("auto_role"),
        # docs/specs/21 §13: a dự án's set is never a built-in one.
        CheckConstraint(
            "business_project_id IS NULL OR (NOT builtin AND auto_role IS NULL)",
            name="project_not_builtin",
        ),
        # Names unique per owner (the system, or one dự án); a person's own set, one per owner.
        Index(
            "uq_permission_sets_owner_name",
            func.coalesce(text("business_project_id"), 0),
            func.lower(text("name")),
            unique=True,
        ),
        Index(
            "uq_permission_sets_personal_owner",
            "personal_user_id",
            func.coalesce(text("business_project_id"), 0),
            unique=True,
            postgresql_where=text("personal_user_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    description: Mapped[str | None]
    builtin: Mapped[bool] = mapped_column(default=False)
    # Who holds it without being given it: everyone of that role (built-in sets only).
    auto_role: Mapped[str | None]
    # A person's own set (the permissions ticked on their profile, docs/specs/21 §9): hidden
    # from the list of sets, given to that person only.
    personal_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # docs/specs/21 §13: None = holds across the system; else a set of that dự án only, whose
    # items count over that dự án while its holder still has a grant there.
    business_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )


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


class TeamPermissionSet(TimestampMixin, Base):
    """A custom set given to everyone currently in a team (docs/specs/21 §11, e.g. the QA team
    holds "Tester"): one rule instead of assigning each person."""

    __tablename__ = "team_permission_sets"

    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    set_id: Mapped[int] = mapped_column(
        ForeignKey("permission_sets.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
