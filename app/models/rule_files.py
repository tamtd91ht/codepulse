"""Review rules imported as files (docs/specs/17 §Tệp quy tắc, user 2026-11-11): besides the
rules typed on the screen, a dự án may hold several imported files (.md, .txt, .doc, .docx). Each
is stored converted to Markdown (never as the original bytes) and goes into every review prompt
of the dự án as a rules source of its own, cited by its name."""

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ReviewRuleFile(TimestampMixin, Base):
    __tablename__ = "review_rule_files"
    __table_args__ = (UniqueConstraint("business_project_id", "name", name="uq_rule_file_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    # The file name as uploaded (a new upload of the same name replaces the text).
    name: Mapped[str]
    body: Mapped[str]  # Markdown, converted from the file
    chars: Mapped[int]
    content_hash: Mapped[str]
    enabled: Mapped[bool] = mapped_column(server_default="true", default=True)
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
