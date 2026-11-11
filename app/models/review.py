from datetime import UTC, datetime
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
        Index("ix_reviews_llm_retry", "id", postgresql_where=text("llm_retry")),
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
    # Which Claude account answered (its label then, app/review/llm_accounts.py); null before
    # 2026-11-09 or with a single credential from the environment.
    llm_account: Mapped[str | None]
    rubric_version: Mapped[str | None]
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    error: Mapped[str | None]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the reviewed change was made: the commit's time, or when the MR was opened
    # (CHANGE_TIME_UNKNOWN when neither is known). Every period and report counts by it.
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("'1970-01-01 00:00:00+00'")
    )

    @hybrid_property
    def counted_at(self) -> datetime | None:
        """The moment that puts the review in a period for every score, card and report: when
        the change was made, the commit's time or the MR's opening (user, 2026-11-04: "time là
        time commit hoặc time MR; time review chỉ là thông tin"), however long before. The
        review's time is used only when that time is unknown, or lies after the review (a clock
        off or a forged date: a change cannot be reviewed before it exists)."""
        changed, finished = self.changed_at, self.finished_at
        if changed is None or changed == CHANGE_TIME_UNKNOWN:
            return finished
        if finished is None:
            return changed  # not finished yet: counts nowhere, only the list shows it
        return changed if changed <= finished else finished

    @counted_at.inplace.expression
    @classmethod
    def _counted_at_expression(cls) -> ColumnElement[datetime | None]:
        return case(
            (cls.changed_at == CHANGE_TIME_UNKNOWN, cls.finished_at),
            (cls.finished_at.is_(None), cls.changed_at),
            (cls.changed_at <= cls.finished_at, cls.changed_at),
            else_=cls.finished_at,
        )

    # Failed because the LLM was down, waiting to be reviewed again once it works (app/review/
    # llm_recovery.py); cleared when the resume job claims it.
    llm_retry: Mapped[bool] = mapped_column(server_default="false", default=False)
    # docs/specs/17: merge verdict (approve|fix_required|block) and risk score from the
    # findings; the model's design & intent notes; what asked for the review and who.
    verdict: Mapped[str | None]
    risk_score: Mapped[int | None]
    intent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trigger: Mapped[str] = mapped_column(
        server_default="webhook"
    )  # webhook|dashboard|telegram|backfill|rerun|mcp
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
    # docs/specs/22 §4: the component the model placed it in, per criteria set: {set_id: key}.
    criteria: Mapped[dict[str, str] | None] = mapped_column(JSONB)


class FindingDispute(TimestampMixin, Base):
    __tablename__ = "finding_disputes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    finding_id: Mapped[int | None] = mapped_column(ForeignKey("review_findings.id"), index=True)
    raised_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None]
    decision: Mapped[str | None]
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # An AI second look the lead asked for before deciding (app/review/dispute_check.py).
    ai_status: Mapped[str | None]  # queued|running|done|failed
    ai_verdict: Mapped[str | None]  # finding_holds|dispute_holds|unclear
    ai_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    ai_reason: Mapped[str | None]
    ai_evidence: Mapped[str | None]  # "path:12-20"
    ai_model: Mapped[str | None]
    ai_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    ai_requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ai_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


CLOSURE_KINDS = ("auto", "request", "admin", "reopen")
# A finding no longer to fix (its closing outcome).
FINDING_CLOSED = ("fixed", "accepted_risk", "false_positive")
# Why the check dropped a request: the finding was closed another way before it ran.
ALREADY_CLOSED = "Finding đã được đóng trước khi kiểm"


def shown_status(closure_status: str, finding_status: str | None) -> str:
    """What a row of "Đóng finding" shows (user 2026-11-06: the list and the finding's page
    must agree): a closed finding shows "closed" whatever became of this request; a request
    that closed it, the finding open again since, shows "reopened"; otherwise the request's
    own status."""
    if finding_status in FINDING_CLOSED:
        return "closed"
    if closure_status == "closed":
        return "reopened"
    return closure_status


CLOSURE_STATUSES = (
    "pending",
    "checking",
    "proposed",
    "unmet",
    "closed",
    "rejected",
    "failed",
    "cancelled",
    "reopened",
)
# A request in flight: waiting for a manager/admin, being checked, its proposal waiting, or the
# check found it not good enough and the manager/admin has not weighed that yet (`unmet`).
CLOSURE_OPEN = ("pending", "checking", "proposed", "unmet")


class FindingClosure(TimestampMixin, Base):
    """A critical/high finding closed because a later change fixed it (outside the spec,
    requested by the user 2026-10-31; see PROGRESS.md and app/review/resolve.py).

    Closing only moves the finding to `fixed`: its severity stays and it still counts in the
    review's score and verdict (only false_positive and rejected do not). One row per attempt:
    - kind "auto": a later review's change, checked against the repo's open findings of the last
      AUTO_CLOSE_DAYS days, fixed it (only closings are stored; `review_id` is that review);
    - kind "request": the author named the commits that fix it (`pending`); a manager of their
      team or an admin either refuses it (`rejected`, note) or runs the check (`checking`, then
      `closed`, `failed`, or `unmet` with the reason: the manager/admin then closes it anyway
      with a note, runs it again, or confirms the refusal, `rejected`);
    - kind "admin": closed by hand, the note is mandatory; kind "reopen": opened again (note).
    """

    __tablename__ = "finding_closures"
    __table_args__ = (
        CheckConstraint("kind IN ('auto','request','admin','reopen')", name="kind"),
        CheckConstraint(
            "status IN ('pending','checking','proposed','unmet','closed','rejected','failed',"
            "'cancelled','reopened')",
            name="status",
        ),
        CheckConstraint(
            "closed_as IN ('fixed','accepted_risk','false_positive')", name="closed_as"
        ),
        CheckConstraint("via IN ('web','mcp')", name="via"),
        Index("ix_finding_closures_created_at", "created_at"),
        # One request in flight per finding: a double click must not pay twice.
        Index(
            "uq_finding_closures_pending",
            "finding_id",
            unique=True,
            postgresql_where=text("status IN ('pending','checking','proposed','unmet')"),
            sqlite_where=text("status IN ('pending','checking','proposed','unmet')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    finding_id: Mapped[int] = mapped_column(
        ForeignKey("review_findings.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str]
    status: Mapped[str]
    shas: Mapped[list[str]] = mapped_column(JSONB, default=list)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id", ondelete="SET NULL"))
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Who ran the check or refused the request (a manager of the author's team, or an admin).
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None]
    # The author's case that the finding does not hold (instead of, or with, the commits).
    argument: Mapped[str | None]
    # The finding's status this closing set: fixed (a change fixed it), accepted_risk (the
    # argument held: lowered to severity_after) or false_positive (the finding was wrong);
    # None while not closed.
    closed_as: Mapped[str | None]
    reason: Mapped[str | None]  # the checker's reason, or why the request was refused
    evidence: Mapped[str | None]  # file:lines of the fix in the change
    # Closing by an argument or a note (no commit) lowers the finding (user request
    # 2026-11-03): its severity before and after, and what the check suggested (medium|low).
    severity_before: Mapped[str | None]
    severity_after: Mapped[str | None]
    suggested_severity: Mapped[str | None]
    # Every check of this request (a re-run adds); billed by the `finding.close_request.cost`
    # audit rows, one per call, in the month it was made.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The "Chạy review" being run: only the worker carrying this token claims and decides it.
    run_token: Mapped[str | None]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Where a request came from (docs/specs/26 §8): the dashboard form ("web") or Claude Code
    # through the MCP server ("mcp"); `meta` holds what the MCP client sent with it:
    # {files_changed, tests, token_id, token_name, client}.
    via: Mapped[str] = mapped_column(server_default="web", default="web")
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class LlmHealth(TimestampMixin, Base):
    """One row (id 1): is the LLM working? ok | down | recovering (app/review/llm_recovery.py)."""

    __tablename__ = "llm_health"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(server_default="ok")
    down_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    probes: Mapped[int] = mapped_column(server_default="0", default=0)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requeued_last: Mapped[int | None]
