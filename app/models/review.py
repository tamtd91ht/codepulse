from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    and_,
    case,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from app.models.base import Base, TimestampMixin

SEVERITIES = ("critical", "high", "medium", "low", "info")
# reviews.changed_at of a change whose time is not known: sorts last.
CHANGE_TIME_UNKNOWN = datetime(1970, 1, 1, tzinfo=UTC)
# A commit's time is set by its author (`git commit --date`): it places a review in a period only
# when it lies at most this long before the review finished (user, 2026-10-29).
COMMIT_TIME_MAX_LAG = timedelta(days=30)


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
            "round",
            name="uq_reviews_target",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_reviews_author_user_id_finished_at", "author_user_id", "finished_at"),
        # Read backwards for "newest change first" and its (changed_at, id) cursor.
        Index("ix_reviews_changed_at_id", "changed_at", "id"),
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
    # When the reviewed change was made: the commit's time, or when the MR was opened
    # (CHANGE_TIME_UNKNOWN when neither is known). "Kết quả review" is sorted by it.
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("'1970-01-01 00:00:00+00'")
    )

    @hybrid_property
    def counted_at(self) -> datetime | None:
        """The moment that puts the review in a period for scores, cards and reports (user,
        2026-10-29): when the change was made, if that is known and lies within
        COMMIT_TIME_MAX_LAG before the review finished; otherwise when the review finished."""
        changed, finished = self.changed_at, self.finished_at
        if changed is None or changed == CHANGE_TIME_UNKNOWN:
            return finished
        if finished is None:
            return changed  # not finished yet: counts nowhere, only the list shows it
        return changed if finished - COMMIT_TIME_MAX_LAG <= changed <= finished else finished

    @counted_at.inplace.expression
    @classmethod
    def _counted_at_expression(cls) -> ColumnElement[datetime | None]:
        return case(
            (cls.changed_at == CHANGE_TIME_UNKNOWN, cls.finished_at),
            (cls.finished_at.is_(None), cls.changed_at),
            (
                and_(
                    cls.changed_at >= cls.finished_at - COMMIT_TIME_MAX_LAG,
                    cls.changed_at <= cls.finished_at,
                ),
                cls.changed_at,
            ),
            else_=cls.finished_at,
        )

    # docs/specs/17: merge verdict (approve|fix_required|block) and risk score from the
    # findings; the model's design & intent notes; what asked for the review and who.
    verdict: Mapped[str | None]
    risk_score: Mapped[int | None]
    intent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trigger: Mapped[str] = mapped_column(
        server_default="webhook"
    )  # webhook|dashboard|telegram|backfill|rerun
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # docs/specs/19 §4: agent = the main pass ran with the repository open; fast = the no-tool
    # pass (fallback, or below REVIEW_AGENT_MIN_LOC). Before spec 19: fast | deep.
    mode: Mapped[str] = mapped_column(server_default="fast")
    # Report of the agentic pass and of the verification step (key "verification"), or why
    # they did not run ("fallback", "error", "skipped").
    deep: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # docs/specs/17 §Căn cứ review: rubric, team rules (name/path + hash), repo instructions,
    # agents and skills the deep pass read. Names and hashes only, never the text.
    basis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # docs/specs/17 §Lịch sử: a deliberate "review lại" of the same sha is round N+1; the
    # earlier round stays (status superseded, superseded_by -> the new row).
    round: Mapped[int] = mapped_column(server_default="1")
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("reviews.id", ondelete="SET NULL")
    )


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
    # open|fixed|disputed|accepted_risk|false_positive|rejected (refuted by the verification)
    status: Mapped[str | None] = mapped_column(server_default="open")
    # docs/specs/19 §4.3: what the independent verification said and its evidence.
    verified: Mapped[str] = mapped_column(
        server_default="skipped"
    )  # confirmed|downgraded|rejected|unverified|skipped
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class FindingDispute(TimestampMixin, Base):
    __tablename__ = "finding_disputes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    finding_id: Mapped[int | None] = mapped_column(ForeignKey("review_findings.id"), index=True)
    raised_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None]
    decision: Mapped[str | None]
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
