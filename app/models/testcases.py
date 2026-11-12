"""Testcase (docs/specs/30, user 2026-11-12): the test cases of a dự án, kept as records rather
than a sheet, and the AI's work on them.

TestSuite: a group of cases (usually one tab of the team's Google Sheet), owned by its home dự án
and shareable with others (TestSuiteLink, like a document, docs/specs/29 §17). TestCase: one case,
with what a tester needs (preconditions, steps, data, expected result) and what links it to the
code (Jira keys, globs over the repo paths it covers, its automated test file).
TestCaseRevision: the case as it was before each change. TestAiRun: one AI job: writing cases
from sources (generate), reviewing a suite (audit), or the cases a merge request calls for
(impact); a person applies what it proposes.

The TestRail model (docs/specs/30 §13): TestSection, a tree of folders in a suite; cases in the
"steps" template (one expected result per step, shared steps inserted); TestSharedStep,
TestMilestone, TestConfigGroup / TestConfig; TestPlan groups TestRuns; a TestRunTest is one case in
one run, its TestResults the history of what testers recorded.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.models.base import Base, TimestampMixin

PRIORITIES = ("critical", "high", "medium", "low")
CASE_KINDS = ("manual", "automated")
CASE_STATUSES = ("draft", "review", "approved", "obsolete")  # §15 G7 adds "review"
CASE_SOURCES = ("manual", "import", "ai")
REVISION_VIA = ("editor", "import", "ai", "restore", "bulk")
TEMPLATES = ("text", "steps")
# TestRail's case types (docs/specs/30 §13.3).
CASE_TYPES = (
    "acceptance",
    "accessibility",
    "automated",
    "compatibility",
    "destructive",
    "functional",
    "other",
    "performance",
    "regression",
    "security",
    "smoke",
    "usability",
)
# Built-in statuses (§15 G6 adds "skipped"); a dự án may add its own (TestStatus), so the
# columns are checked by the application, not by a CHECK constraint.
TEST_STATUSES = ("untested", "passed", "failed", "blocked", "retest", "skipped")
RESULT_STATUSES = ("passed", "failed", "blocked", "retest", "skipped")
RUN_SOURCES = ("manual", "regression", "plan", "ci")
RESULT_SOURCES = ("manual", "ci")
CI_IMPORT_STATUSES = ("queued", "running", "done", "failed")
AI_KINDS = ("generate", "audit", "impact", "links", "duplicates")
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
    # §15 G2: code globs every case of the suite inherits (a case's own add to them).
    code_paths: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
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
        CheckConstraint(f"template IN {TEMPLATES}", name="template"),
        CheckConstraint(f"type IN {CASE_TYPES}", name="type"),
        Index(
            "uq_test_cases_suite_automation_id",
            "suite_id",
            "automation_id",
            unique=True,
            postgresql_where="automation_id IS NOT NULL",
        ),
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
    # The code of the sheet row it was imported from ("Mã gốc"); its own key is C<id>.
    code: Mapped[str | None]
    section_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_sections.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str]
    template: Mapped[str] = mapped_column(server_default="text", default="text")
    # Template "steps": [{"content", "expected"} | {"shared": <shared step id>}].
    steps_list: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default="[]", default=list
    )
    type: Mapped[str] = mapped_column(server_default="functional", default="functional")
    estimate_s: Mapped[int | None]
    milestone_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_milestones.id", ondelete="SET NULL"), index=True
    )
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
    # Globs over the dự án's repo paths the case covers (docs/specs/30 §8.1); "{repo}:{glob}" or
    # a bare glob (every repo of the dự án), §15 G2.
    code_paths: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    # §15 G1: the automated test's identity in CI reports ("classname.name"), unique in a suite.
    automation_id: Mapped[str | None]
    # §15 G1: CI results disagree (pass and fail on one commit, or flipping often).
    flaky: Mapped[bool] = mapped_column(server_default="false", default=False)
    # §15 G5: values of the dự án's custom fields (TestField, entity "case"), {key: value}.
    custom: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
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


class TestSection(TimestampMixin, Base):
    """A folder of a suite; `parent_id` makes the tree (docs/specs/30 §13.2)."""

    __tablename__ = "test_sections"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True)
    suite_id: Mapped[int] = mapped_column(
        ForeignKey("test_suites.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_sections.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    description: Mapped[str | None]
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    # §15 G2: code globs the cases of this folder (and its sub-folders) inherit.
    code_paths: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)


class TestSharedStep(TimestampMixin, Base):
    """Steps several cases insert (docs/specs/30 §13.4): [{"content", "expected"}]."""

    __tablename__ = "test_shared_steps"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]", default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TestMilestone(TimestampMixin, Base):
    __tablename__ = "test_milestones"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    description: Mapped[str | None]
    start_on: Mapped[date | None] = mapped_column(Date)
    due_on: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TestConfigGroup(TimestampMixin, Base):
    """A kind of configuration (Trình duyệt, Nền tảng…) a plan multiplies its runs by."""

    __tablename__ = "test_config_groups"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    position: Mapped[int] = mapped_column(server_default="0", default=0)


class TestConfig(TimestampMixin, Base):
    __tablename__ = "test_configs"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("test_config_groups.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    position: Mapped[int] = mapped_column(server_default="0", default=0)


class TestPlan(TimestampMixin, Base):
    __tablename__ = "test_plans"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    description: Mapped[str | None]
    milestone_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_milestones.id", ondelete="SET NULL"), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TestRun(TimestampMixin, Base):
    __tablename__ = "test_runs"
    __test__ = False
    __table_args__ = (CheckConstraint(f"source IN {RUN_SOURCES}", name="source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_plans.id", ondelete="CASCADE"), index=True
    )
    milestone_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_milestones.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str]
    description: Mapped[str | None]
    # The configurations of this run (a plan's combination), and their names shown.
    config_ids: Mapped[list[int]] = mapped_column(JSONB, server_default="[]", default=list)
    config: Mapped[str | None]
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    refs: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    source: Mapped[str] = mapped_column(server_default="manual", default="manual")
    # How the cases were chosen (suite, sections, filters; the MRs of a regression list).
    source_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TestRunTest(Base):
    """One case in one run. While the run is open it shows the case as it is now; closing the
    run keeps `snapshot` (also kept if the case is deleted)."""

    __tablename__ = "test_run_tests"
    __test__ = False
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_test_run_tests_run_case"),
        Index("ix_test_run_tests_run_id_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"))
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str]
    section: Mapped[str | None]  # the section path when added / closed
    priority: Mapped[str]
    type: Mapped[str]
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    status: Mapped[str] = mapped_column(server_default="untested", default="untested")
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestResult(Base):
    __tablename__ = "test_results"
    __test__ = False
    __table_args__ = (CheckConstraint(f"source IN {RESULT_SOURCES}", name="source"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    test_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("test_run_tests.id", ondelete="CASCADE"), index=True
    )
    # None: a comment or a reassignment only.
    status: Mapped[str | None]
    comment: Mapped[str | None]
    version: Mapped[str | None]
    elapsed_s: Mapped[int | None]
    defects: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    # Template "steps": [{"status", "actual"}] in the order of the steps.
    step_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default="[]", default=list
    )
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # §15 G1: "manual" (a tester) or "ci" (a report uploaded from CI) with what CI said.
    source: Mapped[str] = mapped_column(server_default="manual", default="manual")
    log: Mapped[str | None]  # the failure message / output
    stack: Mapped[str | None]  # the stack trace
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default="[]", default=list
    )
    # {commit_sha, branch, mr_iid, test_name, file, import_id}
    ci: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    # §15 G6: the first step that failed (1-based), for template "steps".
    failed_step: Mapped[int | None]
    # §15 G5: values of the dự án's custom fields (TestField, entity "result"), {key: value}.
    custom: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestCiImport(Base):
    """One CI report uploaded (§15 G1): parsed in the background into a run's results. The file
    is kept until it is processed."""

    __tablename__ = "test_ci_imports"
    __test__ = False
    __table_args__ = (CheckConstraint(f"status IN {CI_IMPORT_STATUSES}", name="status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    suite_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_suites.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(server_default="queued", default="queued")
    format: Mapped[str | None]  # junit | testng | xunit | playwright | cucumber
    file_name: Mapped[str | None]
    # The parameters it was sent with (run_name, milestone_id, config_ids, close_run, mr_iid,
    # commit_sha, branch, auto_create).
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    total: Mapped[int] = mapped_column(server_default="0", default=0)
    done: Mapped[int] = mapped_column(server_default="0", default=0)  # progress
    mapped: Mapped[int] = mapped_column(server_default="0", default=0)
    created_cases: Mapped[int] = mapped_column(server_default="0", default=0)
    unmapped: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    error: Mapped[str | None]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TestProjectSettings(TimestampMixin, Base):
    """A dự án's testing settings (§15): who may record (G6 "chỉ người được giao"), the
    approval rule (G7), where bugs go in Jira (G3)."""

    __tablename__ = "test_project_settings"
    __test__ = False

    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_only: Mapped[bool] = mapped_column(server_default="false", default=False)
    require_approval: Mapped[bool] = mapped_column(server_default="false", default=False)
    jira_project_key: Mapped[str | None]
    jira_issue_type: Mapped[str] = mapped_column(server_default="Bug", default="Bug")
    jira_components: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    jira_labels: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TestStatus(TimestampMixin, Base):
    """A result status a dự án adds to the built-in ones (§15 G6)."""

    __tablename__ = "test_statuses"
    __test__ = False
    __table_args__ = (
        UniqueConstraint("business_project_id", "key", name="uq_test_statuses_project_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str]  # lowercase slug, never one of TEST_STATUSES
    name: Mapped[str]
    color: Mapped[str]  # a token name: good, bad, warn, info, muted, primary
    done: Mapped[bool] = mapped_column(server_default="true", default=True)  # counts as tested
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TestAttachment(Base):
    """A file or picture attached to a result (§15 G6), kept once per dự án and content."""

    __tablename__ = "test_attachments"
    __test__ = False
    __table_args__ = (
        UniqueConstraint("business_project_id", "sha256", name="uq_test_attachments_project_sha"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    mime: Mapped[str]
    size: Mapped[int]
    sha256: Mapped[str]
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestReportSchedule(TimestampMixin, Base):
    """A report sent on a schedule (§15 G4) to a webhook (chat / Telegram connector)."""

    __tablename__ = "test_report_schedules"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    kind: Mapped[str]  # run_summary | release_readiness | coverage | defects | automation | ...
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", default=dict)
    cadence: Mapped[str] = mapped_column(server_default="weekly", default="weekly")  # daily|weekly
    weekday: Mapped[int | None]  # 0 = Monday, for weekly
    hour: Mapped[int] = mapped_column(server_default="8", default=8)  # local time (REPORT_TZ)
    webhook_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_webhooks.id", ondelete="SET NULL")
    )
    enabled: Mapped[bool] = mapped_column(server_default="true", default=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


FIELD_ENTITIES = ("case", "result")
FIELD_TYPES = (
    "text",
    "textarea",
    "number",
    "date",
    "dropdown",
    "multiselect",
    "checkbox",
    "user",
    "url",
)
REVIEW_DECISIONS = ("submit", "approve", "reject")
BULK_KINDS = ("edit", "move", "copy", "replace", "duplicates")


class TestField(TimestampMixin, Base):
    """A custom field of a dự án's cases or results (docs/specs/30 §15.8, G5). Archived, not
    deleted: the values stay in `custom`."""

    __tablename__ = "test_fields"
    __test__ = False
    __table_args__ = (
        UniqueConstraint(
            "business_project_id", "entity", "key", name="uq_test_fields_bp_entity_key"
        ),
        CheckConstraint(f"entity IN {FIELD_ENTITIES}", name="entity"),
        CheckConstraint(f"type IN {FIELD_TYPES}", name="type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_project_id: Mapped[int] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    entity: Mapped[str]
    key: Mapped[str]  # slug from the name, fixed once made
    name: Mapped[str]
    type: Mapped[str]
    options: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    required: Mapped[bool] = mapped_column(server_default="false", default=False)
    default: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # Empty: every suite / template of the dự án.
    suite_ids: Mapped[list[int]] = mapped_column(JSONB, server_default="[]", default=list)
    templates: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", default=list)
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class TestCaseReview(Base):
    """One step of a case's approval (§15.9, G7): sent for review, approved or rejected."""

    __tablename__ = "test_case_reviews"
    __test__ = False
    __table_args__ = (CheckConstraint(f"decision IN {REVIEW_DECISIONS}", name="decision"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str]
    comment: Mapped[str | None]
    revision: Mapped[int]  # the case's revision it was about
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestBulkOp(Base):
    """One bulk change of cases (§15.10, G10), kept to undo it: the revision each case had
    before, and the cases it made (copy)."""

    __tablename__ = "test_bulk_ops"
    __test__ = False
    __table_args__ = (CheckConstraint(f"kind IN {BULK_KINDS}", name="kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str]
    summary: Mapped[str] = mapped_column(server_default="", default="")
    # {case id: the revision number it had before} for changed cases.
    before: Mapped[dict[str, int]] = mapped_column(JSONB, server_default="{}", default=dict)
    created_ids: Mapped[list[int]] = mapped_column(JSONB, server_default="[]", default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
