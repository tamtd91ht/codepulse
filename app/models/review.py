from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

SEVERITIES = ("critical", "high", "medium", "low", "info")


class Review(TimestampMixin, Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("target_type IN ('mr','commit')", name="target_type"),
        # NULLS NOT DISTINCT: one of mr_id / commit_sha is always NULL, so a plain UNIQUE
        # would never fire.
        UniqueConstraint(
            "target_type",
            "mr_id",
            "commit_sha",
            "reviewed_sha",
            "rubric_version",
            name="uq_reviews_target",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_reviews_author_user_id_finished_at", "author_user_id", "finished_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    target_type: Mapped[str]
    mr_id: Mapped[int | None] = mapped_column(ForeignKey("merge_requests.id"), index=True)
    commit_sha: Mapped[str | None] = mapped_column(ForeignKey("commits.sha"), index=True)
    reviewed_sha: Mapped[str]
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str]  # queued|running|done|failed|skipped|superseded
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    loc_reviewed: Mapped[int | None]
    summary: Mapped[str | None]
    model: Mapped[str | None]
    rubric_version: Mapped[str | None]
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    error: Mapped[str | None]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewFinding(TimestampMixin, Base):
    __tablename__ = "review_findings"
    __table_args__ = (
        CheckConstraint("severity IN ('critical','high','medium','low','info')", name="severity"),
        Index("ix_review_findings_review_id_severity", "review_id", "severity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(server_default="ai")  # ai|semgrep|sonar
    severity: Mapped[str]
    category: Mapped[str]  # security|bug|performance|maintainability|testing|style|docs
    rule_id: Mapped[str | None]
    file_path: Mapped[str | None]
    line_start: Mapped[int | None]
    line_end: Mapped[int | None]
    title: Mapped[str | None]
    message: Mapped[str | None]
    suggestion: Mapped[str | None]
    code_excerpt: Mapped[str | None]
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    fingerprint: Mapped[str | None]
    # open|fixed|disputed|accepted_risk|false_positive
    status: Mapped[str | None] = mapped_column(server_default="open")


class FindingDispute(TimestampMixin, Base):
    __tablename__ = "finding_disputes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    finding_id: Mapped[int | None] = mapped_column(ForeignKey("review_findings.id"), index=True)
    raised_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None]
    decision: Mapped[str | None]
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
