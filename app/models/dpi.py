"""DPI: monthly developer performance (docs/specs/25). Issues and their MRs, production defects,
review comment labels, the rubric and profiles, the monthly cycle and its snapshots."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

DPI_STATUSES = ("draft", "self_review", "lead_review", "calibration", "finalized", "locked")
LINK_SOURCES = ("closes", "mention", "branch")
COMMENT_LABELS = (
    "actionable",
    "design",
    "correctness",
    "security",
    "testability",
    "cosmetic",
    "other",
)


class Issue(TimestampMixin, Base):
    """A GitLab issue of a tracked repo (docs/specs/25 §4.1): the task source of A1, A2, C2."""

    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("project_id", "iid"),
        Index("ix_issues_closed_at_src", "closed_at_src"),
        Index("ix_issues_due_date", "due_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    iid: Mapped[int]
    title: Mapped[str | None]
    state: Mapped[str | None]  # opened|closed
    labels: Mapped[list[str] | None] = mapped_column(JSONB)
    # Accounts (gitlab_user) of the assignees.
    assignee_account_ids: Mapped[list[int] | None] = mapped_column(JSONB)
    type: Mapped[str | None]  # from type::*: feature|bug|refactor|chore|docs|incident|automation
    complexity: Mapped[str | None]  # XS|S|M|L|XL from complexity::*
    complexity_points: Mapped[int | None]
    prod_severity: Mapped[str | None]  # P1|P2|P3 from prod-bug::*
    milestone: Mapped[str | None]
    due_date: Mapped[date | None] = mapped_column(Date)  # the issue's, else its milestone's
    web_url: Mapped[str | None]
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_estimate_s: Mapped[int | None]
    time_spent_s: Mapped[int | None]


class IssueMrLink(TimestampMixin, Base):
    __tablename__ = "issue_mr_links"
    __table_args__ = (CheckConstraint(f"source IN {LINK_SOURCES}", name="source"),)

    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True
    )
    mr_id: Mapped[int] = mapped_column(
        ForeignKey("merge_requests.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    source: Mapped[str]  # closes|mention|branch (the strongest seen)


class ProdDefect(TimestampMixin, Base):
    """A production bug (issue labelled prod-bug::P*) and its root-cause analysis (§4.4).
    Counted in B2 only once a lead confirmed the RCA."""

    __tablename__ = "prod_defects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), unique=True)
    severity: Mapped[str]  # P1|P2|P3
    # What the system found (MRs closing it, reverts, SHAs named): candidates, never a verdict.
    suggestions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    root_commit_sha: Mapped[str | None]
    root_mr_id: Mapped[int | None] = mapped_column(
        ForeignKey("merge_requests.id", ondelete="SET NULL")
    )
    rca_status: Mapped[str] = mapped_column(server_default="pending")  # pending|confirmed|process
    # [{"user_id": 3, "share": 0.5}, ...]: who carries it, and how much (sums to 1).
    responsible: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    process_gaps: Mapped[list[str] | None] = mapped_column(JSONB)  # missing_test, review_missed…
    rca_note: Mapped[str | None]
    rca_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    rca_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewComment(TimestampMixin, Base):
    """The label of a review comment (an MR note by someone else than the author, §4.5)."""

    __tablename__ = "review_comments"
    __table_args__ = (CheckConstraint(f"label IN {COMMENT_LABELS}", name="label"),)

    note_id: Mapped[int] = mapped_column(
        ForeignKey("mr_notes.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    label: Mapped[str]
    by: Mapped[str] = mapped_column(server_default="rule")  # llm|rule
    # The MR author pushed after it (system note), or a lead confirmed it on the scorecard.
    acted_on: Mapped[bool] = mapped_column(server_default="false")
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DpiRubric(TimestampMixin, Base):
    """A version of the rubric (§1): groups A-E, their metrics with `max`, `source`, label."""

    __tablename__ = "dpi_rubrics"

    version: Mapped[str] = mapped_column(primary_key=True)
    metrics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(server_default="false")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DpiProfile(TimestampMixin, Base):
    """Group weights (sum 100) and the A1 target of a level / role (§1)."""

    __tablename__ = "dpi_profiles"

    key: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str]
    weights: Mapped[dict[str, float]] = mapped_column(JSONB)  # {"A": 25, "B": 30, ...}
    a1_target: Mapped[float] = mapped_column(server_default="28")
    position: Mapped[int] = mapped_column(server_default="0")


class DpiUserProfile(TimestampMixin, Base):
    __tablename__ = "dpi_user_profiles"
    __table_args__ = (UniqueConstraint("user_id", "effective_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    profile: Mapped[str] = mapped_column(ForeignKey("dpi_profiles.key"))
    effective_from: Mapped[date] = mapped_column(Date)
    set_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DpiReview(TimestampMixin, Base):
    """One person's month in the cycle (§5)."""

    __tablename__ = "dpi_reviews"
    __table_args__ = (
        UniqueConstraint("user_id", "month"),
        CheckConstraint(f"status IN {DPI_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    month: Mapped[date] = mapped_column(Date, index=True)  # the 1st
    status: Mapped[str] = mapped_column(server_default="draft")
    profile: Mapped[str]
    leave_days: Mapped[Decimal] = mapped_column(Numeric(4, 1), server_default="0")
    # The lead's adjustment of a metric after a dispute, the calibration delta, the 1:1.
    adjustment: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="0")
    adjustment_reason: Mapped[str | None]
    adjusted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action_plan: Mapped[str | None]
    one_on_one_at: Mapped[date | None] = mapped_column(Date)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assessed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DpiAssessment(TimestampMixin, Base):
    """The lead's score of one manager metric (§3): score, comment, evidence links."""

    __tablename__ = "dpi_assessments"
    __table_args__ = (UniqueConstraint("review_id", "metric"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("dpi_reviews.id", ondelete="CASCADE"), index=True
    )
    metric: Mapped[str]  # A3|B5|D3|E1|E2|E3
    score: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    comment: Mapped[str]
    evidence: Mapped[list[str]] = mapped_column(JSONB)
    by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DpiMetricOverride(TimestampMixin, Base):
    """A metric value a dispute changed (§5): replaces the computed `v`, with the reason."""

    __tablename__ = "dpi_metric_overrides"
    __table_args__ = (UniqueConstraint("review_id", "metric"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("dpi_reviews.id", ondelete="CASCADE"), index=True
    )
    metric: Mapped[str]
    value: Mapped[Decimal] = mapped_column(Numeric(4, 3))  # v in [0, 1]
    reason: Mapped[str]
    by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DpiDispute(TimestampMixin, Base):
    __tablename__ = "dpi_disputes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("dpi_reviews.id", ondelete="CASCADE"), index=True
    )
    metric: Mapped[str]
    reason: Mapped[str]
    raised_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision: Mapped[str | None]  # accepted|rejected
    answer: Mapped[str | None]
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DpiPeerFeedback(TimestampMixin, Base):
    """Optional peer feedback for D3 (§3): anonymous to the person it is about."""

    __tablename__ = "dpi_peer_feedback"
    __table_args__ = (
        UniqueConstraint("month", "from_user_id", "to_user_id"),
        CheckConstraint("score BETWEEN 1 AND 5", name="score"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    month: Mapped[date] = mapped_column(Date)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    score: Mapped[int]
    comment: Mapped[str | None]


class DpiSnapshot(Base):
    """A computed scorecard (§6.1): kept for good, a recompute adds a new one."""

    __tablename__ = "dpi_snapshots"
    __table_args__ = (Index("ix_dpi_snapshots_user_id_month", "user_id", "month"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    month: Mapped[date] = mapped_column(Date)
    kind: Mapped[str]  # auto (computed) | final (at finalize)
    rubric_version: Mapped[str]
    profile: Mapped[str]
    inputs_hash: Mapped[str]
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    total: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    rating: Mapped[str | None]
    confidence: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
