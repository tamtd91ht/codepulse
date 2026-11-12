"""Testcase (docs/specs/30, user 2026-11-12): the test cases of a dự án, kept as records rather
than a sheet, and the AI's work on them.

TestSuite: a group of cases (usually one tab of the team's Google Sheet), owned by its home dự án
and shareable with others (TestSuiteLink, like a document, docs/specs/29 §17). TestCase: one case,
with what a tester needs (preconditions, steps, data, expected result) and what links it to the
code (Jira keys, globs over the repo paths it covers, its automated test file).
TestCaseRevision: the case as it was before each change. TestAiRun: one AI job: writing cases
from sources (generate), reviewing a suite (audit), or the cases a merge request calls for
(impact); a person applies what it proposes.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

PRIORITIES = ("critical", "high", "medium", "low")
CASE_KINDS = ("manual", "automated")
CASE_STATUSES = ("draft", "approved", "obsolete")
CASE_SOURCES = ("manual", "import", "ai")
REVISION_VIA = ("editor", "import", "ai", "restore", "bulk")
AI_KINDS = ("generate", "audit", "impact")
AI_STATUSES = ("queued", "fetching", "writing", "ready", "failed", "applied", "discarded")


class TestSuite(TimestampMixin, Base):
    __tablename__ = "test_suites"
    __test__ = False  # not a pytest class

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    description: Mapped[str | None]
    # Where it was imported from: the Google Sheet's link and the tab ("Nhập lại").
    source_url: Mapped[str | None]
    source_sheet: Mapped[str | None]
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TestSuiteLink(Base):
    """A suite shared with one more dự án (its home is the suite's own business_project_id)."""

    __tablename__ = "test_suite_links"
    __test__ = False

    suite_id: Mapped[int] = mapped_column(
        ForeignKey("test_suites.id", ondelete="CASCADE"), primary_key=True
    )
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    linked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestCase(TimestampMixin, Base):
    __tablename__ = "test_cases"
    __test__ = False
    __table_args__ = (
        UniqueConstraint("suite_id", "code", name="uq_test_cases_suite_code"),
        CheckConstraint(f"priority IN {PRIORITIES}", name="priority"),
        CheckConstraint(f"kind IN {CASE_KINDS}", name="kind"),
        CheckConstraint(f"status IN {CASE_STATUSES}", name="status"),
        CheckConstraint(f"source IN {CASE_SOURCES}", name="source"),
        Index(
            "ix_test_cases_jira_keys",
            "jira_keys",
            postgresql_using="gin",
            postgresql_ops={"jira_keys": "jsonb_path_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    suite_id: Mapped[int] = mapped_column(
        ForeignKey("test_suites.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str]
    section: Mapped[str | None]  # chức năng / màn hình
    title: Mapped[str]
    preconditions: Mapped[str | None]
    steps: Mapped[str | None]
    test_data: Mapped[str | None]
    expected: Mapped[str | None]
    notes: Mapped[str | None]
    priority: Mapped[str] = mapped_column(server_default="medium", default="medium")
    kind: Mapped[str] = mapped_column(server_default="manual", default="manual")
    # The automated test: a path in the repo, optionally "::test name".
    automation: Mapped[str | None]
    tags: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    jira_keys: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    # Globs over the dự án's repo paths the case covers (docs/specs/30 §8.1).
    code_paths: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    status: Mapped[str] = mapped_column(server_default="draft", default="draft")
    source: Mapped[str] = mapped_column(server_default="manual", default="manual")
    revision: Mapped[int] = mapped_column(server_default="1", default=1)
    # The accent-folded text searched (code, section, title, steps, expected, tags, Jira).
    search: Mapped[str] = mapped_column(String, server_default="", default="")
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TestCaseRevision(Base):
    """A case as it was before one change (number = the revision it had then)."""

    __tablename__ = "test_case_revisions"
    __test__ = False
    __table_args__ = (
        UniqueConstraint("case_id", "number", name="uq_test_case_revisions_case_number"),
        CheckConstraint(f"via IN {REVISION_VIA}", name="via"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int]
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    via: Mapped[str]
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestAiRun(Base):
    """One AI job on test cases (docs/specs/30 §3.3): its input, what it read, what it proposes.
    `requested_at` is the run token: a stale run asked again cannot overwrite the new one."""

    __tablename__ = "test_ai_runs"
    __test__ = False
    __table_args__ = (
        CheckConstraint(f"kind IN {AI_KINDS}", name="kind"),
        CheckConstraint(f"status IN {AI_STATUSES}", name="status"),
        Index("ix_test_ai_runs_created_by_status", "created_by", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    suite_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_suites.id", ondelete="CASCADE"), index=True
    )
    merge_request_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("merge_requests.id", ondelete="CASCADE"), index=True
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(server_default="queued")
    error: Mapped[str | None]
    model: Mapped[str | None]
    cost_usd: Mapped[float | None] = mapped_column(Float)
    applied_count: Mapped[int | None]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
